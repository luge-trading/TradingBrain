"""Thin CLI wrapper for batch daily review.

The actual parser and CLI logic live in src.engine.daily_review.
"""
from __future__ import annotations

from src.engine.daily_review import main


if __name__ == "__main__":
    raise SystemExit(main())
