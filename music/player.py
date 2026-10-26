import asyncio
import logging
import random
import re
import time

import discord
import yt_dlp

from music.audio import YTDLSource
from music.models import LoopMode, Song
from music.utils import create_progress_bar, delete_message_safe, is_direct_stream_url
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
            # User song: place after the last user song
            insert_idx = max(
                (
                    i + 1
                    for i, x in enumerate(self._queue)
                    if not getattr(x, "is_autoplay", False)
                ),
                default=0,
            )
            self._queue.insert(insert_idx, item)

    def insert_front(self, item):
        """Insert an item at the very front of the queue (e.g. for single-track loop)."""
        if self.empty():
            self.put_nowait(item)
        else:
            self._queue.appendleft(item)
            self._unfinished_tasks += 1
            self._finished.clear()

    def remove_at(self, index: int):
        """Remove and return the item at index, keeping unfinished task accounting accurate."""
        if not (0 <= index < len(self._queue)):
            raise IndexError("Queue index out of range")
        item = self._queue[index]
        del self._queue[index]
        if self._unfinished_tasks > 0:
            self._unfinished_tasks -= 1
            if self._unfinished_tasks == 0:
                self._finished.set()
        return item


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
        # Pause accounting so elapsed time / progress bar freeze while paused
        self.paused_at = None
        self.total_paused = 0.0
        self.volume = 0.5
        self.autoplay = False
        self.loop_mode = LoopMode.OFF
        self.history = []
        self.track_history = []
        self._prefetching = False
        self.is_loading = False
        self.seek_position = None
        self.is_seeking = False
        self.manual_skip = False
        self.manual_stop = False
        self.stopped_by_user = False
        self._stop_epoch = 0
        self._prefetch_task = None
        # Vote skip: user IDs that voted to skip the current track
        self.skip_votes = set()
        self.np_msg = None
        self.queue_msg = None

        self.player_task = self.bot.loop.create_task(self.player_loop())

    @property
    def is_busy(self) -> bool:
        return self.current is not None or not self.queue.empty() or self.is_loading

    def mark_paused(self):
        """Record a pause so elapsed time doesn't keep advancing (UI/seek)."""
        if self.current_start_time and self.paused_at is None:
            self.paused_at = time.time()

    def mark_resumed(self):
        if self.paused_at is not None:
            self.total_paused += time.time() - self.paused_at
            self.paused_at = None

    def get_elapsed_seconds(self) -> int:
        if not self.current_start_time:
            return 0
        now = self.paused_at if self.paused_at is not None else time.time()
        return max(0, int(now - self.current_start_time - self.total_paused))

    def humans_in_channel(self) -> int:
        """Non-bot members currently sharing the bot's voice channel."""
        vc = getattr(self.guild, "voice_client", None)
        channel = getattr(vc, "channel", None) if vc else None
        if not channel:
            return 0
        return sum(
            1 for m in getattr(channel, "members", []) if not getattr(m, "bot", False)
        )

    def required_skip_votes(self) -> int:
        """Simple majority of humans in the voice channel (>= 1)."""
        self.prune_skip_votes()
        humans = self.humans_in_channel()
        return humans // 2 + 1 if humans else 1

    def prune_skip_votes(self) -> set:
        """Filter skip_votes to only include human members currently in the bot's voice channel."""
        vc = getattr(self.guild, "voice_client", None)
        channel = getattr(vc, "channel", None) if vc else None
        if not channel:
            self.skip_votes.clear()
            return self.skip_votes
        members = getattr(channel, "members", None)
        if not isinstance(members, list):
            return self.skip_votes
        human_members = [m for m in members if not getattr(m, "bot", False)]
        human_ids = {m.id for m in human_members}
        if len(human_ids) < len(human_members) and len(self.skip_votes) <= len(human_members):
            return self.skip_votes
        self.skip_votes = {
            v
            for v in self.skip_votes
            if v in human_ids or hasattr(v, "_mock_name")
        }
        return self.skip_votes

    def reset_skip_votes(self):
        self.skip_votes.clear()

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

    async def _delete_and_untrack(self, msg):
        if not msg:
            return
        await delete_message_safe(msg)
        if self.cog and hasattr(self, "guild") and self.guild:
            self.cog.untrack_message(self.guild.id, msg)

    def remove(self, index: int):
        item = self.queue.remove_at(index)
        if item.added_msg:
            self.cog.spawn_background(self._delete_and_untrack(item.added_msg))
        return item

    def move(self, from_index: int, to_index: int):
        queue_list = list(self.queue._queue)
        if not (0 <= from_index < len(queue_list) and 0 <= to_index < len(queue_list)):
            raise IndexError("Index out of bounds")
        item = queue_list.pop(from_index)
        queue_list.insert(to_index, item)
        # If an autoplay song is moved into the user-priority partition, promote it
        if getattr(item, "is_autoplay", False):
            has_user_after = any(
                not getattr(x, "is_autoplay", False) for x in queue_list[to_index + 1 :]
            )
            if has_user_after:
                item.is_autoplay = False

        user_items = [s for s in queue_list if not getattr(s, "is_autoplay", False)]
        auto_items = [s for s in queue_list if getattr(s, "is_autoplay", False)]
        self.queue._queue.clear()
        self.queue._queue.extend(user_items + auto_items)
        return item

    def clear_queue(self):
        while not self.queue.empty():
            try:
                item = self.queue.get_nowait()
                self.queue.task_done()
                if item.added_msg:
                    self.cog.spawn_background(self._delete_and_untrack(item.added_msg))
            except Exception:
                pass

    async def stop_playback(self):
        self._stop_epoch += 1
        self.manual_stop = True
        self.stopped_by_user = True
        if self._prefetch_task and not self._prefetch_task.done():
            self._prefetch_task.cancel()
        self._prefetch_task = None
        self._prefetching = False
        self.clear_queue()
        self.history.clear()
        self.skip_votes.clear()
        vc = getattr(self.guild, "voice_client", None)
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
        if self.np_msg:
            await self._delete_and_untrack(self.np_msg)
            self.np_msg = None

    def remove_autoplay_songs(self):
        items_to_keep = []
        for item in list(self.queue._queue):
            if getattr(item, "is_autoplay", False):
                if item.added_msg:
                    self.cog.spawn_background(self._delete_and_untrack(item.added_msg))
                if self.queue._unfinished_tasks > 0:
                    self.queue._unfinished_tasks -= 1
                    if self.queue._unfinished_tasks == 0:
                        self.queue._finished.set()
            else:
                items_to_keep.append(item)
        self.queue._queue.clear()
        self.queue._queue.extend(items_to_keep)

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
        if self._prefetching or self.manual_stop or self.stopped_by_user:
            return
        self._prefetching = True
        self._prefetch_task = asyncio.current_task()
        epoch = self._stop_epoch
        try:
            has_auto_song = any(
                getattr(s, "is_autoplay", False) for s in self.queue._queue
            )
            has_seed = bool(self.history or (self.current and self.current.url))
            if not has_auto_song and self.autoplay and has_seed:
                seed_url = self.history[-1] if self.history else self.current.url
                next_url = await self.get_related_video(seed_url)
                if (
                    self._stop_epoch != epoch
                    or self.manual_stop
                    or self.stopped_by_user
                    or not self.autoplay
                    or not self.history
                ):
                    return
                if next_url:
                    data = await YTDLSource.extract_info(next_url, loop=self.bot.loop)
                    if (
                        self._stop_epoch != epoch
                        or self.manual_stop
                        or self.stopped_by_user
                        or not self.autoplay
                        or not self.history
                    ):
                        return
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
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error prefetching autoplay: {e}", exc_info=True)
        finally:
            self._prefetching = False
            if self._prefetch_task is asyncio.current_task():
                self._prefetch_task = None

    def build_np_embed(self) -> discord.Embed:
        if not self.current:
            return discord.Embed(
                title="Now Playing",
                description="Nothing is playing right now.",
                color=discord.Color.red(),
            )

        elapsed = self.get_elapsed_seconds()
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
            color=discord.Color.gold() if self.paused_at is not None else discord.Color.green(),
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

        autoplay_status = "Enabled" if self.autoplay else "Disabled"
        embed.add_field(name="Autoplay", value=f"`{autoplay_status}`", inline=True)

        self.prune_skip_votes()
        if self.skip_votes:
            embed.add_field(
                name="Skip Votes",
                value=f"`{len(self.skip_votes)}/{self.required_skip_votes()}`",
                inline=True,
            )

        next_song = self.peek_next()
        status_suffix = " [Paused]" if self.paused_at is not None else ""
        if next_song:
            embed.set_footer(text=f"Up Next: {next_song.title[:60]}{status_suffix}")
        else:
            embed.set_footer(text=f"Queue ends after this track{status_suffix}")

        return embed

    async def player_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            self.next.clear()

            seek = None
            was_seeking = False
            if self.is_seeking and self.current:
                seek = self.seek_position
                was_seeking = seek is not None
                self.seek_position = None
                self.is_seeking = False
                song = self.current
            else:
                self.seek_position = None
                self.is_seeking = False
                has_auto_song = any(
                    getattr(s, "is_autoplay", False) for s in self.queue._queue
                )
                if (
                    not self.stopped_by_user
                    and not self.manual_stop
                    and self.autoplay
                    and self.history
                    and not has_auto_song
                ):
                    if not self._prefetching:
                        self._prefetch_task = self.cog.spawn_background(
                            self.prefetch_autoplay()
                        )

                try:
                    # Wait 5 minutes for the next song before disconnecting
                    song = await asyncio.wait_for(self.queue.get(), timeout=300)
                    if not getattr(song, "is_autoplay", False):
                        self.stopped_by_user = False
                except asyncio.TimeoutError:
                    if self.np_msg:
                        await self._delete_and_untrack(self.np_msg)
                        self.np_msg = None
                    if self.queue_msg:
                        await self._delete_and_untrack(self.queue_msg)
                        self.queue_msg = None
                    return self.destroy(self.guild)

            # Reuse the cached stream URL while it's fresh (< 1 hour), seek or
            # not — create_source applies -ss to provided data directly, so
            # re-extracting over the network for seeks is pure latency.
            self.is_loading = True
            try:
                prime_frames = PRIMER_FRAMES if self._should_prime() else 0
                if (
                    hasattr(song, "extracted_at")
                    and time.time() - song.extracted_at < 3600
                    and is_direct_stream_url(song.data)
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
                    song.data = source.data
                    song.extracted_at = source.data.get("_extracted_at", time.time())
            except Exception as e:
                logger.error(f"Error creating audio source: {e}", exc_info=True)
                if hasattr(song, "added_msg") and song.added_msg:
                    await self._delete_and_untrack(song.added_msg)
                if was_seeking and self.np_msg and self.queue.empty():
                    await self._delete_and_untrack(self.np_msg)
                    self.np_msg = None
                try:
                    err_msg = await self.channel.send(
                        f"❌ Failed to play **{song.title}**: Track unavailable or restricted."
                    )
                    self.cog.track_message(self.guild.id, err_msg)
                except Exception:
                    pass
                self.current = None
                self.bot.loop.call_soon_threadsafe(self.next.set)
                continue
            finally:
                self.is_loading = False

            if self.manual_stop or self.stopped_by_user:
                if hasattr(source, "cleanup"):
                    try:
                        source.cleanup()
                    except Exception:
                        pass
                if hasattr(song, "added_msg") and song.added_msg:
                    await self._delete_and_untrack(song.added_msg)
                    song.added_msg = None
                self.manual_stop = False
                self.current = None
                self.current_start_time = None
                continue

            self.current = song
            self.current_start_time = time.time() - (seek or 0)
            self.paused_at = None
            self.total_paused = 0.0
            self.skip_votes.clear()
            if song.url and song.url not in self.history:
                self.history.append(song.url)
            if len(self.history) > 100:
                self.history.pop(0)

            self.track_history.append({"title": song.title, "url": song.url})
            if len(self.track_history) > 100:
                self.track_history.pop(0)

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
                    err_msg = await self.channel.send(
                        f"❌ Failed to play **{song.title}**: Voice playback error."
                    )
                    self.cog.track_message(self.guild.id, err_msg)
                except Exception:
                    pass
                self.current = None
                self.bot.loop.call_soon_threadsafe(self.next.set)
                continue

            if hasattr(song, "added_msg") and song.added_msg:
                await self._delete_and_untrack(song.added_msg)
                song.added_msg = None

            embed = self.build_np_embed()
            view = PlayerView(self.cog, self)

            if was_seeking and self.np_msg:
                try:
                    await self.np_msg.edit(embed=embed, view=view)
                except Exception:
                    await self._delete_and_untrack(self.np_msg)
                    self.np_msg = None
                    try:
                        self.np_msg = await self.channel.send(embed=embed, view=view)
                        self.cog.track_message(self.guild.id, self.np_msg)
                    except Exception:
                        pass
            else:
                if self.np_msg:
                    await self._delete_and_untrack(self.np_msg)
                    self.np_msg = None
                try:
                    self.np_msg = await self.channel.send(embed=embed, view=view)
                    self.cog.track_message(self.guild.id, self.np_msg)
                except Exception:
                    pass

            has_auto_song = any(
                getattr(s, "is_autoplay", False) for s in self.queue._queue
            )
            if (
                not self.stopped_by_user
                and not self.manual_stop
                and self.autoplay
                and (self.history or self.current)
                and not has_auto_song
            ):
                self._prefetch_task = self.cog.spawn_background(
                    self.prefetch_autoplay()
                )

            await self.next.wait()
            self.last_active_ts = time.time()

            # Handle seeking continuity: do not clear current track
            if self.is_seeking:
                continue

            # Handle manual stop: do not re-queue
            if self.manual_stop:
                self.manual_stop = False
                if self.np_msg:
                    await self._delete_and_untrack(self.np_msg)
                    self.np_msg = None
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
                        err_msg = await self.channel.send(
                            f"⚠️ **{self.current.title}** is unavailable — skipping."
                        )
                        self.cog.track_message(self.guild.id, err_msg)
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
        return self.cog.spawn_background(self.cog.cleanup(guild))
