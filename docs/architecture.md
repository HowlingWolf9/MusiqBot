# Architecture

This document explains how MusiqBot works internally: the module layout, the
player state machine, queue semantics, autoplay, audio resolution, and the
message-lifecycle machinery that keeps channels clean.

```
music_bot.py                 Entry point: bot client, tree error filter,
                             single-instance lock, signal handling
└── music_cog.py             Extension shim re-exporting music.* symbols
    └── music/
        ├── config.py        yt-dlp + FFmpeg option sets (module-level singleton)
        ├── models.py        Song value object; LoopMode enum
        ├── utils.py         delete_message_safe / coerce_duration / progress bar
        ├── permissions.py   check_dj / check_dj_permission guards
        ├── audio.py         YTDLSource (PCMVolumeTransformer), search ranking,
        │                    SilencePrimer
        ├── player.py        MusicQueue (priority queue), MusicPlayer +
        │                    player_loop state machine, autoplay engine
        ├── services/
        │   ├── spotify.py       Spotify oEmbed → YouTube search query
        │   └── autocomplete.py  Google suggest API client with cache
        ├── views/
        │   ├── player_view.py   Persistent Now Playing buttons
        │   ├── queue_view.py    Paginated ephemeral queue browser
        │   └── search_view.py   Ephemeral 5-result dropdown
        └── cog.py           MusicCog: slash commands, event listeners,
                             settings/orphan persistence, cleanup logic
```

## Boot Sequence

1. `logging` configured; `AutocompleteErrorFilter` attached to the
   `discord.app_commands.tree` logger to silence expired-interaction noise.
2. `MusicBot.setup_hook()` loads the `music_cog` extension → `MusicCog`.
3. `MusicCog.cog_load()`:
   - runs `_sweep_orphans()` — deletes every UI message recorded in
     `music_orphans.json` by a previous process;
   - starts the `empty_channel_sweep` task (every minute).
4. On login (`on_ready`) the bot is ready to receive interactions. Slash
   commands must be registered once via the owner-only `!sync` prefix command.
5. `main` acquires `/tmp/musiqbot.lock` (`fcntl.flock`, non-blocking) so only
   one instance can run per host; registers SIGTERM/SIGINT handlers.

## MusicCog Responsibilities (`music/cog.py`)

- **Player registry** — `players: dict[guild_id, MusicPlayer]`; created lazily
  by `get_player()`, which also restores persisted `volume`/`autoplay` from
  settings. One player per guild at a time.
- **Settings persistence** — `music_settings.json`, written atomically via a
  temp file + `fsync` + `os.replace`. Mutated by `/volume`, `/autoplay`,
  `/setdjrole`. Writes are offloaded to an executor (`save_settings_async`).
- **Orphan message index** — every public UI message ("Added to Queue",
  Now Playing, queue view) is registered through `track_message()` and removed
  via `untrack_message()` when deliberately deleted. The on-disk index caps at
  50 records per guild and enables crash/reload cleanup.
- **Voice connection** — `ensure_voice_client()` serializes connects/moves per
  guild with an `asyncio.Lock`, moving the client if it is elsewhere or
  force-reconnecting after stale sessions. `verify_voice()` enforces
  in-guild + user-in-VC + same-channel-as-bot preconditions for commands.
- **Lifecycle listeners** — see [Automated Lifecycle](#automated-lifecycle).
- **Error handling** — `cog_app_command_error` converts
  `MissingPermissions`/`BotMissingPermissions` into friendly ephemeral
  messages and swallows-and-logs everything else after informing the user.

## Player State Machine (`music/player.py`)

Each `MusicPlayer` owns:

| Field | Purpose |
|---|---|
| `queue: MusicQueue` | Priority-aware FIFO (below) |
| `next: asyncio.Event` | Set from the audio thread when the current source ends |
| `current` / `current_start_time` | Active track + wall-clock start (seek-adjusted) |
| `history: list[str]` | Played URLs, capped at 100; seeds autoplay |
| `loop_mode`, `autoplay`, `volume` | Guild-facing knobs |
| `manual_skip` / `manual_stop` | Flags distinguishing intentional stops from natural completion |
| `is_seeking` / `seek_position` | Seek-in-place path |
| `is_loading`, `_prefetching` | Busy-state markers used by `/play` UX |
| `np_msg`, `queue_msg` | Tracked UI messages |
| `player_task` | The background loop task |

### `player_loop`

```
┌───────────────────────────────────────────────────────────────────┐
│ wait_until_ready                                                  │
│ ▼                                                                 │
│ ◄──────────────────── outer loop ────────────────┐                │
│ clear next event                                 │                │
│ seek pending? ──yes──► reuse current track ──────┤                │
│      │no                                         │                │
│      ▼                                           │                │
│ maybe spawn prefetch_autoplay()                  │                │
│ queue.get() with 300s timeout                    │                │
│      │timeout                                    │                │
│      └──► delete np/queue messages → destroy()   │                │
│      ▼                                           │                │
│ resolve source (reuse cached URL if <1h old,     │                │
│                 else re-extract; add -ss if seek;│                │
│                 prepend prime frames if cold)    │                │
│      │failure → notify channel, continue ────────┤                │
│      ▼                                           │                │
│ wait ≤5s for voice connect (else destroy)        │                │
│ record history (cap 100), reset skip/stop flags  │                │
│ voice_client.play(source, after=set next)        │                │
│ delete "loading" msg; post new Now Playing embed │                │
│ await next                                       │                │
│      ▼                                           │                │
│ is_seeking? ─────────────► continue (same track)─┤                │
│ manual_stop? ► clear current ────────────────────┤                │
│ manual_skip? ► (+requeue if Queue-loop) ─────────┤                │
│ natural end:                                     │                │
│   instant-fail? ► warn "unavailable", no requeue─┤                │
│   Loop.TRACK ► insert_front(clone)               │                │
│   Loop.QUEUE ► put(clone)                        │                │
│ clear current ───────────────────────────────────┘                │
└───────────────────────────────────────────────────────────────────┘
```

Key behaviors:

- **Idle timeout** — `asyncio.wait_for(queue.get(), 300)`; five minutes of
  nothing to play tears the player down via `cleanup()`.
- **Stream URL freshness** — yt-dlp metadata carries `extracted_at`; URLs
  younger than one hour are reused without a second network round-trip,
  otherwise the track is re-extracted (YouTube stream URLs expire).
- **Instant-fail detection** — if playback ends < 1.5 s after starting, and
  the track's known duration is either unknown (`0`) or greater than 2 s, the
  stream never actually played (403 / instant EOF); it's reported as
  unavailable and bypasses loop re-queueing so dead streams can't wedge loop
  mode. Genuinely short clips (≤ 2 s) are exempt from this heuristic.
- **Failure isolation** — a source that fails to resolve or start sends one
  channel notice and moves on to the next item instead of killing the loop.

### Queue priority model (`MusicQueue`)

A subclass of `asyncio.Queue` overriding `_put`:

- **User requests** are inserted *before* any queued autoplay picks, keeping
  strict FIFO among themselves — a fresh request always jumps ahead of the
  entire autoqueue block.
- **Autoplay items** (`song.is_autoplay`) always append at the end.
- `insert_front()` supports Track-loop restarts.

`shuffle()` shuffles the user block and autoplay block independently, then
concatenates them, preserving the invariant. This is covered by dedicated
tests (`test_user_song_priority_over_autoqueue`, etc.).

### Autoplay engine

When `autoplay` is enabled, history exists, and no autoplay song is queued:

1. `prefetch_autoplay()` runs as a side task while the current track plays,
   so the next pick is usually ready before the current one ends.
2. `get_related_video(seed_url)` builds a YouTube Mix URL
   (`watch?v=<id>&list=RD<id>`) and flat-extracts entries.
3. Entries are filtered by `is_valid_music_entry()`:
   - duration must be within **60–900 s** when known;
   - title must not match ~30 `NON_MUSIC_PATTERNS` regexes (reaction,
     tutorial, podcast, trailer, "10 hour", ...);
   - placeholder titles ("deleted video") are rejected.
4. Candidates already played (history), currently playing, or queued are
   excluded by video ID, URL, and normalized title (`normalize_title`
   strips punctuation/brackets for fuzzy dedupe).
5. Up to 20 valid candidates are pooled; one is chosen by weighted random
   from the top 10 (weight decays with rank) for variety.
6. Fallback when the Mix yields nothing: `ytsearch5:"<title> audio"` or
   `"<artist> audio"` (with `- Topic`/`VEVO` suffixes stripped).

## Audio Resolution Pipeline

### yt-dlp configuration (`config.py`)

```python
format: bestaudio/best, noplaylist, quiet, default_search: ytsearch,
source_address: 0.0.0.0,
extractor_args.youtube.player_client = ["android", "web"]
```

The pinned clients matter: newer defaults serve stream URLs that reject
FFmpeg's user agent or require PO tokens, producing instant 403s.

FFmpeg options add resilient reconnect flags (`-reconnect 1
-reconnect_streamed 1 -reconnect_delay_max 5 -nostdin`) and `-vn`.

### Search ranking (`audio.py::select_best_search_entry`)

Scored ordering of `ytsearch5` results:

| Signal | Score |
|---|---|
| Uploader is a "- Topic" / Topic channel (official studio release) | +10 |
| Title contains "official audio" / "(audio)" / "[audio]" | +5 |
| Title contains "lyric(s)" | +3 |
| Title contains "music video" / "official video" / "(video)" | −5 |

Ties break toward earlier results.

### `YTDLSource`

Wraps `discord.PCMVolumeTransformer` around `FFmpegPCMAudio`:

- `extract_info()` forces bare queries through `ytsearch5:` and applies the
  ranking above; direct URLs take the first entry.
- `create_source()` injects `-ss <seek>` for seeks and copies yt-dlp's
  negotiated `User-Agent` header into FFmpeg's `before_options`
  (quotes/backslashes stripped) so googlevideo doesn't 403 the default
  `Lavf` agent.

### `SilencePrimer`

Discord drops packets sent before a cold voice session's media path is live
(fresh join, resume after idle). When the player was idle > 1 s, the source is
wrapped to emit **25 frames × 3840 bytes** (~500 ms of 20 ms 48 kHz stereo PCM
silence) before real audio, protecting each track's opening seconds.

## Services

- **Spotify** (`services/spotify.py`) — public oEmbed endpoint
  (`open.spotify.com/oembed`) returns the track title; suffix boilerplate is
  stripped and the result becomes `ytsearch:<clean title>`. No API keys.
- **Autocomplete** (`services/autocomplete.py`) — Google suggest API
  (`suggestqueries.google.com/complete/search?client=firefox&ds=yt`) with a
  hard 0.4 s budget; results cached in a ≤500-entry dict (cleared wholesale
  when oversized). Expired interactions short-circuit; failures fall back to
  echoing the typed text.

## Permissions Model (`permissions.py`)

`check_dj()` accepts users who:

1. hold `manage_guild` or `administrator`,
2. have a role literally named `dj` (case-insensitive), or
3. have the custom role recorded by `/setdjrole` (matched by ID first, then
   case-insensitive name).

`check_dj_permission()` wraps it with an ephemeral rejection message. Every
disruptive command and every PlayerView button calls this guard.

## Interactive Views

| View | Lifetime | Notes |
|---|---|---|
| `PlayerView` | persistent (`timeout=None`) | 5 buttons; all DJ-gated; loop button reflects/mutates `loop_mode` and edits the embed in place; interaction check requires same VC membership |
| `QueueView` | 180 s | Ephemeral paginated list (10/page); page buttons disable at bounds; deletes its own message on timeout |
| `SearchView` | 300 s | Ephemeral dropdown of top-5 results; invoker-exclusive; deletes menu after selection |

## Automated Lifecycle

Three independent mechanisms guarantee the bot never outstays its welcome:

1. **Idle player** — the 300 s `queue.get()` timeout (above).
2. **Empty-channel countdown** — `on_voice_state_update` starts a 300 s timer
   when the bot shares a channel with zero humans. Repeat events do **not**
   reset it (it fires 5 min after the channel *first* emptied); a human
   joining cancels it. Timer bookkeeping is guarded against a cancelled
   predecessor evicting its replacement from the dict.
3. **Minute sweep** — `empty_channel_sweep` re-checks all guilds and starts
   missing countdowns, covering dropped gateway events, reconnects, and
   restart-while-alone scenarios.

`cleanup(guild)` is the single teardown path: cancels timers, drops the voice
lock, disconnects, deletes tracked messages (np/queue/added), clears the
queue, and cancels `player_task`.

## Message Hygiene

Public messages are deliberately minimized and tracked:

- "⏳ Loading track…" placeholders and "Added to Queue" embeds are deleted the
  moment playback of that song starts (or when the song is removed/cleared).
- Only one live Now Playing message exists per guild; `/nowplaying` and each
  track advance replace it.
- Everything posted is registered in the orphan index, so even a `kill -9`
  leaves no litter after the next boot.

## Error-Suppression Strategy

Fast typing generates autocomplete interactions that expire before their
responses land, surfacing as Discord error `10062` (`Unknown Interaction`)
and log noise. Both the custom `CommandTree.on_error` and the logging filter
in `music_bot.py` drop these specific cases; everything else still logs with
tracebacks.

## Testing Hooks

The design keeps Discord I/O behind seams that tests mock: `YTDLSource.*` are
classmethods, services take injected `aiohttp` sessions, and views/cogs accept
fake interactions. See [Testing & Contributing](testing.md).
