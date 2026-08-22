# MusiqBot Documentation

MusiqBot is a feature-rich, asynchronous Discord music bot built with
[`discord.py`](https://discordpy.readthedocs.io/) and
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp). It streams high-quality audio into
voice channels with a persistent playback loop, priority-aware queueing,
interactive UI components, and fully automated lifecycle handling.

## Documentation Index

| Document | Audience | Contents |
|---|---|---|
| [Commands Reference](commands.md) | Users & admins | Every slash command, UI button, permission requirement, and response behavior |
| [Setup & Deployment](setup.md) | Admins & hosts | Installation, configuration (`.env`), runtime files, scripts, systemd autostart, troubleshooting |
| [Architecture](architecture.md) | Developers | Module map, player state machine, queue priority model, autoplay engine, audio pipeline, message lifecycle |
| [Testing & Contributing](testing.md) | Contributors | Running the test suite, what is covered, coding conventions, contribution checklist |

## Quick Start

```bash
# 1. Create and activate a virtual environment (Python 3.10+)
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Provide your bot token
echo "DISCORD_TOKEN=your-token-here" > .env

# 4. Run the bot
./run_music.sh        # or: python music_bot.py
```

FFmpeg must be installed on the system (`sudo apt install ffmpeg` on Debian/Ubuntu).

After the first start, run `!sync` in any channel **from the bot owner's
account** to register the slash commands globally with Discord.

## Feature Highlights

- **Playback** — YouTube tracks/searches/playlists, Spotify link resolution,
  seek/replay, volume control, three loop modes.
- **Queue** — FIFO queue where user requests always rank ahead of autoplay
  picks; shuffle, move, remove, clear; paginated ephemeral `/queue` view.
- **Autoplay engine** — when enabled and the queue runs dry, seeds a YouTube
  Mix from listening history, filters out non-music content (reactions,
  tutorials, podcasts...), and prefetches the next pick before the current
  track ends for gapless continuation.
- **Interactive UI** — persistent Now Playing embed with native buttons
  (pause/skip/stop/loop/shuffle), ephemeral dropdown `/search`, paginated
  queue browser.
- **Self-management** — leaves after 5 minutes of an empty queue or an empty
  voice channel; cleans up its own messages on stop, shutdown, crash recovery,
  and dev reloads via an orphan-message index.
- **Operational safety** — single-instance lock, graceful SIGTERM/SIGINT
  teardown, atomic settings persistence, per-guild connection locks.

## External References

- discord.py docs: <https://discordpy.readthedocs.io/>
- yt-dlp docs: <https://github.com/yt-dlp/yt-dlp#readme>
- FFmpeg download: <https://ffmpeg.org/download.html>
