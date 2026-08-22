import urllib.parse
import aiohttp
import discord
from discord import app_commands


def _to_choice(s: str) -> app_commands.Choice[str]:
    return app_commands.Choice(name=s[:100], value=s[:100])


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
        return [_to_choice(s) for s in cache[query] if isinstance(s, str)]

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
                    suggestions = [s for s in data[1][:25] if isinstance(s, str)]
                    if suggestions:
                        cache[query] = suggestions
                        if len(cache) > 500:
                            cache.clear()
                        return [_to_choice(s) for s in suggestions]
    except Exception:
        pass
    return [_to_choice(current)]
