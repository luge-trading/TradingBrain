"""Retrieve current Chinese A-share stock market data."""

from dataclasses import dataclass

import akshare as ak


@dataclass(frozen=True, slots=True)
class Stock:
    """Current market data for an A-share stock."""

    stock_name: str
    latest_price: float
    change_percent: float
    volume: int
    turnover: float


def get_stock(symbol: str) -> Stock:
    """Return the latest market data for an A-share stock code.

    Args:
        symbol: Six-digit A-share stock code, for example ``"000021"``.

    Returns:
        The stock's latest quote data.

    Raises:
        ValueError: If ``symbol`` is invalid or does not exist.
        RuntimeError: If AKShare cannot retrieve market data.
    """
    if not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isdigit():
        raise ValueError(f"Invalid stock code: {symbol!r}")

    try:
        market_data = ak.stock_zh_a_spot_em()
    except Exception as exc:
        raise RuntimeError("Unable to retrieve stock market data") from exc

    matching_rows = market_data.loc[market_data["代码"].astype(str) == symbol]
    if matching_rows.empty:
        raise ValueError(f"Stock code does not exist: {symbol}")

    row = matching_rows.iloc[0]
    return Stock(
        stock_name=str(row["名称"]),
        latest_price=float(row["最新价"]),
        change_percent=float(row["涨跌幅"]),
        volume=int(row["成交量"]),
        turnover=float(row["成交额"]),
    )
