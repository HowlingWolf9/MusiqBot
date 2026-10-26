import time
from enum import Enum

from music.utils import coerce_duration


class LoopMode(Enum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


# yt-dlp info dicts carry formats[], thumbnails[], captions, ... that Song
# never reads. Keeping only consumed keys stops single-track adds from
# retaining megabyte-scale payloads for the song's whole lifetime.
_SONG_DATA_KEYS = (
    "id",
    "title",
    "url",
    "webpage_url",
    "duration",
    "thumbnail",
    "uploader",
    "channel",
    "artist",
    # create_source() reuses the stream URL + negotiated User-Agent without a
    # second network round-trip while the metadata is fresh.
    "http_headers",
    "_extracted_at",
)


class Song:
    def __init__(self, data, requester, is_autoplay: bool = False):
        self.data = (
            {k: data[k] for k in _SONG_DATA_KEYS if k in data}
            if isinstance(data, dict)
            else {}
        )
        self.requester = requester
        self.is_autoplay = bool(is_autoplay)
        self.title = data.get("title") or "Unknown Track"
        self.url = data.get("webpage_url") or data.get("url")
        self.thumbnail = data.get("thumbnail")
        self.duration = coerce_duration(data.get("duration"))
        self.uploader = data.get("uploader") or data.get("artist") or "Unknown Artist"
        now = time.time()
        extracted_at = data.get("_extracted_at") if isinstance(data, dict) else None
        self.extracted_at = float(extracted_at) if extracted_at is not None else now
        self.data["_extracted_at"] = self.extracted_at
        self.added_msg = None
        self.source = None
