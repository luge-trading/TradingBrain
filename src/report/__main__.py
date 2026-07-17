"""Command-line entry for stock report generation."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.report import generate_stock_report
from src.data.database import DEFAULT_DATABASE_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.report",
        description="Generate per-stock Markdown review report",
    )

    parser.add_argument("symbol", help="六位股票代码，例如 000021")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--database-path", dest="database_path", default=None)
    parser.add_argument("--output-dir", dest="output_dir", default="reports")
    parser.add_argument("--no-update", dest="no_update", action="store_true")

    args = parser.parse_args(argv)

    try:
        db_path = args.database_path if args.database_path is not None else DEFAULT_DATABASE_PATH
        out_dir = args.output_dir if args.output_dir is not None else "reports"

        path = generate_stock_report(
            args.symbol,
            database_path=db_path,
            output_dir=out_dir,
            update_data=(not args.no_update),
            limit=args.limit,
        )
        print(path)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
