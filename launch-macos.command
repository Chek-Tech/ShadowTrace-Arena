#!/bin/zsh

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
GUI_SCRIPT="$SCRIPT_DIR/control_center.py"
PYTHON_CMD=""

show_setup_prompt() {
    local component="$1"
    local prompt="Install or repair ${component} for ShadowTrace Arena, then run '${SCRIPT_DIR}/launch-macos.command' again."
    if command -v pbcopy >/dev/null 2>&1; then
        printf '%s' "$prompt" | pbcopy
    fi
    if command -v osascript >/dev/null 2>&1; then
        osascript -e "display dialog \"Missing ${component}. A repair prompt was copied to the clipboard.\" buttons {\"OK\"} default button \"OK\" with title \"ShadowTrace Arena\""
    else
        printf '\n%s\n' "$prompt"
    fi
}

if [[ ! -f "$GUI_SCRIPT" ]]; then
    echo "[ERROR] Control center was not found: $GUI_SCRIPT"
    exit 1
fi

for candidate in \
    "$SCRIPT_DIR/.venv/bin/python" \
    python3 \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
    python
do
    if [[ "$candidate" == */* ]]; then
        [[ -x "$candidate" ]] || continue
    else
        command -v "$candidate" >/dev/null 2>&1 || continue
    fi
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        PYTHON_CMD="$candidate"
        break
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    show_setup_prompt "Python 3.11 or newer"
    exit 1
fi

if ! "$PYTHON_CMD" -c 'import tkinter' >/dev/null 2>&1; then
    show_setup_prompt "Python Tkinter"
    exit 1
fi

cd "$SCRIPT_DIR" || exit 1
exec "$PYTHON_CMD" "$GUI_SCRIPT"
