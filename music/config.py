import yt_dlp

ytdl_format_options = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"]
        }
    },
}

# Low-latency streaming options: prioritize Opus WebM and resilient reconnects without dropping packets
ffmpeg_options = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)
