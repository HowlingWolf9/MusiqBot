import re
import urllib.parse
import aiohttp


async def resolve_spotify(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        oembed_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(url)}"
        async with session.get(oembed_url) as resp:
            if resp.status == 200:
                data = await resp.json()
                title = data.get("title")
                if title:
                    cleaned = re.sub(
                        r"\s*-\s*song (and lyrics )?by.*$", "", title, flags=re.IGNORECASE
                    )
                    cleaned = re.sub(
                        r"\s*\|\s*Spotify.*$", "", cleaned, flags=re.IGNORECASE
                    )
                    return f"ytsearch:{cleaned.strip()}"
    except Exception:
        pass
    return None
