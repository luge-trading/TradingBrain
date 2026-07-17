"""Generate per-stock Markdown review reports."""
from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

from src.data.update import update_stock_daily, UpdateResult
from src.data.database import DEFAULT_DATABASE_PATH
from src.analysis.technical import analyze_stock_daily
from src.analysis.signal import analyze_stock_signal, SignalResult


_TREND_MAP = {
    "bullish_alignment": "多头排列",
    "bearish_alignment": "空头排列",
    "mixed": "均线结构混合",
    "insufficient_data": "历史数据不足",
    "above_ma20": "收盘价位于 MA20 上方",
    "below_ma20": "收盘价位于 MA20 下方",
    "at_ma20": "收盘价接近 MA20",
}

_VOLUME_MAP = {
    "expanded": "放量",
    "contracted": "缩量",
    "normal": "常量",
    "expanded_rise": "放量上涨",
    "expanded_decline": "放量下跌",
    "contracted_rise": "缩量上涨",
    "contracted_decline": "缩量下跌",
    "normal_rise": "常量上涨",
    "normal_decline": "常量下跌",
    "normal_flat": "平量平盘",
}

_RISK_LEVEL_MAP = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "unknown": "未知",
}

_RISK_FLAG_MAP = {
    "bearish_alignment": "空头排列",
    "high_volume_decline": "放量下跌",
    "contracting_volume_rebound": "缩量反弹",
    "break_below_ma20": "跌破 MA20",
    "insufficient_history": "历史数据不足",
}


def _fmt_price(x: Optional[float]) -> str:
    if x is None or pd.isna(x):
        return "N/A"
    return f"{x:.2f}"


def _fmt_pct(x: Optional[float]) -> str:
    if x is None or pd.isna(x):
        return "N/A"
    return f"{x:.2f}%"


def _fmt_int(x: Optional[float]) -> str:
    if x is None or pd.isna(x):
        return "N/A"
    return f"{int(round(x))}"


def _map_or_original(mapping: dict, key: str) -> str:
    return mapping.get(key, key)


def generate_stock_report(
    symbol: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
    output_dir: str | PathLike[str] = "reports",
    update_data: bool = True,
    limit: int = 500,
) -> Path:
    """Generate a Chinese Markdown review report for a single stock.

    Returns the Path to the created markdown file.
    """
    symbol = str(symbol)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    update_result: Optional[UpdateResult] = None

    if update_data:
        update_result = update_stock_daily(
            symbol,
            database_path=database_path,
            limit=limit,
        )

    # load indicators and latest signal
    indicator_df = analyze_stock_daily(
        symbol,
        database_path=database_path,
    )

    signal = analyze_stock_signal(
        symbol,
        database_path=database_path,
    )

    trade_date = signal.trade_date

    filename = f"{trade_date}-{symbol}-review.md"
    file_path = output_path / filename

    try:
        with file_path.open("w", encoding="utf-8") as fh:
            fh.write(f"# {symbol} {trade_date} 复盘\n\n")

            # Section 1: Data update
            fh.write("## 一、数据更新\n")

            if update_data:
                fh.write(f"- 请求数据条数: {update_result.fetched_rows}\n")
                fh.write(f"- 新增数据条数: {update_result.new_rows}\n")
                fh.write(f"- 实际写入条数: {update_result.stored_rows}\n")
                fh.write(f"- 更新前最后交易日: {update_result.latest_before or 'N/A'}\n")
                fh.write(f"- 更新后最后交易日: {update_result.latest_after or 'N/A'}\n\n")
            else:
                fh.write("- 本次未执行数据更新\n\n")

            # Section 2: Latest market
            fh.write("## 二、最新行情\n")

            latest = indicator_df.iloc[-1]

            headers = [
                "交易日期",
                "收盘价",
                "当日涨跌幅",
                "MA5",
                "MA10",
                "MA20",
                "成交量",
                "5日平均成交量",
                "5日量能比",
            ]

            fh.write("|" + "|".join(headers) + "|\n")
            fh.write("|" + "|".join(["---"] * len(headers)) + "|\n")

            row_vals = [
                str(latest["date"]),
                _fmt_price(latest.get("close")),
                _fmt_pct(latest.get("return_pct")),
                _fmt_price(latest.get("ma5")),
                _fmt_price(latest.get("ma10")),
                _fmt_price(latest.get("ma20")),
                _fmt_int(latest.get("volume")),
                _fmt_int(latest.get("volume_ma5")),
                ("N/A" if pd.isna(latest.get("volume_ratio_5")) else f"{latest.get('volume_ratio_5'):.2f}"),
            ]

            fh.write("|" + "|".join(row_vals) + "|\n\n")

            # Section 3: Trend status
            fh.write("## 三、趋势状态\n")
            fh.write(f"- 趋势: {_map_or_original(_TREND_MAP, signal.trend_state)}\n")
            fh.write(f"- MA20 位置: {_map_or_original(_TREND_MAP, signal.price_vs_ma20)}\n\n")

            # Section 4: 量价状态
            fh.write("## 四、量价状态\n")
            fh.write(f"- 量能: {_map_or_original(_VOLUME_MAP, signal.volume_state)}\n")
            fh.write(f"- 量价: {_map_or_original(_VOLUME_MAP, signal.volume_price_state)}\n\n")

            # Section 5: 风险识别
            fh.write("## 五、风险识别\n")
            fh.write(f"- 风险分数: {signal.risk_score}\n")
            fh.write(f"- 风险等级: {_map_or_original(_RISK_LEVEL_MAP, signal.risk_level)}\n")
            mapped_flags = [ _map_or_original(_RISK_FLAG_MAP, f) for f in signal.risk_flags ]
            fh.write(f"- 风险标签: {', '.join(mapped_flags) if mapped_flags else '无'}\n")
            fh.write(f"- 判断证据: {'; '.join(signal.evidence) if signal.evidence else '无'}\n\n")

            # Section 6: 最近十个交易日
            fh.write("## 六、最近十个交易日\n")
            last_n = indicator_df.tail(10)

            headers = ["日期", "收盘价", "涨跌幅", "MA5", "MA10", "MA20", "量能比"]
            fh.write("|" + "|".join(headers) + "|\n")
            fh.write("|" + "|".join(["---"] * len(headers)) + "|\n")

            for _, r in last_n.iterrows():
                vals = [
                    str(r["date"]),
                    _fmt_price(r.get("close")),
                    _fmt_pct(r.get("return_pct")),
                    _fmt_price(r.get("ma5")),
                    _fmt_price(r.get("ma10")),
                    _fmt_price(r.get("ma20")),
                    ("N/A" if pd.isna(r.get("volume_ratio_5")) else f"{r.get('volume_ratio_5'):.2f}"),
                ]
                fh.write("|" + "|".join(vals) + "|\n")

            fh.write("\n")

            # Section 7: 次日观察条件
            fh.write("## 七、次日观察条件\n")

            # Gather needed indicators
            def _safe_float(val):
                try:
                    v = float(val)
                except Exception:
                    return None
                if not np.isfinite(v):
                    return None
                return v

            close_v = _safe_float(latest.get("close"))
            ma5_v = _safe_float(latest.get("ma5"))
            ma10_v = _safe_float(latest.get("ma10"))
            ma20_v = _safe_float(latest.get("ma20"))
            vol_ratio_v = _safe_float(latest.get("volume_ratio_5"))
            ret_v = _safe_float(latest.get("return_pct"))

            # MA20 position
            if close_v is None or ma20_v is None:
                fh.write("- MA20 位置：历史数据不足，暂无法形成该观察条件。\n")
            else:
                if close_v > ma20_v:
                    fh.write(f"- MA20 位置：当前收盘价 {_fmt_price(close_v)}，位于 MA20 {_fmt_price(ma20_v)} 上方；观察是否跌破 {_fmt_price(ma20_v)}。\n")
                elif close_v < ma20_v:
                    fh.write(f"- MA20 位置：当前收盘价 {_fmt_price(close_v)}，位于 MA20 {_fmt_price(ma20_v)} 下方；观察是否重新站上 {_fmt_price(ma20_v)}。\n")
                else:
                    fh.write(f"- MA20 位置：当前收盘价 {_fmt_price(close_v)}，与 MA20 {_fmt_price(ma20_v)} 相近；观察是否形成明显偏离。\n")

            # Moving averages alignment
            if ma5_v is None or ma10_v is None or ma20_v is None:
                fh.write("- 均线结构：历史数据不足，暂无法形成该观察条件。\n")
            else:
                # represent ordering
                if ma5_v > ma10_v > ma20_v:
                    order = f"MA5={_fmt_price(ma5_v)} > MA10={_fmt_price(ma10_v)} > MA20={_fmt_price(ma20_v)}"
                elif ma5_v < ma10_v < ma20_v:
                    order = f"MA5={_fmt_price(ma5_v)} < MA10={_fmt_price(ma10_v)} < MA20={_fmt_price(ma20_v)}"
                else:
                    order = f"MA5={_fmt_price(ma5_v)}, MA10={_fmt_price(ma10_v)}, MA20={_fmt_price(ma20_v)} (结构混合)"

                fh.write(f"- 均线结构：当前为 {order}；观察该排列是否继续维持。\n")

            # Volume threshold
            threshold = 1.5
            if vol_ratio_v is None:
                fh.write("- 放量阈值：历史数据不足，暂无法形成该观察条件。\n")
            else:
                fh.write(f"- 放量阈值：当前量能比为 {vol_ratio_v:.2f}；达到放量标准需要量能比不低于 {threshold:.2f}。\n")

            # Volume-decline risk
            if ret_v is None or vol_ratio_v is None:
                fh.write("- 放量下跌风险：历史数据不足，暂无法形成该观察条件。\n")
            else:
                fh.write(f"- 放量下跌风险：观察是否同时出现当日涨跌幅 ≤ -2.00%（当前 {ret_v:.2f}%）且量能比 ≥ {threshold:.2f}（当前 {vol_ratio_v:.2f}）。\n")

            # Trend weakening
            if ma5_v is None or ma10_v is None or ma20_v is None:
                fh.write("- 趋势转弱条件：历史数据不足，暂无法形成该观察条件。\n")
            else:
                fh.write(f"- 趋势转弱条件：观察收盘价是否跌破 MA20（{_fmt_price(ma20_v)}），或 MA5（{_fmt_price(ma5_v)}）是否下穿 MA10（{_fmt_price(ma10_v)}）。\n")

            fh.write("\n")

            # Section 8: 说明
            fh.write("## 八、说明\n")
            fh.write("本报告基于历史行情和预设规则生成，仅用于交易复盘和风险识别，不构成投资建议。\n")

    except OSError as exc:
        raise RuntimeError("Unable to write report file") from exc

    return file_path
