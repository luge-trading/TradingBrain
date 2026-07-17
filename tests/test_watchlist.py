import pytest
from pathlib import Path

from src.config.watchlist import DEFAULT_WATCHLIST_PATH, WatchlistConfig, load_watchlist, normalize_symbols


def test_default_watchlist_path_constant():
    assert DEFAULT_WATCHLIST_PATH == Path("config/watchlist.toml")


def test_normalize_symbols_strips_duplicates_and_preserves_order():
    result = normalize_symbols(["000021", " 600584 ", "000021", "600584"])
    assert result == ("000021", "600584")


def test_load_watchlist_success(tmp_path):
    toml_path = tmp_path / "watchlist.toml"
    toml_path.write_text("version = 1\nsymbols = [\"000021\"]\n", encoding="utf-8")

    config = load_watchlist(toml_path)

    assert isinstance(config, WatchlistConfig)
    assert config.path == toml_path
    assert config.version == 1
    assert config.symbols == ("000021",)


def test_load_watchlist_file_not_found(tmp_path):
    missing = tmp_path / "missing.toml"
    with pytest.raises(FileNotFoundError) as exc:
        load_watchlist(missing)
    assert str(missing) in str(exc.value)


def test_load_watchlist_invalid_toml(tmp_path):
    toml_path = tmp_path / "bad.toml"
    toml_path.write_text("version = 1\nsymbols = [\"000021\",\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_watchlist(toml_path)


def test_load_watchlist_missing_version(tmp_path):
    toml_path = tmp_path / "watchlist.toml"
    toml_path.write_text("symbols = [\"000021\"]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required 'version'"):
        load_watchlist(toml_path)


def test_load_watchlist_bool_version_rejected(tmp_path):
    toml_path = tmp_path / "watchlist.toml"
    toml_path.write_text("version = true\nsymbols = [\"000021\"]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="version must be an integer"):
        load_watchlist(toml_path)


def test_load_watchlist_non_int_version_rejected(tmp_path):
    toml_path = tmp_path / "watchlist.toml"
    toml_path.write_text("version = \"1\"\nsymbols = [\"000021\"]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="version must be an integer"):
        load_watchlist(toml_path)


def test_load_watchlist_unsupported_version(tmp_path):
    toml_path = tmp_path / "watchlist.toml"
    toml_path.write_text("version = 2\nsymbols = [\"000021\"]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported watchlist version"):
        load_watchlist(toml_path)


def test_load_watchlist_missing_symbols(tmp_path):
    toml_path = tmp_path / "watchlist.toml"
    toml_path.write_text("version = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required 'symbols'"):
        load_watchlist(toml_path)


def test_load_watchlist_symbols_not_array(tmp_path):
    toml_path = tmp_path / "watchlist.toml"
    toml_path.write_text("version = 1\nsymbols = \"000021\"\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be an array"):
        load_watchlist(toml_path)


def test_load_watchlist_symbols_empty(tmp_path):
    toml_path = tmp_path / "watchlist.toml"
    toml_path.write_text("version = 1\nsymbols = []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must not be empty"):
        load_watchlist(toml_path)


def test_load_watchlist_symbols_non_string(tmp_path):
    toml_path = tmp_path / "watchlist.toml"
    toml_path.write_text("version = 1\nsymbols = [1, \"000021\"]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain only strings"):
        load_watchlist(toml_path)


def test_load_watchlist_path_none():
    with pytest.raises(ValueError, match="must not be None"):
        load_watchlist(None)
