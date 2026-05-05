#!/usr/bin/env bash
# run.sh — SkillBridge Opportunity Finder launcher
#
# First run: creates a virtual environment and installs dependencies.
# Every run after that: just works.
#
# Usage:
#   ./run.sh                          # interactive mode
#   ./run.sh --industry "Healthcare"  # filter by industry
#   ./run.sh --search "cyber"         # keyword search
#   ./run.sh --list-industries        # show all categories
#   ./run.sh --help                   # all options

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"
PYTHON_SCRIPT="$SCRIPT_DIR/scrape_skillbridge.py"

# Create the virtual environment if it doesn't exist yet.
if [ ! -d "$VENV_DIR" ]; then
    echo "[setup] Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Install or update dependencies if requirements.txt is newer than the venv.
MARKER="$VENV_DIR/.requirements_installed"
if [ ! -f "$MARKER" ] || [ "$REQUIREMENTS" -nt "$MARKER" ]; then
    echo "[setup] Installing dependencies..."
    "$VENV_DIR/bin/pip" install --quiet -r "$REQUIREMENTS"
    touch "$MARKER"
fi

# Run the program, forwarding all arguments.
exec "$VENV_DIR/bin/python" "$PYTHON_SCRIPT" "$@"
