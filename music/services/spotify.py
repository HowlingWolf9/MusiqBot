import json
import re
import urllib.parse

import aiohttp


async def resolve_spotify(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        oembed_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(url)}"
        # Tight budget: a hung oEmbed request must not stall /play for minutes
        async with session.get(
            oembed_url, timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                title = data.get("title")
                if title:
                    cleaned = re.sub(
                        r"\s*-\s*song (and lyrics )?by.*$", "", title, flags=re.IGNORECASE
                    )
                    cleaned = re.sub(
                        r"\s*\|\s*Spotify.*$", "", cleaned, flags=re.IGNORECASE
                    ).strip()
                    author = data.get("author_name")
                    if author:
                        return f"ytsearch:{cleaned} - {author}"
                    return f"ytsearch:{cleaned}"
    except Exception:
        pass
    return None


async def resolve_spotify_playlist(
    session: aiohttp.ClientSession, url: str
) -> list[str]:
    """Extract list of search queries from a Spotify playlist or album embed page.
    Returns a list of search query strings formatted as 'ytsearch:{title} - {artist}'.
    """
    m = re.search(r"/(playlist|album)/([a-zA-Z0-9]+)", url)
    if not m:
        return []
    entity_type = m.group(1)
    entity_id = m.group(2)
    embed_url = f"https://open.spotify.com/embed/{entity_type}/{entity_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        async with session.get(
            embed_url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()
            match = re.search(
                r'<script id="__NEXT_DATA__".*?>(.*?)</script>', html, re.DOTALL
            )
            if not match:
                return []
            data = json.loads(match.group(1))
            entity = (
                data.get("props", {})
                .get("pageProps", {})
                .get("state", {})
                .get("data", {})
                .get("entity", {})
            )
            track_list = entity.get("trackList", [])
            results = []
            for item in track_list[:100]:
                if not isinstance(item, dict):
                    continue
                t_title = item.get("title")
                t_artist = item.get("subtitle") or ""
                if t_title:
                    query = f"ytsearch:{t_title} - {t_artist}".strip(" -")
                    results.append(query)
            return results
    except Exception:
        return []
