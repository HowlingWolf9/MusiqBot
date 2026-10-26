#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Create user systemd directory if it doesn't exist
mkdir -p ~/.config/systemd/user/

# Create the service file
cat << EOF > ~/.config/systemd/user/musiqbot.service
[Unit]
Description=MusiqBot Discord Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${SCRIPT_DIR}/run_dev.sh
Restart=on-failure
RestartSec=5s
# Graceful teardown budget in music_bot.py is 15s; give it room before SIGKILL
TimeoutStopSec=30s

[Install]
WantedBy=default.target
EOF

# Create the yt-dlp auto-update service + timer (runs update_ytdlp.sh daily;
# restarts the bot only when the version actually changed)
cat << EOF > ~/.config/systemd/user/musiqbot-ytdlp-update.service
[Unit]
Description=MusiqBot yt-dlp auto-update

[Service]
Type=oneshot
ExecStart=${SCRIPT_DIR}/update_ytdlp.sh
EOF

cat << 'EOF' > ~/.config/systemd/user/musiqbot-ytdlp-update.timer
[Unit]
Description=Daily yt-dlp update for MusiqBot

[Timer]
OnCalendar=*-*-* 06:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Reload systemd, enable and start the service
systemctl --user daemon-reload
systemctl --user enable musiqbot.service
systemctl --user start musiqbot.service
systemctl --user enable --now musiqbot-ytdlp-update.timer

# Enable lingering so the service stays running even when the user logs out
loginctl enable-linger $USER

echo "Autostart configured successfully! The bot is now running in the background."
echo "You can check its status anytime with: systemctl --user status musiqbot.service"
echo "To view logs, use: journalctl --user -u musiqbot.service -f"
echo "yt-dlp updates run daily at 06:00: systemctl --user list-timers musiqbot-ytdlp-update.timer"
