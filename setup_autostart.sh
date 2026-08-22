#!/bin/bash

# Create user systemd directory if it doesn't exist
mkdir -p ~/.config/systemd/user/

# Create the service file
cat << 'EOF' > ~/.config/systemd/user/musiqbot.service
[Unit]
Description=MusiqBot Discord Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/wolf/Projects/MusiqBot
ExecStart=/home/wolf/Projects/MusiqBot/run_dev.sh
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
EOF

# Reload systemd, enable and start the service
systemctl --user daemon-reload
systemctl --user enable musiqbot.service
systemctl --user start musiqbot.service

# Enable lingering so the service stays running even when the user logs out
loginctl enable-linger $USER

echo "Autostart configured successfully! The bot is now running in the background."
echo "You can check its status anytime with: systemctl --user status musiqbot.service"
echo "To view logs, use: journalctl --user -u musiqbot.service -f"
