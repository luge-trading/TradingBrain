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

from src.report.daily_summary import generate_daily_summary
from src.report.stock_report import generate_stock_report
from src.data.database import DEFAULT_DATABASE_PATH
from src.config.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist, normalize_symbols


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

    normalized = normalize_symbols(symbols)

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
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--symbols", nargs="+", help="Stock codes (one or more, 6-digit)")
    group.add_argument("--watchlist", help="Path to watchlist TOML file")
    parser.add_argument("--database-path", dest="database_path", default=DEFAULT_DATABASE_PATH, help="Path to SQLite database")
    parser.add_argument("--output-dir", dest="output_dir", default="reports", help="Output directory for reports")
    parser.add_argument("--limit", type=int, default=500, help="Max historical rows to fetch per stock")
    parser.add_argument("--no-update", dest="no_update", action="store_true", help="Skip data update step")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    update_data = not bool(args.no_update)

    if args.symbols is not None:
        symbols = normalize_symbols(args.symbols)
        source = "命令行"
    else:
        watchlist_path = Path(args.watchlist) if args.watchlist else DEFAULT_WATCHLIST_PATH
        try:
            config = load_watchlist(watchlist_path)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        symbols = config.symbols
        source = str(config.path)

    if not symbols:
        print("Error: No symbols provided or loaded from watchlist", file=sys.stderr)
        return 2

    try:
        result = run_daily_review(
            symbols,
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

    print(f"自选股来源: {source}")
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

    summary_meta: dict[str, int] = {}
    try:
        summary_path = generate_daily_summary(
            result,
            database_path=args.database_path,
            output_dir=args.output_dir,
            watchlist_source=source,
            summary_meta=summary_meta,
        )
        print(f"汇总报告: {summary_path}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.failure_count > 0 or summary_meta.get("analysis_failures", 0) > 0:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
