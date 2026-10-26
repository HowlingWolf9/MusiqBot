#!/bin/bash
# Auto-update yt-dlp in the venv; restart the bot only if the version changed.
set -euo pipefail
cd "$(dirname "$0")"
source venv/bin/activate

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [ytdlp-update] $*"; }

OLD=$(python -c "import yt_dlp; print(yt_dlp.version.__version__)")
if ! pip install -q -U yt-dlp; then
    log "ERROR: pip install failed — keeping running bot on $OLD"
    exit 1
fi
NEW=$(python -c "import yt_dlp; print(yt_dlp.version.__version__)")

if [ "$OLD" = "$NEW" ]; then
    log "yt-dlp already up to date ($NEW)"
    exit 0
fi

log "yt-dlp updated $OLD -> $NEW"

# Keep the requirements pin in sync so a future `pip install -r` doesn't downgrade
sed -i "s/^yt-dlp==.*/yt-dlp==$NEW/" requirements.txt

# The running process still has the old module loaded; restart to pick up the fix
if systemctl --user is-active --quiet musiqbot.service; then
    systemctl --user restart musiqbot.service
    log "musiqbot.service restarted on yt-dlp $NEW"
else
    log "musiqbot.service not active — skipping restart"
fi
