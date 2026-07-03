#!/bin/bash
export PATH="/home/wolf/.deno/bin:$PATH"
source venv/bin/activate
watchmedo auto-restart --directory=./ --pattern="*.py" --recursive -- python music_bot.py
