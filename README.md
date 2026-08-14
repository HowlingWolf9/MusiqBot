# MuhazBot - Project Overview

MuhazBot is a feature-rich, asynchronous Discord Music Bot built using `discord.py` and `yt-dlp`. It's designed to stream high-quality audio into voice channels with robust queue management, interactive UIs, and automated lifecycle handling.

## 🏗️ Architecture & Core Structure

```
MuhazBot/
├── music_bot.py                 # Bot client, command tree, and application entrypoint
├── music_cog.py                 # Backward-compatible extension facade
├── music/                       # Modular music package
│   ├── config.py                # FFmpeg & YTDL streaming configurations
│   ├── models.py                # Song dataclass and LoopMode enum
│   ├── audio.py                 # YTDLSource stream resolution and search ranking
│   ├── permissions.py           # DJ role and management permission guards
│   ├── utils.py                 # Progress bar generator & safe message cleanup
│   ├── services/                # External services & search integrations
│   │   ├── spotify.py           # Spotify track resolution via oEmbed
│   │   └── autocomplete.py      # Fast YouTube query autocompletion with caching
│   ├── views/                   # Interactive Discord UI components
│   │   ├── player_view.py       # Persistent Now Playing interactive buttons
│   │   ├── queue_view.py        # Paginated queue display
│   │   └── search_view.py       # Ephemeral dropdown search selector
│   ├── player.py                # MusicPlayer state machine, queue loop & autoplay
│   └── cog.py                   # MusicCog: Slash commands & Discord event listeners
├── test_music.py                # Comprehensive pytest test suite
└── music_settings.json          # Persistent JSON store for guild settings
```

## 🚀 Deployment & Scripts

* **`setup_autostart.sh`**: A deployment script that registers the bot as a `systemctl --user` daemon service (`muhazbot.service`), ensuring it automatically starts on boot and restarts upon failure.
* **`run_dev.sh`**: A developer-focused startup script leveraging `watchmedo` to automatically restart the Python process whenever a `.py` file is modified.
* **`run_music.sh`**: The standard production bash script to activate the virtual environment and execute the bot.

## ✨ Key Features & Capabilities

1. **Advanced Queue Management**:
   - Supports basic FIFO queues alongside complex operations like `.shuffle()` and `.remove(index)`.
   - **Autoplay Engine**: When the queue exhausts, the bot can query YouTube Mixes based on the user's listening history to endlessly populate related tracks.
2. **Interactive UI Elements**:
   - **`/search`**: Renders an ephemeral interactive dropdown select menu allowing users to pick a specific track from top search results. It automatically cleans up its own interface upon selection or timeout.
   - **Now Playing Controls**: Sends a persistent, stylized embed housing native Discord UI buttons (Play/Pause, Skip, Stop) for immediate playback control.
3. **Security & Permissions**:
   - Disruptive commands (Skip, Stop, Pause, Resume, Leave, Shuffle, Remove) are tightly restricted by a `check_dj()` verifier.
   - Users must either have **Manage Server** permissions or possess a role explicitly named **"DJ"** to interrupt playback.
4. **Automated Lifecycle Management**:
   - **Idle Timeout**: The internal `PlayerLoop` halts and disconnects the bot exactly 5 minutes after a queue finishes playing.
   - **Empty Channel Detection**: An `on_voice_state_update` listener monitors the channel. If all human users leave the voice chat, a 5-minute countdown is triggered to gracefully terminate the stream and disconnect the bot, preventing bandwidth waste.

## 📦 Core Dependencies
- `discord.py` (Core Discord API wrapper)
- `yt-dlp` (Video metadata extraction and stream resolution)
- `PyNaCl` (Required for Discord voice connections)
- `FFmpeg` (Underlying system dependency handling the audio streaming pipeline)
- `pytest` / `pytest-asyncio` (Testing frameworks)
