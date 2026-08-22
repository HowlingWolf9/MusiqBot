# Setup & Deployment

## Prerequisites

| Requirement | Notes |
|---|---|
| Python **3.10+** | Codebase uses `str \| None` union syntax; developed against 3.14 |
| FFmpeg | System package (`apt install ffmpeg`, `brew install ffmpeg`, etc.) — required for the audio streaming pipeline |
| A Discord application + bot account | Create at <https://discord.com/developers/applications>, enable the **Message Content** intent under *Bot → Privileged Gateway Intents* |
| Bot invite with scopes `bot` + `applications.commands` | Permissions: View Channels, Send Messages, Manage Messages (for `/clear_messages`), Connect, Speak, and ideally Move Members |

## Installation

```bash
git clone <your-fork-url> MuhazBot
cd MuhazBot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt            # runtime dependencies
pip install -r requirements-dev.txt        # + pytest/ruff (development only)
```

## Configuration

### Environment

The bot loads a `.env` file from the working directory (via `python-dotenv`):

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | The bot token from the Discord developer portal |

`.env` is gitignored — never commit real tokens.

### Runtime data files

Created automatically next to the code, both written **atomically**
(temp file + `fsync` + `os.replace`):

| File | Purpose |
|---|---|
| `music_settings.json` | Per-guild settings keyed by guild ID string: `volume` (0–1 float, default 0.5), `autoplay` (bool, default false), optional `dj_role_id` / `dj_role_name` from `/setdjrole` |
| `music_orphans.json` | Index of public UI messages (up to 50 per guild) so a fresh boot can delete embeds left behind by a crashed/reloaded process |

Both are safe to delete while the bot is stopped; they are rebuilt as needed.

## Running

### Scripts provided

| Script | Use case | What it does |
|---|---|---|
| `run_music.sh` | Day-to-day hosting | If `muhazbot.service` is active, attaches to its logs instead of starting a duplicate. Otherwise kills stale `music_bot.py` processes and starts the bot under `watchmedo auto-restart` |
| `run_dev.sh` | Development | Runs the bot under `watchmedo auto-restart` — the process restarts whenever any `.py` file changes |
| `setup_autostart.sh` | Server deployment | Installs a `systemctl --user` unit (see below) |

All scripts activate `venv/bin/activate` before launching.

### Manual run

```bash
source venv/bin/activate
python music_bot.py
```

### First boot checklist

1. Start the bot — you should see `Logged in as ... (ID: ...)`.
2. From the **owner's** account, send `!sync` in any shared server channel to
   register slash commands globally (can take up to an hour to propagate;
   server-wide it appears instantly).
3. Join a voice channel and try `/play never gonna give you up`.

## Production Autostart (systemd user service)

```bash
./setup_autostart.sh
```

This writes `~/.config/systemd/user/muhazbot.service`:

```ini
[Unit]
Description=MuhazBot Discord Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/wolf/Projects/MuhazBot
ExecStart=/home/wolf/Projects/MuhazBot/run_dev.sh
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
```

…then enables + starts it and runs `loginctl enable-linger` so the bot keeps
running after you log out.

Useful commands:

```bash
systemctl --user status muhazbot.service   # health
journalctl --user -u muhazbot.service -f   # follow logs
systemctl --user restart muhazbot.service  # apply config/code changes
systemctl --user disable --now muhazbot.service  # stop autostart
```

Note the unit executes `run_dev.sh`, i.e. production here still uses
`watchmedo`; edits to `.py` files trigger an automatic in-place restart.

## Operational Safeguards

- **Single-instance lock** — `music_bot.py` takes a non-blocking exclusive
  `flock` on `/tmp/muhazbot.lock`. A second instance exits immediately with a
  log error. This prevents overlapping bots from watchmedo restarts or double
  invocations. The lock is released *first* during signal handling so a
  replacement process can start while the old one is still tearing down.
- **Graceful shutdown** — `SIGTERM`/`SIGINT` trigger `shutdown_cleanup()`:
  every guild player is cleaned up (UI messages deleted, voice clients
  disconnected) within a 15 s budget before the bot closes.
- **Crash recovery** — on boot, the orphan sweep deletes any Now Playing /
  queue / added-track messages recorded by a previous process.
- **Error filtering** — transient Discord errors (`Unknown Interaction`
  10062, acknowledgement 40060) from fast typers/expired autocomplete are
  suppressed rather than spamming logs.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Slash commands don't appear | Run `!sync` as owner; invite the bot with the `applications.commands` scope |
| Tracks fail instantly with 403 / "unavailable" | yt-dlp is outdated relative to YouTube changes — `pip install -U yt-dlp`. The bot already pins `player_client: android,web` to avoid PO-token-gated clients |
| No audio but no errors | Ensure FFmpeg is installed and on `PATH`; check the bot has **Connect/Speak** permissions in that VC |
| `Another MuhazBot instance is already running` | A previous process still holds `/tmp/muhazbot.lock`; kill it or remove the stale lock holder (`fuser /tmp/muhazbot.lock`) |
| Bot doesn't leave empty channels | It waits 5 minutes by design; the minute-sweep covers missed events, so persistent presence usually means a ghost member or missing permissions |
