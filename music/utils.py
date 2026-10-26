async def delete_message_safe(msg) -> bool:
    if msg:
        try:
            await msg.delete()
            return True
        except Exception:
            pass
    return False


def coerce_duration(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def create_progress_bar(elapsed: int, total: int, length: int = 14) -> str:
    if total <= 0:
        return "🔘" + "━" * (length - 1)
    progress = min(1.0, max(0.0, elapsed / float(total)))
    filled_length = int(round(length * progress))
    filled_length = min(length, max(0, filled_length))
    bar = "━" * filled_length + "🔘" + "━" * (length - filled_length)
    return bar


def is_direct_stream_url(data: dict) -> bool:
    """Return True if data contains a resolved direct media stream URL for FFmpeg,
    rather than a webpage or playlist URL that requires extraction.
    """
    if not isinstance(data, dict):
        return False
    url = data.get("url")
    if not url or not isinstance(url, str):
        return False
    webpage_url = data.get("webpage_url")
    if webpage_url and url == webpage_url:
        return False
    url_lower = url.lower()
    web_patterns = (
        "youtube.com/watch",
        "youtu.be/",
        "youtube.com/shorts/",
        "music.youtube.com/watch",
        "youtube.com/playlist",
        "soundcloud.com/",
        "open.spotify.com/",
        "spotify.com/",
    )
    if any(pat in url_lower for pat in web_patterns):
        return False
    return True
