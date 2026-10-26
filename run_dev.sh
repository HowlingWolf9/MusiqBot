#!/bin/bash
export PATH="/home/wolf/.deno/bin:$PATH"
source venv/bin/activate
watchmedo auto-restart --directory=./ --pattern="*.py" --ignore-patterns="venv/*;__pycache__/*;.git/*" --recursive -- python music_bot.py
