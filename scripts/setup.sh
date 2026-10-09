#!/usr/bin/env bash
# One-command setup for IraLens (Linux and macOS).
#
#   ./scripts/setup.sh              # runtime + dev tools into ./.venv
#   ./scripts/setup.sh --no-dev     # runtime only
#   VENV_DIR=/some/path ./scripts/setup.sh
#
# Installs the package in editable mode, then runs a health check. It does not
# download the browser engine; run `iralens install-engine` for that (optional).
set -euo pipefail

cd "$(dirname "$0")/.."
VENV_DIR="${VENV_DIR:-.venv}"
EXTRAS="dev"
for arg in "$@"; do
  case "$arg" in
    --no-dev) EXTRAS="" ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "error: Python 3.10 or newer is required (found: $("$PYTHON" --version 2>&1))" >&2
  exit 1
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "==> creating virtualenv in $VENV_DIR"
  "$PYTHON" -m venv "$VENV_DIR"
fi

TARGET="."
[ -n "$EXTRAS" ] && TARGET=".[$EXTRAS]"
echo "==> installing iralens ($TARGET) in editable mode"
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV_DIR/bin/python" -m pip install -e "$TARGET"

echo "==> health check"
"$VENV_DIR/bin/iralens" --version
"$VENV_DIR/bin/iralens" --json doctor >/dev/null && echo "doctor: ok"

cat <<MSG

Setup complete.

  activate:        source $VENV_DIR/bin/activate
  try it:          iralens search "solar panel efficiency"
  research:        iralens --json research "your question"
  MCP server:      iralens mcp
  tests (offline): $VENV_DIR/bin/python -m pytest -q
  browser engine:  iralens install-engine   (optional, downloads a binary)
MSG
