"""
MuhazBot Music Cog - Extension entrypoint and backward-compatible facade.
All core logic is organized modularly in the `music` package.
"""

import yt_dlp

from music import (
    LoopMode,
    MusicCog,
    MusicPlayer,
    MusicQueue,
    PlayerView,
    QueueView,
    SearchSelect,
    SearchView,
    Song,
    YTDLSource,
    check_dj,
    check_dj_permission,
    coerce_duration,
    create_progress_bar,
    delete_message_safe,
    extract_video_id,
    ffmpeg_options,
    fetch_song_autocomplete,
    is_valid_music_entry,
    normalize_title,
    resolve_spotify,
    select_best_search_entry,
    setup,
    ytdl,
    ytdl_format_options,
)

__all__ = [
    "LoopMode",
    "MusicCog",
    "MusicPlayer",
    "MusicQueue",
    "coerce_duration",
    "extract_video_id",
    "is_valid_music_entry",
    "normalize_title",
    "PlayerView",
    "QueueView",
    "SearchSelect",
    "SearchView",
    "Song",
    "YTDLSource",
    "check_dj",
    "check_dj_permission",
    "create_progress_bar",
    "delete_message_safe",
    "ffmpeg_options",
    "fetch_song_autocomplete",
    "resolve_spotify",
    "select_best_search_entry",
    "setup",
    "yt_dlp",
    "ytdl",
    "ytdl_format_options",
]
