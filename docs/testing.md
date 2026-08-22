# Testing & Contributing

## Test Suite Overview

`test_music.py` (~1400 lines) is a comprehensive `pytest` suite exercising the
bot's logic with Discord I/O mocked out. It runs fully offline — no token,
no network, no real voice sessions.

### Running

```bash
source venv/bin/activate
python -m pytest test_music.py -v          # full run
python -m pytest test_music.py -k autoplay # subset by keyword
```

Dependencies: `requirements-dev.txt` (`pytest`, `pytest-asyncio`,
`pytest-mock`, plus `ruff` for linting).

### What's covered (highlights)

| Area | Example tests |
|---|---|
| Queue semantics | user-priority over autoqueue, multi-user FIFO before autoplay, shuffle preserving priority, move/remove bounds, clear |
| Loop modes | track-loop insert-front, queue-loop re-append, skip/stop interaction with loop, instant-fail bypassing requeue |
| Permissions | DJ gate rejections, custom `/setdjrole` matching, Manage-Server fallback |
| Autoplay | mix filtering of non-music entries, history/queue exclusion, fallback search when the mix is empty, prefetch task spawning |
| Audio pipeline | search-ranking scorer, FFmpeg option assertions, User-Agent passthrough into FFmpeg args, SilencePrimer frame behavior |
| Seek/replay | timestamp parsing (`ss`, `mm:ss`, `hh:mm:ss`), invalid input rejection, no-op when idle |
| Lifecycle | idle disconnect timer, empty-channel countdown not resetting on repeats, minute sweep starting missed timers, forced-disconnect cleanup, shutdown cleanup |
| Persistence | atomic settings write/read round-trip |
| Message hygiene | orphan sweep deleting leftovers, track/untrack index roundtrip |
| Services | autocomplete caching + malformed-response handling + expired interactions, Spotify metadata cleanup and error paths |
| UI logic | QueueView page clamping and interaction checks, SearchSelect handling of garbage entries/durations |
| Error UX | missing-permission responses, unexpected-error followups after defer |

## Coding Conventions

- **Python 3.10+** style: PEP 604 unions (`str | None`), f-strings, type hints
  on public functions.
- **Formatting/lint**: Ruff (`.ruff_cache/` present). Run `ruff check .`
  before submitting; keep lines consistent with surrounding style (~100 cols).
- **No comments unless necessary** — the codebase favors self-documenting
  names; comments are reserved for non-obvious *why* explanations
  (e.g. the PO-token rationale in `config.py`, the lock-order note in
  `music_bot.py`).
- **Async discipline**:
  - Blocking work (yt-dlp extraction, file writes) goes through
    `run_in_executor`.
  - Never call blocking APIs on the event loop.
  - Cross-thread completion uses
    `loop.call_soon_threadsafe(...)` (see the `after=` callback wiring).
  - Per-guild shared state mutation happens under the cog's
    `voice_connect_locks[guild_id]` lock.
- **Persistence** — every write to `music_settings.json` /
  `music_orphans.json` must remain atomic (temp file → flush+fsync →
  `os.replace`) so crashes can't corrupt state.

## Contribution Workflow

1. Fork / branch from `main`.
2. Start the dev loop: `./run_dev.sh` — the bot auto-restarts on any `.py`
   change (the single-instance lock makes this safe).
3. Add or extend tests in `test_music.py`, mirroring existing fixture style
   (`cog`, `interaction` fixtures with mocked guilds/members).
4. Verify:

   ```bash
   python -m pytest test_music.py -q
   ruff check .
   ```

5. Keep behavioral changes documented: update the relevant file under
   `docs/` (commands.md for user-visible changes, architecture.md for
   internals) and the README feature list if significant.

### Adding a new slash command — checklist

- [ ] Define it on `MusicCog` with `@app_commands.command` and a concise description.
- [ ] Voice-dependent? Call `verify_voice()` first; needs connect/move? Use `ensure_voice_client()`.
- [ ] Disruptive? Guard with `check_dj_permission()`.
- [ ] Any public message posted must pass through `track_message()` and be
      removed via `delete_message_safe()` (+ `untrack_message()` when
      appropriate).
- [ ] Persisted state changes go through `self.settings[...]` +
      `save_settings_async()`.
- [ ] Add tests for success path, permission failure, and edge cases.
- [ ] Remind users to run `!sync` (owner) to register the new command.
