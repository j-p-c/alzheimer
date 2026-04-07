#!/bin/bash
# Run this from your terminal, NOT from within Claude Code.
# Claude Code owns settings.json during a session — writing to it
# from inside a session will be silently clobbered.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/setup.py" --install
python3 "$SCRIPT_DIR/setup.py" --check
