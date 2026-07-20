#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="com.luge.tradingbrain.scheduled-review"
TEMPLATE="$PROJECT_ROOT/config/launchd/com.luge.tradingbrain.scheduled-review.plist.template"
DESTINATION="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
SERVICE_TARGET="$DOMAIN/$LABEL"

render_plist() {
    local output_path="$1"
    "$PROJECT_ROOT/.venv/bin/python" - "$TEMPLATE" "$output_path" "$PROJECT_ROOT" <<'PY'
from pathlib import Path
import sys

template_path, output_path, project_root = map(Path, sys.argv[1:])
text = template_path.read_text(encoding="utf-8")
if text.count("__PROJECT_ROOT__") == 0:
    raise SystemExit("template does not contain __PROJECT_ROOT__")
output_path.write_text(text.replace("__PROJECT_ROOT__", str(project_root)), encoding="utf-8")
PY
}

safe_bootout() {
    local prefix="$1"
    if launchctl print "$SERVICE_TARGET" >/dev/null 2>&1; then
        echo "$prefix removing loaded service $SERVICE_TARGET"
        launchctl bootout "$SERVICE_TARGET"
    else
        echo "$prefix service is not loaded: $SERVICE_TARGET"
    fi
}

check_prerequisites() {
    local prefix="$1"
    [[ -f "$TEMPLATE" ]] || { echo "$prefix missing template: $TEMPLATE" >&2; return 1; }
    [[ -x "$PROJECT_ROOT/.venv/bin/python" ]] || { echo "$prefix Python is not executable: $PROJECT_ROOT/.venv/bin/python" >&2; return 1; }
    [[ -f "$PROJECT_ROOT/config/watchlist.toml" ]] || { echo "$prefix missing watchlist: $PROJECT_ROOT/config/watchlist.toml" >&2; return 1; }
    [[ -f "$PROJECT_ROOT/config/email.toml" ]] || { echo "$prefix missing email config: $PROJECT_ROOT/config/email.toml" >&2; return 1; }
    "$PROJECT_ROOT/.venv/bin/python" - "$PROJECT_ROOT/config/email.toml" <<'PY'
from pathlib import Path
import sys
import tomllib

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8").lower()
for forbidden in ("password", "app_password", "token", "secret", "credential"):
    if forbidden in text:
        raise SystemExit(f"email config contains forbidden field: {forbidden}")
with path.open("rb") as stream:
    data = tomllib.load(stream)
print(f"Email sender: {data['message']['sender']}")
print(f"Email recipients: {', '.join(data['message']['recipients'])}")
print(f"SMTP: {data['smtp']['host']}:{data['smtp']['port']}")
PY
    "$PROJECT_ROOT/.venv/bin/python" -W error -m src.engine.scheduled_review --help >/dev/null
}

validate() (
    local prefix="[validate]"
    local temporary_plist
    echo "$prefix checking prerequisites"
    check_prerequisites "$prefix"
    temporary_plist="$(mktemp "${TMPDIR:-/tmp}/tradingbrain-launchd.XXXXXX")"
    trap 'rm -f "$temporary_plist"' EXIT
    render_plist "$temporary_plist"
    plutil -lint "$temporary_plist"
    echo "$prefix project root: $PROJECT_ROOT"
    echo "$prefix Python: $PROJECT_ROOT/.venv/bin/python"
    echo "$prefix watchlist: $PROJECT_ROOT/config/watchlist.toml"
    echo "$prefix database: $PROJECT_ROOT/data/trading_brain.db"
    echo "$prefix reports: $PROJECT_ROOT/reports"
    echo "$prefix JSONL log: $PROJECT_ROOT/logs/scheduled-review.jsonl"
    echo "$prefix email config: $PROJECT_ROOT/config/email.toml"
    echo "$prefix schedule: daily at 15:30 system local time"
    echo "$prefix validation passed"
)

install_service() (
    local prefix="${1:-[install]}"
    local temporary_plist
    echo "$prefix checking prerequisites"
    check_prerequisites "$prefix"
    mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_ROOT/logs" "$PROJECT_ROOT/reports" "$PROJECT_ROOT/data"
    temporary_plist="$(mktemp "${TMPDIR:-/tmp}/tradingbrain-launchd.XXXXXX")"
    trap 'rm -f "$temporary_plist"' EXIT
    render_plist "$temporary_plist"
    plutil -lint "$temporary_plist"
    safe_bootout "$prefix"
    cp "$temporary_plist" "$DESTINATION"
    chmod 600 "$DESTINATION"
    echo "$prefix bootstrapping $DESTINATION"
    launchctl bootstrap "$DOMAIN" "$DESTINATION"
    launchctl print "$SERVICE_TARGET"
    echo "$prefix installed: $DESTINATION"
)

uninstall_service() {
    local prefix="[uninstall]"
    safe_bootout "$prefix"
    if [[ -e "$DESTINATION" ]]; then
        rm -f "$DESTINATION"
        echo "$prefix removed: $DESTINATION"
    else
        echo "$prefix plist is not installed: $DESTINATION"
    fi
}

reload_service() {
    local prefix="[reload]"
    safe_bootout "$prefix"
    install_service "$prefix"
}

status_service() {
    local prefix="[status]"
    if ! launchctl print "$SERVICE_TARGET"; then
        echo "$prefix service is not loaded: $SERVICE_TARGET" >&2
        return 1
    fi
}

run_service() {
    local prefix="[run]"
    if ! launchctl print "$SERVICE_TARGET" >/dev/null 2>&1; then
        echo "$prefix service is not loaded: $SERVICE_TARGET" >&2
        return 1
    fi
    echo "$prefix starting $SERVICE_TARGET"
    launchctl kickstart -k "$SERVICE_TARGET"
}

usage() {
    echo "Usage: $0 {validate|install|uninstall|reload|status|run}" >&2
}

case "${1:-}" in
    validate) validate ;;
    install) install_service ;;
    uninstall) uninstall_service ;;
    reload) reload_service ;;
    status) status_service ;;
    run) run_service ;;
    *) usage; exit 2 ;;
esac
