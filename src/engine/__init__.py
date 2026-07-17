"""Engine package for batch operations like daily reviews."""

from typing import TYPE_CHECKING

__all__ = [
    "run_daily_review",
    "StockReviewOutcome",
    "DailyReviewResult",
]

if TYPE_CHECKING:
    from .daily_review import DailyReviewResult, StockReviewOutcome, run_daily_review


def __getattr__(name: str):
    if name in __all__:
        from . import daily_review

        return getattr(daily_review, name)
    raise AttributeError(f"module {__name__} has no attribute {name}")


def __dir__():
    return sorted(__all__)
