#!/bin/bash
export PATH="/home/wolf/.deno/bin:$PATH"

if systemctl --user is-active --quiet muhazbot.service; then
    echo "MuhazBot is currently running as a systemd background service (muhazbot.service)."
    echo "Attaching to logs instead of starting a new instance..."
    echo "Press Ctrl+C to exit logs (bot will continue running in background)."
    exec journalctl --user -u muhazbot.service -f
fi

# Automatically kill any previously running instances of music_bot.py
pkill -9 -f "music_bot.py" 2>/dev/null || true

source venv/bin/activate
exec watchmedo auto-restart --directory=./ --pattern="*.py" --recursive -- python music_bot.py

