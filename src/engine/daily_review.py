"""Batch daily review orchestration.

This module implements run_daily_review which calls generate_stock_report
for each symbol and aggregates results.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple

from src.report.stock_report import generate_stock_report
from src.data.database import DEFAULT_DATABASE_PATH


@dataclass(frozen=True)
class StockReviewOutcome:
    symbol: str
    success: bool
    report_path: Path | None
    error: str | None


@dataclass(frozen=True)
class DailyReviewResult:
    outcomes: Tuple[StockReviewOutcome, ...]

    @property
    def total_count(self) -> int:
        return len(self.outcomes)

    @property
    def success_count(self) -> int:
        return sum(1 for o in self.outcomes if o.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for o in self.outcomes if not o.success)


def _validate_and_normalize_symbols(symbols: Iterable[str]) -> list[str]:
    if symbols is None:
        raise ValueError("symbols must be provided and contain at least one code")

    if isinstance(symbols, str):
        raise ValueError("symbols must be an iterable of stock code strings, not a single string")

    try:
        items = list(symbols)
    except TypeError:
        raise ValueError("symbols must be an iterable of stock code strings")

    if not items:
        raise ValueError("symbols must contain at least one stock code")

    seen: set[str] = set()
    out: list[str] = []

    for raw in items:
        if not isinstance(raw, str):
            raise ValueError(f"Invalid stock code (not a string): {raw!r}")
        code = raw.strip()
        if not code:
            raise ValueError("Empty stock code is not allowed")
        if len(code) != 6 or not code.isdigit():
            raise ValueError(f"Invalid stock code: {code!r}")
        if code in seen:
            continue
        seen.add(code)
        out.append(code)

    return out


def run_daily_review(
    symbols: Iterable[str],
    *,
    database_path=DEFAULT_DATABASE_PATH,
    output_dir: str | Path = "reports",
    update_data: bool = True,
    limit: int = 500,
) -> DailyReviewResult:
    """Run batch daily review for multiple symbols.

    Behavior:
    - Validates input symbols (must be iterable of 6-digit strings)
    - Deduplicates preserving first occurrence
    - Calls generate_stock_report for each symbol and records success/failure
    """
    # validate limit (disallow bool)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    if database_path is None or output_dir is None:
        raise ValueError("database_path and output_dir must not be None")

    normalized = _validate_and_normalize_symbols(symbols)

    outcomes: list[StockReviewOutcome] = []

    for symbol in normalized:
        try:
            path = generate_stock_report(
                symbol,
                database_path=database_path,
                output_dir=output_dir,
                update_data=update_data,
                limit=limit,
            )
            outcomes.append(StockReviewOutcome(symbol=symbol, success=True, report_path=Path(path), error=None))
        except Exception as exc:
            outcomes.append(StockReviewOutcome(symbol=symbol, success=False, report_path=None, error=str(exc)))

    return DailyReviewResult(outcomes=tuple(outcomes))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="src.engine.daily_review", description="Batch daily review for multiple stocks")
    parser.add_argument("--symbols", nargs="+", required=True, help="Stock codes (one or more, 6-digit)")
    parser.add_argument("--database-path", dest="database_path", default=DEFAULT_DATABASE_PATH, help="Path to SQLite database")
    parser.add_argument("--output-dir", dest="output_dir", default="reports", help="Output directory for reports")
    parser.add_argument("--limit", type=int, default=500, help="Max historical rows to fetch per stock")
    parser.add_argument("--no-update", dest="no_update", action="store_true", help="Skip data update step")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    update_data = not bool(args.no_update)

    try:
        result = run_daily_review(
            args.symbols,
            database_path=args.database_path,
            output_dir=args.output_dir,
            update_data=update_data,
            limit=args.limit,
        )
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print("批量复盘完成")
    print(f"总数: {result.total_count}")
    print(f"成功: {result.success_count}")
    print(f"失败: {result.failure_count}")
    print()

    for o in result.outcomes:
        if o.success:
            print(f"{o.symbol} 成功 {o.report_path}")
        else:
            print(f"{o.symbol} 失败 {o.error}")

    return 0 if result.failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
