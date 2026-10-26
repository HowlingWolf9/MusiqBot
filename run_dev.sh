#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.deno/bin:$PATH"
cd "$SCRIPT_DIR"
source "$SCRIPT_DIR/venv/bin/activate"
watchmedo auto-restart --directory="$SCRIPT_DIR" --pattern="*.py" --ignore-patterns="venv/*;__pycache__/*;.git/*" --recursive -- python music_bot.py
