# Commands Reference

All music functionality is exposed as Discord slash commands (`/`). Most
playback-control commands require **DJ privileges** — see
[Permissions](#permissions) below. UI buttons on the Now Playing embed mirror
the most common commands.

## Playback

### `/play url`
Plays a song immediately or appends it to the queue.

| Input | Behavior |
|---|---|
| YouTube video URL | Queues that exact track |
| YouTube URL containing `list=` | Flat-extracts the playlist and queues the first **100** tracks |
| Spotify track/album/playlist link | Resolves metadata via Spotify's public oEmbed endpoint, then searches the cleaned title on YouTube |
| Plain text / search terms | Treated as a YouTube search; the best-ranked result is auto-picked (see [search ranking](architecture.md#audio-resolution-pipeline)) |

Behavior notes:

- The parameter has live **autocomplete**: suggestions come from Google's
  YouTube suggest API (0.4 s timeout, cached), so typing shows real query
  completions. Pasting a full URL also works.
- You must be in a voice channel; the bot joins (or moves to) your channel.
- If nothing is playing, a transient "⏳ Loading track..." message is posted
  and deleted when playback starts. Otherwise a public "Added to Queue"
  embed is posted.
- The deferred ephemeral interaction is deleted once handled — outcomes are
  shown via the public messages above.

### `/search query`
Runs `ytsearch5:` on the query and posts an **ephemeral** dropdown with the
top 5 results (title + duration). Only the invoker can pick from it. Selecting
a track queues/plays it exactly like `/play` and removes the menu. The menu
self-destructs after 5 minutes or on selection.

### `/nowplaying`
Re-posts the Now Playing embed (with control buttons) in the current channel.
The previous Now Playing message is deleted first, so there is never more than
one live player UI per guild. Acknowledged ephemerally.

## Playback Control *(DJ required)*

| Command | Description |
|---|---|
| `/pause` | Pauses playback |
| `/resume` | Resumes paused playback |
| `/skip` | Stops the current source; the loop advances to the next track. In Queue loop mode, the skipped track is re-appended |
| `/stop` | Clears queue **and** history, stops playback. Player idles out after 5 min |
| `/seek timestamp` | Jumps within the current track. Accepts `90`, `1:30`, or `1:05:30` |
| `/replay` | Restarts the current track from 0:00 |

Seeking works by stopping the voice client with a pending seek position; the
player loop re-resolves the stream with an FFmpeg `-ss` offset and keeps the
same track context (Now Playing embed is not recreated).

### Volume

```
/volume vol: 1–100
```
DJ-only. Applied instantly to the active source, stored on the player, and
**persisted per guild** in `music_settings.json`. Default is 50%.

## Queue Management *(DJ required)*

| Command | Description |
|---|---|
| `/queue` | Shows an ephemeral paginated view (10 tracks/page) of the upcoming queue plus the currently playing track. Prev/next buttons; expires (and deletes itself) after 3 minutes |
| `/shuffle` | Shuffles the queue while preserving the user-requests-before-autoplay ordering invariant |
| `/remove index` | Removes the track at `index` (1-based, as displayed by `/queue`). Also deletes its "Added to Queue" message |
| `/move from_index to_index` | Moves a queued track between positions (1-based) |
| `/clear` | Removes all upcoming tracks (current track keeps playing) |

## Modes *(DJ required)*

| Command | Description |
|---|---|
| `/loop mode: Off\|Track\|Queue` | `Track` repeats the current song; `Queue` re-appends finished songs to the end; updates the live Now Playing embed |
| `/autoplay` | Toggles autoplay for this guild. When enabled and the queue runs dry, the bot seeds a [YouTube Mix](architecture.md#autoplay-engine) from history and keeps queuing related tracks. State is persisted per guild |

Loop mode and autoplay are also reachable from the Now Playing embed buttons
(🔁 cycles Off → Track → Queue).

## Session & Admin

| Command | Permission | Description |
|---|---|---|
| `/leave` | DJ | Clears the queue and disconnects the bot, removing all its UI messages |
| `/settings` | anyone | Ephemeral embed showing volume, autoplay state, loop mode, and DJ role |
| `/setdjrole role` | Manage Server | Registers a custom role that grants DJ access (persisted). Defaults: any role literally named "DJ", or users with Manage Server/Administrator |
| `/clear_messages [amount] [minutes] [hours] [days]` | Manage Messages | Deletes up to `amount` (default 100) of **the bot's own messages** sent within the given time window. At least one time unit must be provided. Bulk deletion is used only when the window is < 14 days (Discord API limit) |

## Prefix Command

| Command | Permission | Description |
|---|---|---|
| `!sync` | Bot owner only | Force-syncs the slash-command tree globally with Discord. Needed after adding/renaming commands |

## Now Playing Embed Buttons

The persistent player UI exposes five buttons. All require DJ privileges and
require you to be in the bot's voice channel:

| Button | Action |
|---|---|
| ⏯️ | Pause ↔ Resume |
| ⏭️ | Skip |
| ⏹️ | Stop & clear queue/history |
| 🔁 | Cycle loop mode Off → Track → Queue (button label/color reflects state) |
| 🔀 | Shuffle queue |

Anyone may *view* the embed, but pressing a button without DJ privileges gets
an ephemeral rejection.

## Permissions

A user passes the DJ check if **any** of these hold:

1. They have **Manage Server** (`manage_guild`) or **Administrator**
   permissions, or
2. They hold a role named **`DJ`** (case-insensitive), or
3. They hold the custom role configured via `/setdjrole`.

Commands additionally gated elsewhere:

- `/setdjrole` → native `manage_guild` check enforced by Discord.
- `/clear_messages` → manual **Manage Messages** check; the bot itself also
  needs Manage Messages in the channel for bulk purges.

Everyone can use: `/play`, `/search`, `/queue`, `/nowplaying`, `/settings`.
Note `/play` and `/search` still require you to be in a voice channel.

## Typical Lifecycles

- **Idle disconnect**: when the queue finishes, the player waits **5 minutes**
  for new requests, then deletes its UI messages, disconnects, and destroys
  itself.
- **Empty channel**: if all humans leave the bot's voice channel, a
  **5-minute** countdown starts (repeated leave/join events do not reset it;
  one human rejoining cancels it). A background sweep every minute covers
  missed events.
- **Crash/reload recovery**: every public UI message is recorded in
  `music_orphans.json`; on boot the bot deletes leftovers from previous
  processes.
