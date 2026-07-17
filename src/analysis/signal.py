"""Explainable trend and volume-price signal rules."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike

import numpy as np
import pandas as pd

from src.analysis.technical import analyze_stock_daily
from src.data.database import DEFAULT_DATABASE_PATH


REQUIRED_COLUMNS = (
    "date",
    "close",
    "return_pct",
    "ma5",
    "ma10",
    "ma20",
    "volume_ratio_5",
)


@dataclass(frozen=True, slots=True)
class SignalResult:
    """Latest explainable technical signal for one stock."""

    symbol: str
    trade_date: str
    close: float
    daily_return_pct: float | None
    trend_state: str
    price_vs_ma20: str
    volume_state: str
    volume_price_state: str
    risk_score: int
    risk_level: str
    risk_flags: tuple[str, ...]
    evidence: tuple[str, ...]


def _validate_symbol(symbol: str) -> None:
    """Validate a six-digit stock code."""
    if (
        not isinstance(symbol, str)
        or len(symbol) != 6
        or not symbol.isdigit()
    ):
        raise ValueError(f"Invalid stock code: {symbol!r}")


def classify_latest_signal(
    symbol: str,
    data: pd.DataFrame,
) -> SignalResult:
    """Classify the latest trend and volume-price condition.

    This function describes observable technical conditions. It does not
    predict future prices or generate guaranteed buy and sell decisions.
    """
    _validate_symbol(symbol)

    if not isinstance(data, pd.DataFrame):
        raise TypeError("data must be a pandas DataFrame")

    missing_columns = [
        column
        for column in REQUIRED_COLUMNS
        if column not in data.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing required signal columns: {missing_columns}"
        )

    if data.empty:
        raise ValueError("Signal data cannot be empty")

    result = data.copy()

    try:
        parsed_dates = pd.to_datetime(
            result["date"],
            format="%Y-%m-%d",
            errors="raise",
        )

        numeric_columns = [
            "close",
            "return_pct",
            "ma5",
            "ma10",
            "ma20",
            "volume_ratio_5",
        ]

        for column in numeric_columns:
            result[column] = pd.to_numeric(
                result[column],
                errors="coerce",
            )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Signal data contains invalid values"
        ) from exc

    result["date"] = parsed_dates.dt.strftime("%Y-%m-%d")
    result = result.sort_values("date").reset_index(drop=True)

    latest = result.iloc[-1]

    close = float(latest["close"])

    if not np.isfinite(close) or close <= 0:
        raise ValueError("Latest close must be a positive finite value")

    indicator_values = latest[
        [
            "return_pct",
            "ma5",
            "ma10",
            "ma20",
            "volume_ratio_5",
        ]
    ]

    if indicator_values.isna().any():
        daily_return = latest["return_pct"]

        return SignalResult(
            symbol=symbol,
            trade_date=str(latest["date"]),
            close=close,
            daily_return_pct=(
                None
                if pd.isna(daily_return)
                else float(daily_return)
            ),
            trend_state="insufficient_data",
            price_vs_ma20="unknown",
            volume_state="unknown",
            volume_price_state="unknown",
            risk_score=0,
            risk_level="unknown",
            risk_flags=("insufficient_history",),
            evidence=(
                "At least 20 valid trading days are required.",
            ),
        )

    daily_return_pct = float(latest["return_pct"])
    ma5 = float(latest["ma5"])
    ma10 = float(latest["ma10"])
    ma20 = float(latest["ma20"])
    volume_ratio = float(latest["volume_ratio_5"])

    finite_values = [
        daily_return_pct,
        ma5,
        ma10,
        ma20,
        volume_ratio,
    ]

    if not all(np.isfinite(value) for value in finite_values):
        raise ValueError(
            "Latest indicators must contain finite values"
        )

    if volume_ratio < 0:
        raise ValueError("volume_ratio_5 cannot be negative")

    evidence: list[str] = []
    risk_flags: list[str] = []
    risk_score = 0

    if close > ma5 > ma10 > ma20:
        trend_state = "bullish_alignment"
        evidence.append(
            "close > ma5 > ma10 > ma20"
        )
    elif close < ma5 < ma10 < ma20:
        trend_state = "bearish_alignment"
        evidence.append(
            "close < ma5 < ma10 < ma20"
        )
        risk_flags.append("bearish_alignment")
        risk_score += 2
    else:
        trend_state = "mixed"
        evidence.append(
            "Moving averages are not in full bullish or bearish alignment."
        )

    if np.isclose(close, ma20):
        price_vs_ma20 = "at_ma20"
    elif close > ma20:
        price_vs_ma20 = "above_ma20"
    else:
        price_vs_ma20 = "below_ma20"

    evidence.append(
        f"close={close:.2f}, ma20={ma20:.2f}"
    )

    if volume_ratio >= 1.5:
        volume_state = "expanded"
    elif volume_ratio <= 0.7:
        volume_state = "contracted"
    else:
        volume_state = "normal"

    if daily_return_pct > 0:
        price_direction = "rise"
    elif daily_return_pct < 0:
        price_direction = "decline"
    else:
        price_direction = "flat"

    volume_price_state = (
        f"{volume_state}_{price_direction}"
    )

    evidence.append(
        f"return_pct={daily_return_pct:.2f}%, "
        f"volume_ratio_5={volume_ratio:.2f}"
    )

    if (
        daily_return_pct <= -2.0
        and volume_ratio >= 1.5
    ):
        risk_flags.append("high_volume_decline")
        risk_score += 2

    if (
        daily_return_pct > 0
        and volume_ratio <= 0.7
    ):
        risk_flags.append("contracting_volume_rebound")
        risk_score += 1

    if len(result) >= 2:
        previous = result.iloc[-2]

        previous_close = previous["close"]
        previous_ma20 = previous["ma20"]

        if (
            pd.notna(previous_close)
            and pd.notna(previous_ma20)
            and np.isfinite(float(previous_close))
            and np.isfinite(float(previous_ma20))
            and float(previous_close) >= float(previous_ma20)
            and close < ma20
        ):
            risk_flags.append("break_below_ma20")
            risk_score += 2

    if risk_score == 0:
        risk_level = "low"
    elif risk_score <= 2:
        risk_level = "medium"
    else:
        risk_level = "high"

    return SignalResult(
        symbol=symbol,
        trade_date=str(latest["date"]),
        close=close,
        daily_return_pct=daily_return_pct,
        trend_state=trend_state,
        price_vs_ma20=price_vs_ma20,
        volume_state=volume_state,
        volume_price_state=volume_price_state,
        risk_score=risk_score,
        risk_level=risk_level,
        risk_flags=tuple(risk_flags),
        evidence=tuple(evidence),
    )


def analyze_stock_signal(
    symbol: str,
    *,
    database_path: str | PathLike[str] = DEFAULT_DATABASE_PATH,
) -> SignalResult:
    """Load technical indicators and classify the latest signal."""
    indicator_data = analyze_stock_daily(
        symbol,
        database_path=database_path,
    )

    return classify_latest_signal(
        symbol,
        indicator_data,
    )
