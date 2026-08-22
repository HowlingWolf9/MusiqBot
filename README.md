# MuhazBot

MuhazBot is a feature-rich, asynchronous Discord Music Bot built using
`discord.py` and `yt-dlp`. It streams high-quality audio into voice channels
with a priority-aware queue, autoplay engine, interactive UIs, and fully
automated lifecycle handling.

## 📚 Documentation

Comprehensive documentation lives in [`docs/`](docs/):

| Document | Contents |
|---|---|
| [**Commands Reference**](docs/commands.md) | Every slash command, Now Playing button, permission rules, response behavior |
| [**Setup & Deployment**](docs/setup.md) | Installation, `.env` config, runtime files, run scripts, systemd autostart, troubleshooting |
| [**Architecture**](docs/architecture.md) | Module map, player state machine, queue priority model, autoplay engine, audio pipeline, message lifecycle |
| [**Testing & Contributing**](docs/testing.md) | Running/extending the test suite, coding conventions, contribution checklist |

## 🚀 Quick Start

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
echo "DISCORD_TOKEN=your-token-here" > .env    # requires FFmpeg on the system
./run_music.sh
```

Then send `!sync` from the bot owner's account once to register slash
commands. Full instructions: [Setup & Deployment](docs/setup.md).

## ✨ Key Features

- **Advanced queue management** — FIFO queue where user requests always rank
  ahead of autoplay picks; shuffle, move, remove, clear, paginated `/queue`.
- **Autoplay engine** — seeds a YouTube Mix from listening history when the
  queue runs dry, filtering non-music content (reactions, tutorials,
  podcasts...) and prefetching picks ahead of playback.
- **Interactive UI** — persistent Now Playing embed with native buttons
  (⏯️ ⏭️ ⏹️ 🔁 🔀), ephemeral dropdown `/search`, paginated queue browser.
- **Rich playback control** — seek/replay, volume persistence, three loop
  modes, Spotify link resolution, playlist ingestion (up to 100 tracks).
- **Security & permissions** — disruptive commands restricted by a DJ check:
  *Manage Server*/*Administrator*, a role named **DJ**, or a custom role set
  via `/setdjrole`.
- **Automated lifecycle** — disconnects after 5 idle minutes or an empty
  voice channel; deletes all of its messages on stop, shutdown, crash
  recovery, and dev reloads.
- **Operational safety** — single-instance lock, graceful SIGTERM/SIGINT
  teardown, atomic settings persistence.

## 🏗️ Project Structure

```
MuhazBot/
├── music_bot.py                 # Bot client, command tree, entrypoint
├── music_cog.py                 # Backward-compatible extension facade
├── music/                       # Modular music package
│   ├── config.py                # FFmpeg & YTDL streaming configurations
│   ├── models.py                # Song dataclass and LoopMode enum
│   ├── audio.py                 # YTDLSource stream resolution & search ranking
│   ├── permissions.py           # DJ role and management permission guards
│   ├── utils.py                 # Progress bar generator & safe message cleanup
│   ├── services/                # Spotify resolution, YouTube autocomplete
│   ├── views/                   # Player / queue / search UI components
│   ├── player.py                # MusicPlayer state machine, queue loop & autoplay
│   └── cog.py                   # MusicCog: slash commands & event listeners
├── test_music.py                # Comprehensive pytest test suite (~1400 lines)
├── run_music.sh / run_dev.sh    # Launch scripts (watchmedo auto-restart)
├── setup_autostart.sh           # systemd --user service installer
└── music_settings.json          # Persistent per-guild settings store
```

See the [Architecture guide](docs/architecture.md) for how it all fits
together.

## 📦 Core Dependencies

- `discord.py` — Discord API wrapper
- `yt-dlp` — video metadata extraction and stream resolution
- `PyNaCl` — required for Discord voice connections
- `FFmpeg` — system dependency powering the audio streaming pipeline
- `aiohttp` + `python-dotenv` — HTTP client and env loading
- `pytest` / `pytest-asyncio` / `pytest-mock` — testing toolchain
