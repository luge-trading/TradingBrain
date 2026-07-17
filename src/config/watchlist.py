"""Watchlist TOML configuration loader."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Iterable


def normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
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

    return tuple(out)


DEFAULT_WATCHLIST_PATH: Path = Path("config/watchlist.toml")


@dataclass(frozen=True)
class WatchlistConfig:
    path: Path
    version: int
    symbols: tuple[str, ...]


def load_watchlist(path: str | PathLike[str] = DEFAULT_WATCHLIST_PATH) -> WatchlistConfig:
    if path is None:
        raise ValueError("path must not be None")

    config_path = Path(path)

    try:
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Watchlist file not found: {config_path}") from exc
    except PermissionError as exc:
        raise RuntimeError(f"Unable to read watchlist file: {config_path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to read watchlist file: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML in watchlist file: {config_path}") from exc

    if not isinstance(raw, dict):
        raise ValueError("Watchlist file must contain a TOML table at the root")

    if "version" not in raw:
        raise ValueError("Watchlist file missing required 'version' field")

    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError("Watchlist version must be an integer")
    if version != 1:
        raise ValueError(f"Unsupported watchlist version: {version}")

    if "symbols" not in raw:
        raise ValueError("Watchlist file missing required 'symbols' field")

    symbols = raw["symbols"]
    if not isinstance(symbols, list):
        raise ValueError("Watchlist 'symbols' field must be an array")
    if len(symbols) == 0:
        raise ValueError("Watchlist 'symbols' must not be empty")
    if any(not isinstance(item, str) for item in symbols):
        raise ValueError("Watchlist 'symbols' array must contain only strings")

    normalized = normalize_symbols(symbols)

    return WatchlistConfig(path=config_path, version=version, symbols=tuple(normalized))
