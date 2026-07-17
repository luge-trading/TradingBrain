"""Generate a daily batch summary report for TradingBrain."""
from __future__ import annotations

from datetime import date, datetime
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING

from src.analysis.signal import SignalResult, analyze_stock_signal
from src.data.database import DEFAULT_DATABASE_PATH

if TYPE_CHECKING:
    from src.engine.daily_review import DailyReviewResult


def _escape_table_cell(value: object) -> str:
    text = str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace("|", "\\|")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "\\n")
    return text


def _format_risk_flags(risk_flags: tuple[str, ...]) -> str:
    return ", ".join(risk_flags) if risk_flags else "无"


def _relative_report_link(report_path: Path, output_path: Path) -> str:
    try:
        relative = report_path.relative_to(output_path)
    except Exception:
        relative = report_path.name
    return f"[查看报告]({relative})"


def generate_daily_summary(
    review_result: "DailyReviewResult",
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    output_dir: str | PathLike[str] = "reports",
    watchlist_source: str | None = None,
    report_date: date | None = None,
    summary_meta: dict[str, int] | None = None,
) -> Path:
    """Generate a daily summary report from batch review results.

    summary_meta is an optional output container that receives structured
    metadata about this run, such as analysis failure counts. It does not
    control report content.
    """
    if summary_meta is not None:
        summary_meta["analysis_failures"] = 0
        summary_meta["unknown_count"] = 0

    if report_date is None:
        report_date = date.today()

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    summary_path = output_path / f"{report_date.strftime('%Y-%m-%d')}-daily-summary.md"

    risk_counts = {
        "high": 0,
        "medium": 0,
        "low": 0,
        "unknown": 0,
    }
    high_risk_stocks: list[str] = []
    analysis_error_count = 0

    rows: list[str] = []
    failure_rows: list[str] = []

    for outcome in review_result.outcomes:
        symbol = outcome.symbol
        if outcome.success and outcome.report_path is not None:
            try:
                signal = analyze_stock_signal(symbol, database_path=database_path)
                latest_trade_date = signal.trade_date
                trend_state = signal.trend_state
                risk_level = signal.risk_level
                risk_flags = _format_risk_flags(signal.risk_flags)
                if risk_level in risk_counts:
                    risk_counts[risk_level] += 1
                else:
                    risk_counts["unknown"] += 1
                if risk_level == "high":
                    high_risk_stocks.append(symbol)
            except Exception as exc:
                latest_trade_date = "分析失败"
                trend_state = "分析失败"
                risk_level = "分析失败"
                risk_flags = f"分析失败: {exc}"
                risk_counts["unknown"] += 1
                analysis_error_count += 1
            report_link = _relative_report_link(Path(outcome.report_path), output_path)
            rows.append(
                "| "
                + " | ".join(
                    _escape_table_cell(value)
                    for value in (
                        symbol,
                        "成功",
                        latest_trade_date,
                        trend_state,
                        risk_level,
                        risk_flags,
                        report_link,
                    )
                )
                + " |"
            )
        else:
            latest_trade_date = "N/A"
            trend_state = "失败"
            risk_level = "失败"
            risk_flags = outcome.error or "失败"
            report_link = "N/A"
            risk_counts["unknown"] += 1
            rows.append(
                "| "
                + " | ".join(
                    _escape_table_cell(value)
                    for value in (
                        symbol,
                        "失败",
                        latest_trade_date,
                        trend_state,
                        risk_level,
                        risk_flags,
                        report_link,
                    )
                )
                + " |"
            )
            failure_rows.append(f"- {symbol}: {outcome.error or '未知错误'}")

    if summary_meta is not None:
        summary_meta["analysis_failures"] = analysis_error_count
        summary_meta["unknown_count"] = risk_counts["unknown"]

    with summary_path.open("w", encoding="utf-8") as fh:
        fh.write("# TradingBrain 每日复盘汇总\n\n")
        fh.write("## 一、执行概览\n")
        fh.write(f"- 运行日期: {report_date.isoformat()}\n")
        fh.write(f"- 自选股来源: {watchlist_source or 'N/A'}\n")
        fh.write(f"- 股票总数: {review_result.total_count}\n")
        fh.write(f"- 成功数量: {review_result.success_count}\n")
        fh.write(f"- 失败数量: {review_result.failure_count}\n")
        fh.write(f"- 汇总生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        fh.write("## 二、风险概览\n")
        fh.write(f"- high 数量: {risk_counts['high']}\n")
        fh.write(f"- medium 数量: {risk_counts['medium']}\n")
        fh.write(f"- low 数量: {risk_counts['low']}\n")
        fh.write(f"- 无法分析数量: {risk_counts['unknown']}\n")

        if high_risk_stocks:
            fh.write("- 高风险股票: ")
            fh.write(", ".join(high_risk_stocks) + "\n\n")
        else:
            fh.write("- 无高风险股票。\n\n")

        fh.write("## 三、个股复盘明细\n")
        fh.write("| 股票代码 | 执行状态 | 最新交易日 | 趋势状态 | 风险等级 | 风险标签 | 个股报告 |\n")
        fh.write("| --- | --- | --- | --- | --- | --- | --- |\n")
        for row in rows:
            fh.write(row + "\n")
        fh.write("\n")

        if failure_rows:
            fh.write("## 四、失败明细\n")
            for line in failure_rows:
                fh.write(line + "\n")
            fh.write("\n")

        fh.write("## 五、说明\n")
        fh.write("本汇总基于历史行情和预设技术规则生成，仅用于交易复盘和风险识别，不构成投资建议。\n")

    return summary_path
