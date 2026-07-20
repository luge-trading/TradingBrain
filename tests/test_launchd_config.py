import plistlib
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
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
        "--email-config", f"{root}/config/email.toml",
    ]


def test_rendered_runtime_paths_are_absolute():
    data = render_template()
    paths = data["ProgramArguments"][0::2] + [
        data["WorkingDirectory"], data["StandardOutPath"], data["StandardErrorPath"]
    ]
    runtime_paths = [value for value in paths if value.startswith(str(PROJECT_ROOT))]
    assert len(runtime_paths) == 9
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


def test_email_config_is_absolute_and_plist_has_no_sensitive_fields():
    data = render_template()
    arguments = data["ProgramArguments"]
    index = arguments.index("--email-config")
    assert arguments[index + 1] == str(PROJECT_ROOT / "config/email.toml")
    serialized = TEMPLATE.read_text(encoding="utf-8").lower()
    assert all(word not in serialized for word in ("password", "secret", "token"))


def test_management_script_validates_email_config_semantically():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'config/email.toml' in text
    assert 'tomllib.load' in text


def make_isolated_script_project(tmp_path):
    project = tmp_path / "project"
    script_dir = project / "scripts"
    template_dir = project / "config/launchd"
    python_dir = project / ".venv/bin"
    fake_bin = tmp_path / "fake-bin"
    temp_dir = tmp_path / "temporary-files"
    home = tmp_path / "home"
    for directory in (script_dir, template_dir, python_dir, fake_bin, temp_dir, home):
        directory.mkdir(parents=True, exist_ok=True)

    shutil.copy2(SCRIPT, script_dir / SCRIPT.name)
    shutil.copy2(TEMPLATE, template_dir / TEMPLATE.name)
    (project / "config/watchlist.toml").write_text('symbols = ["600000"]\n', encoding="utf-8")
    (project / "config/email.toml").write_text(
        '''version = 1
enabled = true
[smtp]
host = "smtp.example.com"
port = 465
timeout_seconds = 5
[message]
sender = "sender@example.com"
recipients = ["recipient@example.com"]
subject_prefix = "[TradingBrain]"
attach_summary = false
[keychain]
service = "com.example.smtp"
''', encoding="utf-8",
    )
    python_wrapper = python_dir / "python"
    python_wrapper.write_text(
        "#!/bin/bash\n"
        f"exec {shlex.quote(sys.executable)} \"$@\"\n",
        encoding="utf-8",
    )
    python_wrapper.chmod(0o755)

    state = tmp_path / "launchctl-state"
    launchctl = fake_bin / "launchctl"
    launchctl.write_text(
        f'''#!/bin/bash
set -euo pipefail
state={state!s}
case "$1" in
    print) [[ -e "$state" ]] ;;
    bootstrap) touch "$state" ;;
    bootout) rm -f "$state" ;;
    kickstart) [[ -e "$state" ]] ;;
    *) exit 2 ;;
esac
''', encoding="utf-8",
    )
    launchctl.chmod(0o755)
    environment = {
        "HOME": str(home),
        "TMPDIR": str(temp_dir) + "/",
        "PATH": str(fake_bin) + ":/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(PROJECT_ROOT),
    }
    return project, environment, state, temp_dir


def run_isolated_command(project, environment, command):
    return subprocess.run(
        [str(project / "scripts/manage_launchd.sh"), command],
        cwd=PROJECT_ROOT, env=environment, capture_output=True, text=True, check=False,
    )


def test_validate_succeeds_and_removes_temporary_plist(tmp_path):
    project, environment, _, temp_dir = make_isolated_script_project(tmp_path)
    result = run_isolated_command(project, environment, "validate")
    assert result.returncode == 0, result.stderr
    assert list(temp_dir.iterdir()) == []


def test_install_succeeds_and_removes_temporary_plist(tmp_path):
    project, environment, state, temp_dir = make_isolated_script_project(tmp_path)
    result = run_isolated_command(project, environment, "install")
    assert result.returncode == 0, result.stderr
    assert state.exists()
    assert list(temp_dir.iterdir()) == []


def test_reload_clears_return_trap_before_outer_function_returns(tmp_path):
    project, environment, state, temp_dir = make_isolated_script_project(tmp_path)
    state.touch()
    result = run_isolated_command(project, environment, "reload")
    assert result.returncode == 0, result.stderr
    assert "unbound variable" not in result.stderr
    assert state.exists()
    assert list(temp_dir.iterdir()) == []


def test_render_failure_still_removes_temporary_plist(tmp_path):
    project, environment, _, temp_dir = make_isolated_script_project(tmp_path)
    template = project / "config/launchd" / TEMPLATE.name
    template.write_text("not a plist with __PROJECT_ROOT__", encoding="utf-8")
    result = run_isolated_command(project, environment, "validate")
    assert result.returncode != 0
    assert list(temp_dir.iterdir()) == []
