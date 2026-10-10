#!/bin/zsh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
VENV_ROOT="$PROJECT_ROOT/.venv"
VENV_PYTHON="$VENV_ROOT/bin/python"
SKIP_CODEX=0
SKIP_CODE_SENTINEL=0
NO_LAUNCH=0
KEEP_HOOKS=0
CHECK_ONLY=0
TOOLCHAIN_PROFILE='full'
PACKAGE_MANAGER_BOOTSTRAP=1

usage() {
    cat <<'EOF'
Usage: ./deploy-macos.command [options]

Options:
  --skip-codex        Install the application without deploying Codex packs.
  --skip-code-sentinel
                      Do not deploy the bundled Code Sentinel core or tools.
  --toolchain PROFILE Security tools: none, core, or full (default: full).
  --no-package-manager-bootstrap
                      Do not install Scoop/Homebrew when it is missing.
  --no-launch         Do not open the desktop control center after deployment.
  --keep-hooks-active Keep an existing Codex hooks.json active.
  --check             Check prerequisites without changing files.
  -h, --help          Show this help.
EOF
}

while (( $# > 0 )); do
    case "$1" in
        --skip-codex) SKIP_CODEX=1 ;;
        --skip-code-sentinel) SKIP_CODE_SENTINEL=1 ;;
        --toolchain)
            shift
            if (( $# == 0 )); then
                echo '[ERROR] --toolchain requires none, core, or full.' >&2
                exit 2
            fi
            TOOLCHAIN_PROFILE="$1"
            if [[ "$TOOLCHAIN_PROFILE" != 'none' && \
                  "$TOOLCHAIN_PROFILE" != 'core' && \
                  "$TOOLCHAIN_PROFILE" != 'full' ]]; then
                echo "[ERROR] Invalid toolchain profile: $TOOLCHAIN_PROFILE" >&2
                exit 2
            fi
            ;;
        --no-package-manager-bootstrap) PACKAGE_MANAGER_BOOTSTRAP=0 ;;
        --no-launch) NO_LAUNCH=1 ;;
        --keep-hooks-active) KEEP_HOOKS=1 ;;
        --check) CHECK_ONLY=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "[ERROR] Unknown option: $1" >&2; usage; exit 2 ;;
    esac
    shift
done

step() {
    printf '\n==> %s\n' "$1"
}

python_is_usable() {
    local candidate="$1"
    [[ -x "$candidate" ]] || return 1
    "$candidate" -c \
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
        >/dev/null 2>&1
}

find_python() {
    local candidate
    local candidates=(
        /opt/homebrew/bin/python3
        /opt/homebrew/bin/python3.14
        /opt/homebrew/bin/python3.13
        /opt/homebrew/bin/python3.12
        /opt/homebrew/bin/python3.11
        /usr/local/bin/python3
        /usr/local/bin/python3.14
        /usr/local/bin/python3.13
        /usr/local/bin/python3.12
        /usr/local/bin/python3.11
        /Library/Frameworks/Python.framework/Versions/Current/bin/python3
        /usr/bin/python3
    )
    if command -v python3 >/dev/null 2>&1; then
        candidates=("$(command -v python3)" "${candidates[@]}")
    fi
    for candidate in "${candidates[@]}"; do
        if python_is_usable "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

find_brew() {
    if command -v brew >/dev/null 2>&1; then
        command -v brew
        return 0
    fi
    for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

install_python() {
    local brew_command
    brew_command="$(find_brew || true)"
    if [[ -z "$brew_command" ]]; then
        echo '[ERROR] Python 3.11+ and Homebrew were not found.' >&2
        echo 'Install Python from https://www.python.org/downloads/macos/ and run this file again.' >&2
        return 1
    fi
    step 'Installing Python 3.12 with Homebrew'
    "$brew_command" install python@3.12
}

loopback_proxy_is_dead() {
    local proxy_value="${HTTPS_PROXY:-${HTTP_PROXY:-${ALL_PROXY:-}}}"
    [[ -n "$proxy_value" ]] || return 1
    "$VENV_PYTHON" - "$proxy_value" <<'PY'
import socket
import sys
from urllib.parse import urlparse

proxy = urlparse(sys.argv[1])
if proxy.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise SystemExit(1)
port = proxy.port or (443 if proxy.scheme == "https" else 80)
try:
    with socket.create_connection((proxy.hostname, port), timeout=0.75):
        pass
except OSError:
    raise SystemExit(0)
raise SystemExit(1)
PY
}

install_project_package() {
    if loopback_proxy_is_dead; then
        echo '[WARN] The configured loopback proxy is not running; pip will use a direct connection.'
        env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
            -u http_proxy -u https_proxy -u all_proxy \
            "$VENV_PYTHON" -m pip install --disable-pip-version-check \
            --editable "$PROJECT_ROOT"
    else
        "$VENV_PYTHON" -m pip install --disable-pip-version-check \
            --editable "$PROJECT_ROOT"
    fi
}

step 'Checking Python 3.11 or newer'
VENV_READY=0
BASE_PYTHON=''
if python_is_usable "$VENV_PYTHON"; then
    VENV_READY=1
else
    BASE_PYTHON="$(find_python || true)"
    if [[ -z "$BASE_PYTHON" && $CHECK_ONLY -eq 0 ]]; then
        install_python
        BASE_PYTHON="$(find_python || true)"
    fi
    if [[ -z "$BASE_PYTHON" ]]; then
        echo '[ERROR] No usable Python 3.11+ installation was found.' >&2
        exit 1
    fi
fi

if (( CHECK_ONLY )); then
    if (( VENV_READY )); then
        SELECTED_PYTHON="$VENV_PYTHON"
    else
        SELECTED_PYTHON="$BASE_PYTHON"
    fi
    echo "Python ready: $SELECTED_PYTHON"
    echo "Project root: $PROJECT_ROOT"
    echo "Code Sentinel toolchain profile: $TOOLCHAIN_PROFILE"
    echo 'Check completed; no files were changed.'
    exit 0
fi

if (( ! VENV_READY )); then
    if [[ -e "$VENV_ROOT" ]]; then
        BACKUP="$VENV_ROOT.broken-$(date +%Y%m%d-%H%M%S)"
        echo "Moving unusable environment to: $BACKUP"
        mv "$VENV_ROOT" "$BACKUP"
    fi
    step 'Creating the project virtual environment'
    "$BASE_PYTHON" -m venv "$VENV_ROOT"
    if ! python_is_usable "$VENV_PYTHON"; then
        echo '[ERROR] Virtual environment creation failed.' >&2
        exit 1
    fi
else
    echo "Reusing virtual environment: $VENV_ROOT"
fi

step 'Installing ShadowTrace Arena and runtime dependencies'
install_project_package

step 'Running pre-deployment health checks'
"$VENV_PYTHON" -m shadowtrace_arena doctor

if (( ! SKIP_CODEX )); then
    DISCOVERY_JSON="$("$VENV_PYTHON" -m shadowtrace_arena discover --json)"
    CODEX_COUNT="$(printf '%s' "$DISCOVERY_JSON" | "$VENV_PYTHON" -c \
        'import json, sys; print(json.load(sys.stdin)["count"])')"
    if (( CODEX_COUNT > 0 )); then
        step "Deploying integrations to $CODEX_COUNT Codex configuration(s)"
        INSTALL_ARGS=(-m shadowtrace_arena install --all)
        if (( KEEP_HOOKS )); then
            INSTALL_ARGS+=(--skip-hooks-isolation)
        fi
        "$VENV_PYTHON" "${INSTALL_ARGS[@]}"
    else
        echo '[WARN] Codex config.toml was not found; integration deployment was skipped.'
    fi
else
    echo 'Codex integration deployment skipped by option.'
fi

if (( ! SKIP_CODE_SENTINEL )); then
    step "Deploying Code Sentinel and the $TOOLCHAIN_PROFILE security toolchain"
    TOOLCHAIN_ARGS=(
        -m shadowtrace_arena toolchain --profile "$TOOLCHAIN_PROFILE"
    )
    if (( SKIP_CODEX )); then
        TOOLCHAIN_ARGS+=(--skip-codex)
    fi
    if (( ! PACKAGE_MANAGER_BOOTSTRAP )); then
        TOOLCHAIN_ARGS+=(--no-package-manager-bootstrap)
    fi
    "$VENV_PYTHON" "${TOOLCHAIN_ARGS[@]}"
else
    echo 'Code Sentinel deployment skipped by option.'
fi

step 'Running final health checks'
"$VENV_PYTHON" -m shadowtrace_arena doctor

TK_AVAILABLE=1
if ! "$VENV_PYTHON" -c 'import tkinter' >/dev/null 2>&1; then
    TK_AVAILABLE=0
    BREW_COMMAND="$(find_brew || true)"
    if [[ -n "$BREW_COMMAND" ]]; then
        PYTHON_MINOR="$("$VENV_PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        TK_FORMULA="python-tk@$PYTHON_MINOR"
        if "$BREW_COMMAND" info "$TK_FORMULA" >/dev/null 2>&1; then
            step "Installing Tkinter with Homebrew ($TK_FORMULA)"
            "$BREW_COMMAND" install "$TK_FORMULA"
            if "$VENV_PYTHON" -c 'import tkinter' >/dev/null 2>&1; then
                TK_AVAILABLE=1
            fi
        fi
    fi
fi

if (( ! TK_AVAILABLE )); then
    echo '[WARN] Tkinter is unavailable. CLI deployment is complete, but the GUI cannot start.'
fi

printf '\nShadowTrace Arena deployment completed.\n'
echo "CLI: $VENV_PYTHON -m shadowtrace_arena --help"

if (( ! NO_LAUNCH && TK_AVAILABLE )); then
    step 'Opening the control center'
    LOG_DIR="$HOME/Library/Logs"
    mkdir -p "$LOG_DIR"
    nohup "$VENV_PYTHON" "$PROJECT_ROOT/control_center.py" \
        >>"$LOG_DIR/ShadowTrace-Arena.log" 2>&1 </dev/null &
    echo "Control center PID: $!"
fi
