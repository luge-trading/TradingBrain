"""Send scheduled review notifications through SMTP without persisting secrets."""
from __future__ import annotations

import smtplib
import ssl
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Mapping, Sequence

import certifi


SECURITY_PATH = "/usr/bin/security"


class EmailConfigurationError(ValueError):
    """Raised when the non-sensitive email configuration is invalid."""


class EmailDeliveryError(RuntimeError):
    """Raised when credentials or SMTP delivery are unavailable."""


@dataclass(frozen=True)
class EmailConfig:
    enabled: bool
    host: str
    port: int
    timeout_seconds: float
    sender: str
    recipients: tuple[str, ...]
    subject_prefix: str
    attach_summary: bool
    keychain_service: str


@dataclass(frozen=True)
class EmailSendResult:
    attempted: bool
    sent: bool
    recipients: tuple[str, ...]
    subject: str | None
    attachment: Path | None
    error: str | None


def _required_table(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name)
    if not isinstance(value, dict):
        raise EmailConfigurationError(f"Missing or invalid [{name}] table")
    return value


def _required_text(data: Mapping[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise EmailConfigurationError(f"Missing or invalid {name}")
    return value.strip()


def load_email_config(path: str | Path) -> EmailConfig:
    config_path = Path(path)
    try:
        with config_path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise EmailConfigurationError(f"Unable to load email config: {config_path}") from exc

    if data.get("version") != 1:
        raise EmailConfigurationError("Unsupported or missing email config version")
    if not isinstance(data.get("enabled"), bool):
        raise EmailConfigurationError("Missing or invalid enabled flag")

    smtp = _required_table(data, "smtp")
    message = _required_table(data, "message")
    keychain = _required_table(data, "keychain")
    host = _required_text(smtp, "host")
    port = smtp.get("port")
    timeout = smtp.get("timeout_seconds")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise EmailConfigurationError("SMTP port must be an integer from 1 to 65535")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise EmailConfigurationError("SMTP timeout_seconds must be positive")

    sender = _required_text(message, "sender")
    raw_recipients = message.get("recipients")
    if not isinstance(raw_recipients, list) or not raw_recipients:
        raise EmailConfigurationError("At least one recipient is required")
    if any(not isinstance(item, str) or not item.strip() for item in raw_recipients):
        raise EmailConfigurationError("Every recipient must be a non-empty string")
    subject_prefix = _required_text(message, "subject_prefix")
    attach_summary = message.get("attach_summary")
    if not isinstance(attach_summary, bool):
        raise EmailConfigurationError("Missing or invalid attach_summary flag")

    return EmailConfig(
        enabled=data["enabled"], host=host, port=port, timeout_seconds=float(timeout),
        sender=sender, recipients=tuple(item.strip() for item in raw_recipients),
        subject_prefix=subject_prefix, attach_summary=attach_summary,
        keychain_service=_required_text(keychain, "service"),
    )


def _read_keychain_password(config: EmailConfig) -> str:
    try:
        completed = subprocess.run(
            [SECURITY_PATH, "find-generic-password", "-a", config.sender,
             "-s", config.keychain_service, "-w"],
            capture_output=True, text=True, check=True,
            timeout=config.timeout_seconds,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return_code = getattr(exc, "returncode", None)
        detail = f" (return code {return_code})" if return_code is not None else ""
        raise EmailDeliveryError(
            f"Keychain credential unavailable for sender={config.sender} "
            f"service={config.keychain_service}{detail}"
        ) from exc
    password = completed.stdout.rstrip("\r\n")
    if not password:
        raise EmailDeliveryError(
            f"Keychain credential unavailable for sender={config.sender} service={config.keychain_service}"
        )
    return password


def _subject(config: EmailConfig, market_date: date, status: str) -> str:
    marker = ""
    if status == "completed_with_errors":
        marker = "[部分失败]"
    elif status == "failed":
        marker = "[运行失败]"
    return f"{config.subject_prefix}{marker} {market_date.isoformat()} A股每日复盘"


def _body(
    *, market_date: date, status: str, started_at: datetime, finished_at: datetime,
    details: Mapping[str, Any], summary_path: Path | None,
) -> str:
    symbols: Sequence[str] = details.get("symbols", ())
    symbol_text = ", ".join(str(item) for item in symbols) or "无"
    high_risk = details.get("high_risk_stocks")
    high_risk_text = ", ".join(str(item) for item in high_risk) if high_risk else "请查看汇总报告"
    return "\n".join([
        "TradingBrain A股每日复盘", "",
        f"市场日期：{market_date.isoformat()}", f"运行状态：{status}",
        f"开始时间：{started_at.isoformat()}", f"结束时间：{finished_at.isoformat()}",
        f"成功股票数：{details.get('success_count', '不可用，请查看汇总报告')}",
        f"失败股票数：{details.get('failure_count', '不可用，请查看汇总报告')}",
        f"高风险股票：{high_risk_text}", f"股票代码：{symbol_text}",
        f"汇总报告路径：{summary_path if summary_path else '无'}", "",
        "免责声明：本邮件仅用于研究和复盘，不构成任何投资建议。",
    ])


def send_review_email(
    config_path: str | Path, *, market_date: date, status: str,
    started_at: datetime, finished_at: datetime,
    details: Mapping[str, Any] | None = None, summary_path: str | Path | None = None,
    subject_override: str | None = None, body_override: str | None = None,
) -> EmailSendResult:
    config = load_email_config(config_path)
    if not config.enabled:
        return EmailSendResult(False, False, config.recipients, None, None, "Email notifications disabled")

    subject = subject_override or _subject(config, market_date, status)
    attachment = Path(summary_path) if summary_path is not None else None
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message["Subject"] = subject
    message.set_content(body_override or _body(
        market_date=market_date, status=status, started_at=started_at,
        finished_at=finished_at, details=details or {}, summary_path=attachment,
    ))
    attached = None
    if config.attach_summary and attachment is not None and attachment.is_file():
        message.add_attachment(
            attachment.read_text(encoding="utf-8"), subtype="markdown",
            filename=attachment.name,
        )
        attached = attachment

    try:
        app_password = _read_keychain_password(config)
        context = ssl.create_default_context(cafile=certifi.where())
        with smtplib.SMTP_SSL(
            config.host, config.port, context=context, timeout=config.timeout_seconds,
        ) as smtp:
            smtp.login(config.sender, app_password)
            smtp.send_message(message)
    except EmailDeliveryError:
        raise
    except Exception as exc:
        raise EmailDeliveryError(
            f"Email delivery failed for host={config.host} sender={config.sender}"
        ) from exc
    finally:
        if "app_password" in locals():
            app_password = ""

    return EmailSendResult(True, True, config.recipients, subject, attached, None)
