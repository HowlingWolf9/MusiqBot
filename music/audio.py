import asyncio
import logging
import re
import discord

from music.config import ffmpeg_options, ytdl

logger = logging.getLogger(__name__)


def select_best_search_entry(entries):
    if not entries:
        return {}

    valid_entries = [e for e in entries if isinstance(e, dict)]
    if not valid_entries:
        return {}

    def score_entry(entry):
        score = 0
        title = (entry.get("title") or "").lower()
        uploader = (entry.get("uploader") or entry.get("channel") or "").lower()

        # Topic channels are YouTube Music official studio releases
        if uploader.endswith("- topic") or "topic" in uploader:
            score += 10

        if "official audio" in title or "(audio)" in title or "[audio]" in title:
            score += 5
        elif "lyric" in title or "lyrics" in title:
            score += 3

        # Penalize music videos which often have intro talking/skits
        if "music video" in title or "official video" in title or "(video)" in title:
            score -= 5

        return score

    sorted_entries = sorted(
        enumerate(valid_entries), key=lambda x: (score_entry(x[1]), -x[0]), reverse=True
    )
    return sorted_entries[0][1]


class SilencePrimer(discord.AudioSource):
    """Prepends a short burst of silence before real audio.

    Discord drops packets sent before a cold voice session's media path is
    fully live (fresh join, or resume after manual stop/idle). Priming the
    pipe with disposable silence protects the opening seconds of the track.
    """

    FRAME_SIZE = 3840  # 20ms of 48kHz s16le stereo PCM

    def __init__(self, source, frames: int = 25):
        self.source = source
        self.remaining = frames

    def read(self):
        if self.remaining > 0:
            self.remaining -= 1
            return b"\x00" * self.FRAME_SIZE
        return self.source.read()

    def is_opus(self):
        return False

    def cleanup(self):
        return self.source.cleanup()


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title") or "Unknown Track"
        self.url = data.get("webpage_url") or data.get("url")
        self.duration = data.get("duration") or 0
        self.thumbnail = data.get("thumbnail")
        self.uploader = data.get("uploader") or data.get("artist") or "Unknown Artist"
        self.requester = None

    @classmethod
    async def extract_info(cls, url, *, loop=None):
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
        is_direct_url = url.startswith("http://") or url.startswith("https://")
        if not is_direct_url:
            if not re.match(r"^ytsearch\d*:", url, re.IGNORECASE):
                url = f"ytsearch5:{url}"
            elif url.startswith("ytsearch:"):
                url = url.replace("ytsearch:", "ytsearch5:", 1)

        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=False)
        )
        if "entries" in data and data["entries"]:
            if is_direct_url:
                data = data["entries"][0]
            else:
                data = select_best_search_entry(data["entries"])
        return data

    @classmethod
    async def create_source(cls, url_or_data, *, loop=None, seek=None, prime_frames=0):
        if isinstance(url_or_data, dict):
            data = url_or_data
        else:
            data = await cls.extract_info(url_or_data, loop=loop)

        if "url" not in data:
            data = await cls.extract_info(
                data.get("webpage_url", url_or_data), loop=loop
            )

        opts = ffmpeg_options.copy()
        if seek is not None:
            opts["before_options"] = f"-ss {seek} " + opts.get("before_options", "")

        # Reuse yt-dlp's client-matched User-Agent so googlevideo doesn't 403
        # ffmpeg's default "Lavf" agent on stricter stream URLs.
        user_agent = (data.get("http_headers") or {}).get("User-Agent")
        if user_agent:
            # Strip characters that would break shlex parsing of before_options
            user_agent = user_agent.replace("'", "").replace("\\", "")
        if user_agent:
            opts["before_options"] = (
                f"-user_agent '{user_agent}' " + opts["before_options"]
            )

        ffmpeg_audio = discord.FFmpegPCMAudio(data["url"], **opts)
        if prime_frames > 0:
            ffmpeg_audio = SilencePrimer(ffmpeg_audio, prime_frames)
        return cls(ffmpeg_audio, data=data)
