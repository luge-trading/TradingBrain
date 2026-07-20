from datetime import date, datetime, timezone
from pathlib import Path
import subprocess

import pytest

from src.notification import email_sender as sender


def write_config(path: Path, *, enabled=True, recipients='["to@example.com"]', port=465, timeout=20):
    path.write_text(f'''version = 1
enabled = {str(enabled).lower()}
[smtp]
host = "smtp.example.com"
port = {port}
timeout_seconds = {timeout}
[message]
sender = "from@example.com"
recipients = {recipients}
subject_prefix = "[TradingBrain]"
attach_summary = true
[keychain]
service = "com.example.smtp"
''', encoding="utf-8")
    return path


def test_load_valid_config_and_disabled_flag(tmp_path):
    config = sender.load_email_config(write_config(tmp_path / "email.toml", enabled=False))
    assert not config.enabled
    assert (config.host, config.port, config.timeout_seconds) == ("smtp.example.com", 465, 20.0)
    assert config.sender == "from@example.com"
    assert config.recipients == ("to@example.com",)


@pytest.mark.parametrize("content", [
    "enabled = true", "version = 1\nenabled = true", "version = 1\nenabled = true\n[smtp]",
])
def test_missing_required_config_is_rejected(tmp_path, content):
    path = tmp_path / "email.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(sender.EmailConfigurationError):
        sender.load_email_config(path)


def test_empty_recipient_is_rejected(tmp_path):
    with pytest.raises(sender.EmailConfigurationError, match="recipient"):
        sender.load_email_config(write_config(tmp_path / "email.toml", recipients="[]"))


@pytest.mark.parametrize("port,timeout", [(0, 20), (70000, 20), (465, 0), (465, -1)])
def test_port_and_timeout_are_validated(tmp_path, port, timeout):
    with pytest.raises(sender.EmailConfigurationError):
        sender.load_email_config(write_config(tmp_path / "email.toml", port=port, timeout=timeout))


def test_keychain_command_and_newline_stripping(monkeypatch, tmp_path):
    config = sender.load_email_config(write_config(tmp_path / "email.toml"))
    observed = {}

    def fake_run(command, **kwargs):
        observed.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="memory-only-value\n", stderr="")

    monkeypatch.setattr(sender.subprocess, "run", fake_run)
    assert sender._read_keychain_password(config) == "memory-only-value"
    assert observed["command"] == [
        "/usr/bin/security", "find-generic-password", "-a", "from@example.com",
        "-s", "com.example.smtp", "-w",
    ]
    assert observed["kwargs"]["capture_output"] is True
    assert observed["kwargs"]["check"] is True


def test_keychain_failure_does_not_expose_output(monkeypatch, tmp_path):
    config = sender.load_email_config(write_config(tmp_path / "email.toml"))
    failure = subprocess.CalledProcessError(44, ["security"], output="do-not-disclose")
    monkeypatch.setattr(sender.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(failure))
    with pytest.raises(sender.EmailDeliveryError) as caught:
        sender._read_keychain_password(config)
    assert "do-not-disclose" not in str(caught.value)
    assert "return code 44" in str(caught.value)


class FakeSMTP:
    instance = None

    def __init__(self, host, port, *, context, timeout):
        self.host, self.port, self.context, self.timeout = host, port, context, timeout
        self.login_args = None
        self.message = None
        FakeSMTP.instance = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, sender_address, password):
        self.login_args = (sender_address, password)

    def send_message(self, message):
        self.message = message


def send(monkeypatch, tmp_path, *, status="completed", summary=True):
    config_path = write_config(tmp_path / "email.toml")
    summary_path = tmp_path / "2026-07-20-daily-summary.md"
    if summary:
        summary_path.write_text("# summary", encoding="utf-8")
    monkeypatch.setattr(sender, "_read_keychain_password", lambda config: "memory-only-value")
    monkeypatch.setattr(sender.smtplib, "SMTP_SSL", FakeSMTP)
    now = datetime(2026, 7, 20, 16, tzinfo=timezone.utc)
    result = sender.send_review_email(
        config_path, market_date=date(2026, 7, 20), status=status,
        started_at=now, finished_at=now, details={"symbols": ["600000"], "success_count": 1},
        summary_path=summary_path,
    )
    return result, FakeSMTP.instance


def test_smtp_delivery_message_and_attachment(monkeypatch, tmp_path):
    result, smtp = send(monkeypatch, tmp_path)
    assert (smtp.host, smtp.port, smtp.timeout) == ("smtp.example.com", 465, 20.0)
    assert smtp.login_args == ("from@example.com", "memory-only-value")
    assert smtp.message["From"] == "from@example.com"
    assert smtp.message["To"] == "to@example.com"
    assert smtp.message["Subject"] == "[TradingBrain] 2026-07-20 A股每日复盘"
    assert "运行状态：completed" in smtp.message.get_body(preferencelist=("plain",)).get_content()
    assert any(part.get_filename() == "2026-07-20-daily-summary.md" for part in smtp.message.iter_attachments())
    assert result.attempted and result.sent and result.attachment is not None


def test_tls_context_uses_certifi_and_is_passed_to_smtp(monkeypatch, tmp_path):
    ca_path = "/trusted/certifi-ca.pem"
    tls_context = type("TLSContext", (), {"check_hostname": True, "verify_mode": 2})()
    observed = {}
    monkeypatch.setattr(sender.certifi, "where", lambda: observed.setdefault("certifi_called", True) and ca_path)

    def fake_context(*, cafile):
        observed["cafile"] = cafile
        return tls_context

    monkeypatch.setattr(sender.ssl, "create_default_context", fake_context)
    result, smtp = send(monkeypatch, tmp_path)
    assert result.sent
    assert observed == {"certifi_called": True, "cafile": ca_path}
    assert smtp.context is tls_context
    assert smtp.context.check_hostname is True
    assert smtp.context.verify_mode == 2


@pytest.mark.parametrize("status,marker", [
    ("completed_with_errors", "[部分失败]"), ("failed", "[运行失败]"),
])
def test_status_subject_markers(monkeypatch, tmp_path, status, marker):
    result, _ = send(monkeypatch, tmp_path, status=status)
    assert marker in result.subject


def test_missing_summary_still_sends_without_attachment(monkeypatch, tmp_path):
    result, smtp = send(monkeypatch, tmp_path, summary=False)
    assert result.sent and result.attachment is None
    assert list(smtp.message.iter_attachments()) == []


def test_disabled_config_skips_without_keychain(monkeypatch, tmp_path):
    config = write_config(tmp_path / "email.toml", enabled=False)
    monkeypatch.setattr(sender, "_read_keychain_password", lambda value: pytest.fail("Keychain read"))
    now = datetime.now(timezone.utc)
    result = sender.send_review_email(
        config, market_date=now.date(), status="completed", started_at=now, finished_at=now,
    )
    assert not result.attempted and not result.sent


def test_smtp_error_is_safe(monkeypatch, tmp_path):
    config = write_config(tmp_path / "email.toml")
    monkeypatch.setattr(sender, "_read_keychain_password", lambda value: "memory-only-value")
    monkeypatch.setattr(sender.smtplib, "SMTP_SSL", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("authentication details hidden")))
    now = datetime.now(timezone.utc)
    with pytest.raises(sender.EmailDeliveryError) as caught:
        sender.send_review_email(
            config, market_date=now.date(), status="completed", started_at=now, finished_at=now,
        )
    text = str(caught.value)
    assert "memory-only-value" not in text
    assert "authentication details hidden" not in text


def test_certificate_failure_is_converted_without_exposing_keychain_value(monkeypatch, tmp_path):
    config = write_config(tmp_path / "email.toml")
    monkeypatch.setattr(sender, "_read_keychain_password", lambda value: "memory-only-value")
    certificate_error = sender.ssl.SSLCertVerificationError(1, "certificate verify failed")
    monkeypatch.setattr(
        sender.smtplib, "SMTP_SSL",
        lambda *a, **k: (_ for _ in ()).throw(certificate_error),
    )
    now = datetime.now(timezone.utc)
    with pytest.raises(sender.EmailDeliveryError) as caught:
        sender.send_review_email(
            config, market_date=now.date(), status="completed", started_at=now, finished_at=now,
        )
    assert "memory-only-value" not in str(caught.value)
    assert "certificate verify failed" not in str(caught.value)
