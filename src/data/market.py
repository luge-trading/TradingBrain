"""Market-wide daily facts and validation helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final


SSE_AMOUNT_SOURCE: Final[str] = "SSE_DAILY_STOCK_OVERVIEW"
SZSE_AMOUNT_SOURCE: Final[str] = "SZSE_DAILY_STOCK_OVERVIEW"
EASTMONEY_BREADTH_SOURCE: Final[str] = "EASTMONEY_MARKET_BREADTH"
SQLITE_INTEGER_MAX: Final[int] = 9_223_372_036_854_775_807


def validate_trade_date(value: str) -> str:
    """Return a strict ISO trade date or raise ``ValueError``."""
    if not isinstance(value, str):
        raise ValueError(f"Invalid market trade date: {value!r}")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid market trade date: {value!r}") from exc
    if parsed.strftime("%Y-%m-%d") != value:
        raise ValueError(f"Invalid market trade date: {value!r}")
    return value


def yi_yuan_to_yuan(value: object) -> int:
    """Convert an official amount in 亿元 to integer yuan without floats."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Invalid amount in 亿元: {value!r}")
    cleaned = value.replace(",", "").strip() if isinstance(value, str) else value
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid amount in 亿元: {value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"Invalid amount in 亿元: {value!r}")
    if amount.as_tuple().exponent < -2:
        raise ValueError("Amount in 亿元 exceeds official two-decimal precision")
    yuan = amount * Decimal(100_000_000)
    if yuan != yuan.to_integral_value() or yuan > SQLITE_INTEGER_MAX:
        raise ValueError(f"Amount cannot be stored as SQLite integer yuan: {value!r}")
    return int(yuan)


def _validate_nullable_count(name: str, value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer or None")


def _validate_nullable_amount(name: str, value: int | None) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > SQLITE_INTEGER_MAX
    ):
        raise ValueError(f"{name} must be a non-negative SQLite integer or None")


@dataclass(frozen=True, slots=True)
class ExchangeDailyAmount:
    """One exchange's official daily stock turnover."""

    trade_date: str
    amount_yuan: int
    source: str

    def __post_init__(self) -> None:
        validate_trade_date(self.trade_date)
        _validate_nullable_amount("amount_yuan", self.amount_yuan)
        if self.amount_yuan is None:
            raise ValueError("amount_yuan must not be None")
        if self.source not in {SSE_AMOUNT_SOURCE, SZSE_AMOUNT_SOURCE}:
            raise ValueError(f"Unsupported exchange amount source: {self.source!r}")


@dataclass(frozen=True, slots=True)
class MarketBreadth:
    """Post-close Shanghai plus Shenzhen breadth counts."""

    advance_count: int
    decline_count: int
    flat_count: int
    source: str = EASTMONEY_BREADTH_SOURCE

    def __post_init__(self) -> None:
        for name in ("advance_count", "decline_count", "flat_count"):
            _validate_nullable_count(name, getattr(self, name))
            if getattr(self, name) is None:
                raise ValueError(f"{name} must not be None")
        if self.source != EASTMONEY_BREADTH_SOURCE:
            raise ValueError(f"Unsupported market breadth source: {self.source!r}")


@dataclass(frozen=True, slots=True)
class MarketDaily:
    """Validated daily market facts ready for persistence."""

    trade_date: str
    sh_amount_yuan: int | None = None
    sz_amount_yuan: int | None = None
    total_amount_yuan: int | None = None
    advance_count: int | None = None
    decline_count: int | None = None
    flat_count: int | None = None
    sh_amount_source: str | None = None
    sz_amount_source: str | None = None
    breadth_source: str | None = None

    def __post_init__(self) -> None:
        validate_trade_date(self.trade_date)
        for name in ("sh_amount_yuan", "sz_amount_yuan", "total_amount_yuan"):
            _validate_nullable_amount(name, getattr(self, name))
        for name in ("advance_count", "decline_count", "flat_count"):
            _validate_nullable_count(name, getattr(self, name))

        amounts = (self.sh_amount_yuan, self.sz_amount_yuan, self.total_amount_yuan)
        amount_sources = (self.sh_amount_source, self.sz_amount_source)
        if all(value is None for value in amounts):
            if any(source is not None for source in amount_sources):
                raise ValueError("Amount sources require a complete amount group")
        elif any(value is None for value in amounts):
            raise ValueError("Market amount fields must be all present or all None")
        else:
            if self.total_amount_yuan != self.sh_amount_yuan + self.sz_amount_yuan:
                raise ValueError("total_amount_yuan must equal Shanghai plus Shenzhen")
            if amount_sources != (SSE_AMOUNT_SOURCE, SZSE_AMOUNT_SOURCE):
                raise ValueError("Market amount sources do not match official exchanges")

        breadth = (self.advance_count, self.decline_count, self.flat_count)
        if all(value is None for value in breadth):
            if self.breadth_source is not None:
                raise ValueError("breadth_source requires complete breadth counts")
        elif any(value is None for value in breadth):
            raise ValueError("Market breadth fields must be all present or all None")
        elif self.breadth_source != EASTMONEY_BREADTH_SOURCE:
            raise ValueError("Market breadth source does not match EastMoney")


def compose_market_daily(
    trade_date: str,
    *,
    sh_amount: ExchangeDailyAmount | None = None,
    sz_amount: ExchangeDailyAmount | None = None,
    breadth: MarketBreadth | None = None,
) -> MarketDaily:
    """Compose atomic amount and breadth groups for one trade date."""
    validate_trade_date(trade_date)
    if (sh_amount is None) != (sz_amount is None):
        raise ValueError("Shanghai and Shenzhen amounts must be supplied together")
    if sh_amount is not None and sz_amount is not None:
        if sh_amount.trade_date != trade_date or sz_amount.trade_date != trade_date:
            raise ValueError("Exchange amount dates must match market trade_date")
        if sh_amount.source != SSE_AMOUNT_SOURCE or sz_amount.source != SZSE_AMOUNT_SOURCE:
            raise ValueError("Exchange amount sources are assigned to the wrong market")
        sh_value = sh_amount.amount_yuan
        sz_value = sz_amount.amount_yuan
        total = sh_value + sz_value
        if total > SQLITE_INTEGER_MAX:
            raise ValueError("Combined market amount exceeds SQLite integer range")
    else:
        sh_value = sz_value = total = None

    return MarketDaily(
        trade_date=trade_date,
        sh_amount_yuan=sh_value,
        sz_amount_yuan=sz_value,
        total_amount_yuan=total,
        advance_count=None if breadth is None else breadth.advance_count,
        decline_count=None if breadth is None else breadth.decline_count,
        flat_count=None if breadth is None else breadth.flat_count,
        sh_amount_source=None if sh_amount is None else sh_amount.source,
        sz_amount_source=None if sz_amount is None else sz_amount.source,
        breadth_source=None if breadth is None else breadth.source,
    )


def calculate_advance_ratio(record: MarketDaily) -> float | None:
    """Calculate breadth ratio without persisting a derived fact."""
    counts = (record.advance_count, record.decline_count, record.flat_count)
    if any(value is None for value in counts):
        return None
    denominator = sum(counts)
    if denominator <= 0:
        return None
    return record.advance_count / denominator
