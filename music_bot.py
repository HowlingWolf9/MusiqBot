import asyncio
import contextlib
import fcntl
import logging
import os
import signal

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

load_dotenv()


class AutocompleteErrorFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            _, exc_val, _ = record.exc_info
            if exc_val:
                code = getattr(exc_val, "code", None)
                if isinstance(exc_val, discord.NotFound) or code in (10062, 40060):
                    return False
                if isinstance(exc_val, discord.HTTPException) and code in (
                    10062,
                    40060,
                ):
                    return False
        return True


logging.getLogger("discord.app_commands.tree").addFilter(AutocompleteErrorFilter())


class CustomCommandTree(app_commands.CommandTree):
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        # Silently suppress 404 Unknown Interaction errors caused by rapid
        # typing & expired autocomplete tokens
        if isinstance(error, discord.NotFound) or getattr(error, "code", None) == 10062:
            return
        if isinstance(error, app_commands.CommandInvokeError) and (
            isinstance(error.original, discord.NotFound)
            or getattr(error.original, "code", None) == 10062
        ):
            return
        await super().on_error(interaction, error)


class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            command_prefix="!", intents=intents, tree_cls=CustomCommandTree
        )
        self._synced = False

    async def setup_hook(self):
        await self.load_extension("music_cog")


bot = MusicBot()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


@bot.command(name="sync")
@commands.is_owner()
async def sync(ctx):
    """Sync the slash commands globally."""
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"Synced {len(synced)} commands.")
        bot._synced = True
    except Exception as e:
        await ctx.send(f"Failed to sync: {e}")


if __name__ == "__main__":
    # Single-instance guard: the OS releases this lock automatically on
    # process death (even SIGKILL), preventing overlapping bots from
    # watchmedo restarts, orphaned shells, or double script invocations.
    _lock_file = open("/tmp/musiqbot.lock", "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.getLogger(__name__).error(
            "Another MusiqBot instance is already running — exiting."
        )
        raise SystemExit(1) from None

    _shutting_down = False

    async def _on_signal():
        # systemd's control-group kill mode delivers SIGTERM to watchmedo AND
        # the bot process, so this handler can fire multiple times — run once.
        global _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        # Release the single-instance guard FIRST so watchmedo's replacement
        # can start immediately while this process finishes its own teardown.
        with contextlib.suppress(Exception):
            fcntl.flock(_lock_file, fcntl.LOCK_UN)
        logging.getLogger(__name__).info(
            "Shutdown signal received — cleaning up tracked messages..."
        )
        cog = bot.get_cog("MusicCog")
        if cog and hasattr(cog, "shutdown_cleanup"):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(cog.shutdown_cleanup(), timeout=15)
        with contextlib.suppress(Exception):
            await bot.close()

    async def runner():
        loop = asyncio.get_running_loop()
        _signal_tasks = set()

        def _spawn_signal_handler():
            # Retain a reference so the task can't be GC'd mid-teardown
            t = asyncio.create_task(_on_signal())
            _signal_tasks.add(t)
            t.add_done_callback(_signal_tasks.discard)

        for sig in (signal.SIGTERM, signal.SIGINT):
            # NotImplementedError on non-Unix loops; fall back to defaults there
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _spawn_signal_handler)
        await bot.start(os.getenv("DISCORD_TOKEN"))

    asyncio.run(runner())
