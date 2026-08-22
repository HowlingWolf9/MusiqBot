import asyncio
import logging
import random
import re
import time
import discord
import yt_dlp

from music.audio import YTDLSource
from music.models import LoopMode, Song
from music.utils import create_progress_bar, delete_message_safe
from music.views.player_view import PlayerView

logger = logging.getLogger(__name__)

# Playback starting after this much idle time gets primed with silence so
# Discord doesn't drop the track's opening seconds on a cold voice session.
PRIME_IDLE_SECONDS = 1.0
PRIMER_FRAMES = 25  # ~500ms of 20ms frames


NON_MUSIC_PATTERNS = [
    r"\breaction\b",
    r"\breacting\b",
    r"\breview\b",
    r"\btier list\b",
    r"\btutorial\b",
    r"\bhow to\b",
    r"\bgameplay\b",
    r"\bwalkthrough\b",
    r"\bpodcast\b",
    r"\binterview\b",
    r"\bvlog\b",
    r"\bunboxing\b",
    r"\banalysis\b",
    r"\bexplained\b",
    r"\bdocumentary\b",
    r"\bparody\b",
    r"\bcompilation\b",
    r"\b10 hour",
    r"\b1 hour loop\b",
    r"\bepisode\b",
    r"\btrailer\b",
    r"\bteaser\b",
    r"\blivestream\b",
    r"\bhighlights\b",
    r"\bfull album\b",
    r"\bbehind the scenes\b",
    r"\bbts of\b",
    r"\bmaking of\b",
    r"\bplaythrough\b",
    r"\bstream vod\b",
]


def extract_video_id(url: str | None) -> str | None:
    """Extract YouTube video ID from a URL or raw ID string."""
    if not url:
        return None
    match = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([a-zA-Z0-9_-]+)", str(url))
    if match:
        return match.group(1)
    if re.match(r"^[a-zA-Z0-9_-]+$", str(url)) and not str(url).startswith("http"):
        return str(url)
    return None


def is_valid_music_entry(entry: dict) -> bool:
    """
    Validate that a metadata entry represents an actual music song/track
    and not a random YouTube video (reaction, vlog, tutorial, podcast, etc.).
    """
    if not isinstance(entry, dict):
        return False

    title = (entry.get("title") or "").strip()
    if title.lower() in ("unknown track", "unknown title", "private video", "deleted video"):
        return False

    duration = entry.get("duration")
    if duration is not None:
        if duration < 60 or duration > 900:
            return False

    if title:
        for pat in NON_MUSIC_PATTERNS:
            if re.search(pat, title, re.IGNORECASE):
                return False

    return True


def normalize_title(title: str | None) -> str:
    """Normalize a song title for similarity / deduplication comparison."""
    if not title:
        return ""
    t = re.sub(r"[\(\[].*?[\)\]]", "", str(title))
    t = re.sub(r"[^a-zA-Z0-9\s]", "", t).lower()
    return re.sub(r"\s+", " ", t).strip()


class MusicQueue(asyncio.Queue):
    """
    Custom queue ensuring user-requested songs always take priority ahead of
    autoqueue (autoplay) songs, while maintaining strict FIFO order among user songs.
    """

    def _put(self, item):
        # If item is marked as autoplay, append to the end of the queue
        if getattr(item, "is_autoplay", False):
            self._queue.append(item)
        else:
            # User song: place after preceding user songs, but before any autoplay songs
            insert_idx = len(self._queue)
            for idx, queued_item in enumerate(self._queue):
                if getattr(queued_item, "is_autoplay", False):
                    insert_idx = idx
                    break
            self._queue.insert(insert_idx, item)

    def insert_front(self, item):
        """Insert an item at the very front of the queue (e.g. for single-track loop)."""
        if self.empty():
            self.put_nowait(item)
        else:
            self._queue.appendleft(item)
            self._unfinished_tasks += 1
            self._finished.clear()


class MusicPlayer:
    def __init__(self, interaction: discord.Interaction, cog):
        self.bot = interaction.client
        self.guild = interaction.guild
        self.channel = interaction.channel
        self.cog = cog

        self.queue = MusicQueue()
        self.next = asyncio.Event()

        self.current = None
        self.current_start_time = None
        self.last_active_ts = 0.0
        self.last_play_start = None
        self.volume = 0.5
        self.autoplay = False
        self.loop_mode = LoopMode.OFF
        self.history = []
        self._prefetching = False
        self.is_loading = False
        self.seek_position = None
        self.is_seeking = False
        self.manual_skip = False
        self.manual_stop = False
        self.np_msg = None
        self.queue_msg = None

        self.player_task = self.bot.loop.create_task(self.player_loop())

    @property
    def is_busy(self) -> bool:
        return self.current is not None or not self.queue.empty() or self.is_loading

    def _should_prime(self) -> bool:
        return (time.time() - self.last_active_ts) > PRIME_IDLE_SECONDS

    def _is_instant_fail(self) -> bool:
        """True when playback ended almost immediately after starting.

        Indicates the stream never actually played (403 / instant EOF).
        Tracks with a known short duration are exempt to avoid flagging
        legitimately tiny clips as failures.
        """
        if self.last_play_start is None or self.current is None:
            return False
        if time.time() - self.last_play_start >= 1.5:
            return False
        duration = self.current.duration or 0
        return duration == 0 or duration > 2

    def get_queue_items(self) -> list:
        return list(self.queue._queue)

    def peek_next(self):
        if not self.queue.empty():
            return self.queue._queue[0]
        return None

    def insert_front(self, item):
        self.queue.insert_front(item)

    def shuffle(self):
        items = list(self.queue._queue)
        user_items = [s for s in items if not getattr(s, "is_autoplay", False)]
        auto_items = [s for s in items if getattr(s, "is_autoplay", False)]
        random.shuffle(user_items)
        random.shuffle(auto_items)
        self.queue._queue.clear()
        self.queue._queue.extend(user_items + auto_items)

    def remove(self, index: int):
        if 0 <= index < len(self.queue._queue):
            item = self.queue._queue[index]
            del self.queue._queue[index]
            if item.added_msg:
                asyncio.create_task(delete_message_safe(item.added_msg))
            return item
        raise IndexError("Queue index out of range")

    def move(self, from_index: int, to_index: int):
        queue_list = list(self.queue._queue)
        if not (0 <= from_index < len(queue_list) and 0 <= to_index < len(queue_list)):
            raise IndexError("Index out of bounds")
        item = queue_list.pop(from_index)
        queue_list.insert(to_index, item)
        self.queue._queue.clear()
        self.queue._queue.extend(queue_list)
        return item

    def clear_queue(self):
        while not self.queue.empty():
            try:
                item = self.queue.get_nowait()
                if item.added_msg:
                    asyncio.create_task(delete_message_safe(item.added_msg))
            except Exception:
                pass

    async def delete_message_safe(self, msg):
        await delete_message_safe(msg)

    async def get_related_video(self, url: str) -> str | None:
        """
        Fetch a relevant, music-only recommendation related to the given track.
        Uses candidate pooling and weighted selection to provide variety while strictly
        filtering out non-music videos, recently played tracks, and queued tracks.
        """
        seed_id = extract_video_id(url)

        # Collect excluded IDs, URLs, and titles (current track, history, and queue)
        excluded_ids = set()
        excluded_titles = set()

        if seed_id:
            excluded_ids.add(seed_id)

        for h_url in self.history:
            h_id = extract_video_id(h_url)
            if h_id:
                excluded_ids.add(h_id)
            if h_url:
                excluded_ids.add(h_url)

        if self.current:
            c_url = getattr(self.current, "url", None)
            c_id = extract_video_id(c_url) or (
                self.current.data.get("id") if hasattr(self.current, "data") else None
            )
            if c_id:
                excluded_ids.add(c_id)
            if c_url:
                excluded_ids.add(c_url)
            if self.current.title:
                norm_c = normalize_title(self.current.title)
                if norm_c:
                    excluded_titles.add(norm_c)

        for item in self.queue._queue:
            q_url = getattr(item, "url", None)
            q_id = extract_video_id(q_url) or (
                item.data.get("id") if hasattr(item, "data") else None
            )
            if q_id:
                excluded_ids.add(q_id)
            if q_url:
                excluded_ids.add(q_url)
            if item.title:
                norm_q = normalize_title(item.title)
                if norm_q:
                    excluded_titles.add(norm_q)

        valid_candidates = []

        if seed_id:
            mix_url = f"https://www.youtube.com/watch?v={seed_id}&list=RD{seed_id}"
            ydl_opts = {"extract_flat": True, "quiet": True}

            def extract():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl_inst:
                    return ydl_inst.extract_info(mix_url, download=False)

            try:
                data = await self.bot.loop.run_in_executor(None, extract)
                if data and "entries" in data:
                    for entry in data["entries"]:
                        if not is_valid_music_entry(entry):
                            continue

                        entry_id = (
                            entry.get("id")
                            or extract_video_id(
                                entry.get("url") or entry.get("webpage_url")
                            )
                            or entry.get("url")
                            or entry.get("webpage_url")
                        )
                        entry_url = (
                            entry.get("webpage_url")
                            or entry.get("url")
                            or (
                                f"https://www.youtube.com/watch?v={entry_id}"
                                if entry_id
                                else None
                            )
                        )

                        if not entry_url or not entry_id:
                            continue

                        if entry_id in excluded_ids or entry_url in excluded_ids:
                            continue

                        entry_title = entry.get("title")
                        if entry_title:
                            norm_entry = normalize_title(entry_title)
                            if norm_entry and norm_entry in excluded_titles:
                                continue

                        valid_candidates.append(entry_url)
                        if len(valid_candidates) >= 20:
                            break
            except Exception as e:
                logger.error(f"Error fetching related music mix: {e}", exc_info=True)

        if valid_candidates:
            # Weighted random selection from top 10 candidates to ensure variety
            top_pool = valid_candidates[:10]
            weights = [len(top_pool) - i for i in range(len(top_pool))]
            return random.choices(top_pool, weights=weights, k=1)[0]

        # Music-focused fallback search if YouTube mix is exhausted or unavailable
        try:
            query = None
            if self.current and self.current.title:
                query = f"{self.current.title} audio"
            elif (
                self.current
                and self.current.uploader
                and self.current.uploader.lower() not in ("unknown artist", "youtube")
            ):
                clean_artist = re.sub(
                    r"\s*-\s*Topic|\s*VEVO", "", self.current.uploader, flags=re.IGNORECASE
                ).strip()
                if clean_artist:
                    query = f"{clean_artist} audio"
            elif self.history:
                query = "top music audio"

            if query:
                fallback_data = await YTDLSource.extract_info(
                    f"ytsearch5:{query}", loop=self.bot.loop
                )
                if fallback_data:
                    res_url = fallback_data.get("webpage_url") or fallback_data.get("url")
                    if (
                        res_url
                        and res_url not in excluded_ids
                        and res_url not in self.history
                    ):
                        return res_url
        except Exception as e:
            logger.error(f"Error in autoplay fallback search: {e}", exc_info=True)

        return None

    async def prefetch_autoplay(self):
        if self._prefetching:
            return
        self._prefetching = True
        try:
            has_auto_song = any(
                getattr(s, "is_autoplay", False) for s in self.queue._queue
            )
            if not has_auto_song and self.autoplay and self.history:
                next_url = await self.get_related_video(self.history[-1])
                if next_url and self.autoplay and self.history:
                    data = await YTDLSource.extract_info(next_url, loop=self.bot.loop)
                    if self.autoplay and self.history:
                        song = Song(data, self.guild.me, is_autoplay=True)
                        embed = discord.Embed(
                            title="Added to Queue",
                            description=f"[{song.title}]({song.url})",
                            color=discord.Color.blue(),
                        )
                        embed.set_footer(
                            text=f"Added by {song.requester.display_name} (Autoplay)",
                            icon_url=song.requester.display_avatar.url,
                        )
                        msg = await self.channel.send(embed=embed)
                        song.added_msg = msg
                        self.cog.track_message(self.guild.id, msg)
                        await self.queue.put(song)
        except Exception as e:
            logger.error(f"Error prefetching autoplay: {e}", exc_info=True)
        finally:
            self._prefetching = False

    def build_np_embed(self) -> discord.Embed:
        if not self.current:
            return discord.Embed(
                title="Now Playing",
                description="Nothing is playing right now.",
                color=discord.Color.red(),
            )

        elapsed = (
            int(time.time() - self.current_start_time)
            if self.current_start_time
            else 0
        )
        duration = self.current.duration or 0

        mins_el, secs_el = divmod(elapsed, 60)
        mins_dur, secs_dur = divmod(duration, 60) if duration else (0, 0)
        time_str = (
            f"`{mins_el:02d}:{secs_el:02d} / {mins_dur:02d}:{secs_dur:02d}`"
            if duration
            else f"`{mins_el:02d}:{secs_el:02d}`"
        )

        bar = create_progress_bar(elapsed, duration)

        embed = discord.Embed(
            title="🎶 Now Playing",
            description=f"[{self.current.title}]({self.current.url})\n\n{bar}\n{time_str}",
            color=discord.Color.green(),
        )
        if self.current.thumbnail:
            embed.set_thumbnail(url=self.current.thumbnail)

        embed.add_field(
            name="Requested by", value=self.current.requester.mention, inline=True
        )
        if self.current.uploader:
            embed.add_field(
                name="Artist / Channel", value=self.current.uploader, inline=True
            )

        loop_status = self.loop_mode.value.capitalize()
        embed.add_field(name="Loop Mode", value=f"`{loop_status}`", inline=True)

        next_song = self.peek_next()
        if next_song:
            embed.set_footer(text=f"Up Next: {next_song.title[:60]}")
        else:
            embed.set_footer(text="Queue ends after this track")

        return embed

    async def player_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            self.next.clear()

            seek = None
            if self.is_seeking and self.current:
                seek = self.seek_position
                self.seek_position = None
                self.is_seeking = False
                song = self.current
            else:
                self.seek_position = None
                self.is_seeking = False
                has_auto_song = any(
                    getattr(s, "is_autoplay", False) for s in self.queue._queue
                )
                if self.autoplay and self.history and not has_auto_song:
                    if not self._prefetching:
                        self.bot.loop.create_task(self.prefetch_autoplay())

                try:
                    # Wait 5 minutes for the next song before disconnecting
                    song = await asyncio.wait_for(self.queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    if await delete_message_safe(self.np_msg):
                        self.cog.untrack_message(self.guild.id, self.np_msg)
                    self.np_msg = None
                    await self.delete_message_safe(self.queue_msg)
                    self.queue_msg = None
                    return self.destroy(self.guild)

            # Re-fetch stream URL if older than 1 hour or seeking
            self.is_loading = True
            try:
                prime_frames = PRIMER_FRAMES if self._should_prime() else 0
                if (
                    seek is None
                    and hasattr(song, "extracted_at")
                    and time.time() - song.extracted_at < 3600
                    and song.data.get("url")
                ):
                    source = await YTDLSource.create_source(
                        song.data, loop=self.bot.loop, seek=seek,
                        prime_frames=prime_frames,
                    )
                else:
                    source = await YTDLSource.create_source(
                        song.url, loop=self.bot.loop, seek=seek,
                        prime_frames=prime_frames,
                    )
            except Exception as e:
                logger.error(f"Error creating audio source: {e}", exc_info=True)
                if hasattr(song, "added_msg") and song.added_msg:
                    await self.delete_message_safe(song.added_msg)
                try:
                    await self.channel.send(
                        f"❌ Failed to play **{song.title}**: Track unavailable or restricted."
                    )
                except Exception:
                    pass
                self.current = None
                self.bot.loop.call_soon_threadsafe(self.next.set)
                continue
            finally:
                self.is_loading = False

            self.current = song
            self.current_start_time = time.time() - (seek or 0)
            if song.url and song.url not in self.history:
                self.history.append(song.url)
            if len(self.history) > 100:
                self.history.pop(0)

            source.volume = self.volume
            self.current.source = source

            # Wait briefly if voice client is connecting or reconnecting
            for _ in range(10):
                if self.guild.voice_client and self.guild.voice_client.is_connected():
                    break
                await asyncio.sleep(0.5)

            if not self.guild.voice_client or not self.guild.voice_client.is_connected():
                self.current = None
                return self.destroy(self.guild)

            # Reset skip/stop flags right before playback begins
            self.manual_skip = False
            self.manual_stop = False
            self.last_play_start = time.time()

            try:
                self.guild.voice_client.play(
                    source,
                    after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set),
                )
            except Exception as e:
                logger.error(f"Error starting voice playback: {e}", exc_info=True)
                try:
                    await self.channel.send(
                        f"❌ Failed to play **{song.title}**: Voice playback error."
                    )
                except Exception:
                    pass
                self.current = None
                self.bot.loop.call_soon_threadsafe(self.next.set)
                continue

            if hasattr(song, "added_msg") and song.added_msg:
                if await delete_message_safe(song.added_msg):
                    self.cog.untrack_message(self.guild.id, song.added_msg)
                song.added_msg = None

            if await delete_message_safe(self.np_msg):
                self.cog.untrack_message(self.guild.id, self.np_msg)
            self.np_msg = None

            embed = self.build_np_embed()
            view = PlayerView(self.cog, self)
            try:
                self.np_msg = await self.channel.send(embed=embed, view=view)
                self.cog.track_message(self.guild.id, self.np_msg)
            except Exception:
                pass

            has_auto_song = any(
                getattr(s, "is_autoplay", False) for s in self.queue._queue
            )
            if self.autoplay and self.history and not has_auto_song:
                self.bot.loop.create_task(self.prefetch_autoplay())

            await self.next.wait()
            self.last_active_ts = time.time()

            # Handle seeking continuity: do not clear current track
            if self.is_seeking:
                continue

            # Handle manual stop: do not re-queue
            if self.manual_stop:
                self.current = None
                self.current_start_time = None
                continue

            # Handle manual skip:
            if self.manual_skip:
                if self.loop_mode == LoopMode.QUEUE:
                    new_song = Song(
                        self.current.data,
                        self.current.requester,
                        is_autoplay=getattr(self.current, "is_autoplay", False),
                    )
                    await self.queue.put(new_song)
                self.current = None
                self.current_start_time = None
                continue

            # Natural track completion - Handle Loop Modes
            if self.current:
                # A track that "finishes" almost instantly never actually played;
                # notify instead of silently skipping (e.g. stream 403 / EOF) and
                # bypass loop re-queueing so dead tracks can't wedge loop mode.
                if self._is_instant_fail():
                    logger.warning(
                        f"Track ended instantly, likely unavailable: {self.current.title}"
                    )
                    try:
                        await self.channel.send(
                            f"⚠️ **{self.current.title}** is unavailable — skipping."
                        )
                    except Exception:
                        pass
                    self.current = None
                    self.current_start_time = None
                    continue

                if self.loop_mode == LoopMode.TRACK:
                    new_song = Song(
                        self.current.data,
                        self.current.requester,
                        is_autoplay=getattr(self.current, "is_autoplay", False),
                    )
                    self.insert_front(new_song)
                elif self.loop_mode == LoopMode.QUEUE:
                    new_song = Song(
                        self.current.data,
                        self.current.requester,
                        is_autoplay=getattr(self.current, "is_autoplay", False),
                    )
                    await self.queue.put(new_song)

            self.current = None
            self.current_start_time = None

    def destroy(self, guild):
        return self.bot.loop.create_task(self.cog.cleanup(guild))
