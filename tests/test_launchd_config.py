import plistlib
from pathlib import Path
import subprocess
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = PROJECT_ROOT / "config/launchd/com.luge.tradingbrain.scheduled-review.plist.template"
SCRIPT = PROJECT_ROOT / "scripts/manage_launchd.sh"
LABEL = "com.luge.tradingbrain.scheduled-review"


def load_template():
    with TEMPLATE.open("rb") as stream:
        return plistlib.load(stream)


def render_template():
    text = TEMPLATE.read_text(encoding="utf-8").replace("__PROJECT_ROOT__", str(PROJECT_ROOT))
    with tempfile.NamedTemporaryFile(suffix=".plist") as stream:
        stream.write(text.encode("utf-8"))
        stream.flush()
        with Path(stream.name).open("rb") as rendered:
            return plistlib.load(rendered)


def test_template_is_valid_xml_plist():
    assert load_template()


def test_label_is_correct():
    assert load_template()["Label"] == LABEL


def test_program_arguments_are_exact_and_ordered():
    root = "__PROJECT_ROOT__"
    assert load_template()["ProgramArguments"] == [
        f"{root}/.venv/bin/python", "-m", "src.engine.scheduled_review",
        "--watchlist", f"{root}/config/watchlist.toml",
        "--database-path", f"{root}/data/trading_brain.db",
        "--output-dir", f"{root}/reports",
        "--log-path", f"{root}/logs/scheduled-review.jsonl",
    ]


def test_rendered_runtime_paths_are_absolute():
    data = render_template()
    paths = data["ProgramArguments"][0::2] + [
        data["WorkingDirectory"], data["StandardOutPath"], data["StandardErrorPath"]
    ]
    runtime_paths = [value for value in paths if value.startswith(str(PROJECT_ROOT))]
    assert len(runtime_paths) == 8
    assert all(Path(value).is_absolute() for value in runtime_paths)


def test_working_directory_and_schedule():
    data = render_template()
    assert data["WorkingDirectory"] == str(PROJECT_ROOT)
    assert data["StartCalendarInterval"] == {"Hour": 15, "Minute": 30}


def test_no_automatic_load_or_keep_alive():
    data = load_template()
    assert not data.get("RunAtLoad", False)
    assert not data.get("KeepAlive", False)


def test_standard_logs_are_in_project_logs_directory():
    data = render_template()
    assert data["StandardOutPath"] == str(PROJECT_ROOT / "logs/launchd-stdout.log")
    assert data["StandardErrorPath"] == str(PROJECT_ROOT / "logs/launchd-stderr.log")


def test_program_arguments_do_not_use_a_shell_or_activation():
    arguments = load_template()["ProgramArguments"]
    forbidden = {"/bin/sh", "/bin/bash", "-c", "source", "activate"}
    assert forbidden.isdisjoint(arguments)


def test_python_is_unbuffered():
    assert load_template()["EnvironmentVariables"] == {"PYTHONUNBUFFERED": "1"}


def test_template_has_only_the_expected_placeholder():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "__PROJECT_ROOT__" in text
    without_expected = text.replace("__PROJECT_ROOT__", "")
    assert "__" not in without_expected


def test_management_script_exists_and_is_executable():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111


def test_management_script_has_valid_bash_syntax():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_management_script_supports_required_commands_without_forbidden_privilege():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'set -euo pipefail' in text
    for command in ("validate", "install", "uninstall", "reload", "status", "run"):
        assert f"{command})" in text
    assert "sudo" not in text
