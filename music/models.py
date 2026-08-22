import time
from enum import Enum

from music.utils import coerce_duration


class LoopMode(Enum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


class Song:
    def __init__(self, data, requester, is_autoplay: bool = False):
        self.data = data
        self.requester = requester
        self.is_autoplay = bool(is_autoplay)
        self.title = data.get("title") or "Unknown Track"
        self.url = data.get("webpage_url") or data.get("url")
        self.thumbnail = data.get("thumbnail")
        self.duration = coerce_duration(data.get("duration"))
        self.uploader = data.get("uploader") or data.get("artist") or "Unknown Artist"
        self.extracted_at = time.time()
        self.added_msg = None
        self.source = None
