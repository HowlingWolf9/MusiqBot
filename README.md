# MuhazBot - Project Overview

MuhazBot is a feature-rich, asynchronous Discord Music Bot built using `discord.py` and `yt-dlp`. It's designed to stream high-quality audio into voice channels with robust queue management, interactive UIs, and automated lifecycle handling.

## 🏗️ Architecture & Core Files

* **`music_bot.py`**: The application entry point. Initializes the `commands.Bot` instance, sets up necessary intents, handles globally syncing slash commands, and loads the main `music_cog` extension.
* **`music_cog.py`**: The brain of the bot. Contains the `MusicCog` (housing all slash commands and event listeners), the `MusicPlayer` (manages the playback loop, stream creation, and state per guild), and `Song/YTDLSource` models.
* **`test_music.py`**: A robust `pytest` suite utilizing `pytest-asyncio` and `unittest.mock` to validate critical integrations like permission boundaries, mock-voice connections, and timer teardowns.
* **`music_settings.json`**: A local JSON datastore used to persist guild-specific configurations (like `autoplay` toggles and user-defined `volume` levels) between restarts.

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
