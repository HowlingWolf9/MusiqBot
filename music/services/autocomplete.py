import urllib.parse
import aiohttp
import discord
from discord import app_commands


async def fetch_song_autocomplete(
    interaction: discord.Interaction,
    current: str,
    session: aiohttp.ClientSession,
    cache: dict,
) -> list[app_commands.Choice[str]]:
    if getattr(interaction, "is_expired", lambda: False)():
        return []
    if not current or not current.strip():
        return []

    query = current.strip().lower()
    if query in cache:
        return [
            app_commands.Choice(name=s[:100], value=s[:100]) for s in cache[query]
        ]

    try:
        api_url = f"https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={urllib.parse.quote(query)}"
        async with session.get(
            api_url, timeout=aiohttp.ClientTimeout(total=0.4)
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                if (
                    isinstance(data, list)
                    and len(data) > 1
                    and isinstance(data[1], list)
                ):
                    suggestions = data[1][:25]
                    cache[query] = suggestions
                    if len(cache) > 500:
                        cache.clear()
                    return [
                        app_commands.Choice(name=s[:100], value=s[:100])
                        for s in suggestions
                        if isinstance(s, str)
                    ]
    except Exception:
        pass
    return [app_commands.Choice(name=current[:100], value=current[:100])]
