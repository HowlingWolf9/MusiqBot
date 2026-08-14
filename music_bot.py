import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv

import logging

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
                if isinstance(exc_val, discord.HTTPException) and code in (10062, 40060):
                    return False
        return True


logging.getLogger("discord.app_commands.tree").addFilter(AutocompleteErrorFilter())


class CustomCommandTree(app_commands.CommandTree):
    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        # Silently suppress 404 Unknown Interaction errors caused by rapid typing & expired autocomplete tokens
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
    bot.run(os.getenv("DISCORD_TOKEN"))
