import asyncio
import html
import logging
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from redbot.core import commands
from redbot.core.bot import Red

log = logging.getLogger("red.sablinova.gamedl")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://steamrip.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

KNOWN_DOMAINS: Dict[str, str] = {
    "gofile.io": "Gofile",
    "bzzhr.to": "Buzzheavier",
    "buzzheavier.com": "Buzzheavier",
    "megadb.net": "MegaDB",
    "1fichier.com": "1Fichier",
    "fileditchfiles.me": "FileDitch",
    "fileditch.com": "FileDitch",
    "qiwi.gg": "Qiwi",
    "datanodes.to": "DataNodes",
    "pixeldrain.com": "PixelDrain",
    "mediafire.com": "MediaFire",
    "mega.nz": "MEGA",
    "dropgalaxy.com": "DropGalaxy",
    "krakenfiles.com": "KrakenFiles",
    "rapidgator.net": "RapidGator",
}


def _sync_fetch(url: str, timeout: int = 15) -> str:
    """Synchronous HTTP GET with browser headers to avoid anti-bot blocks."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


class GameDL(commands.Cog):
    """Search SteamRIP and extract game download links, size, and metadata."""

    def __init__(self, bot: Red):
        self.bot = bot
        self._lock = asyncio.Lock()

    async def _fetch_html(self, url: str) -> Optional[str]:
        """Fetch HTML asynchronously via thread pool."""
        try:
            return await asyncio.to_thread(_sync_fetch, url)
        except Exception as exc:
            log.warning("Failed to fetch %s: %s", url, exc)
            return None

    async def _search_steamrip(self, query: str) -> Tuple[List[Tuple[str, str]], Optional[str]]:
        """Search SteamRIP for games matching query. Returns [(title, url), ...] or error."""
        search_url = f"https://steamrip.com/?s={urllib.parse.quote(query)}"
        html_content = await self._fetch_html(search_url)
        if not html_content:
            return [], "Could not connect to SteamRIP. The site may be down or unreachable."

        raw_posts = re.findall(
            r'<h2[^>]*>\s*<a[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>([^<]+)</a>',
            html_content,
        )
        if not raw_posts:
            return [], None

        results: List[Tuple[str, str]] = []
        for href, title in raw_posts:
            clean_title = html.unescape(title).strip()
            full_url = urllib.parse.urljoin("https://steamrip.com/", href)
            results.append((clean_title, full_url))

        return results, None

    async def _extract_details(self, page_url: str) -> Optional[Dict[str, Any]]:
        """Extract game title, size, version, cover image, and direct download links."""
        body = await self._fetch_html(page_url)
        if not body:
            return None

        # Title
        og_title = re.search(
            r'<meta\s+property=[\"\']og:title[\"\']\s+content=[\"\']([^\"\']+)[\"\']',
            body,
            re.IGNORECASE,
        )
        title = html.unescape(og_title.group(1)).strip() if og_title else "Unknown Game"

        # Image
        og_img = re.search(
            r'<meta\s+property=[\"\']og:image[\"\']\s+content=[\"\']([^\"\']+)[\"\']',
            body,
            re.IGNORECASE,
        )
        image = og_img.group(1).strip() if og_img else None

        # Size
        size_match = re.search(
            r'<strong>(?:Game\s+)?Size:\s*</strong>\s*([^<]+)',
            body,
            re.IGNORECASE,
        )
        size = size_match.group(1).strip() if size_match else "Unknown"

        # Version
        ver_match = re.search(
            r'<strong>Version</strong>\s*:\s*([^<]+)',
            body,
            re.IGNORECASE,
        )
        version = ver_match.group(1).strip() if ver_match else None

        # Find shortc-button links (SteamRIP download buttons)
        button_matches = list(
            re.finditer(
                r'<a[^>]+href=[\"\']([^\"\']+)[\"\'][^>]*class=[\"\'][^\"\']*shortc-button[^\"\']*[\"\'][^>]*>(.*?)</a>',
                body,
                re.IGNORECASE,
            )
        )

        downloads: List[Dict[str, str]] = []
        seen_urls = set()

        for m in button_matches:
            raw_url = m.group(1).strip()
            if raw_url.startswith("//"):
                raw_url = "https:" + raw_url

            # Skip self-links or navigation
            if "steamrip.com" in raw_url:
                continue

            if raw_url in seen_urls:
                continue
            seen_urls.add(raw_url)

            # Determine host label
            start_pos = m.start()
            preceding = body[max(0, start_pos - 250) : start_pos]
            label_matches = re.findall(
                r'<strong>(?:<span[^>]*>)?([^<]+?)(?:</span>)?</strong>',
                preceding,
                re.IGNORECASE,
            )

            parsed = urllib.parse.urlparse(raw_url)
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]

            host_label = None
            if label_matches:
                cand = re.sub(r'<[^>]+>', '', label_matches[-1]).strip()
                if cand and not any(
                    x in cand.lower()
                    for x in [
                        "screenshot",
                        "system",
                        "pre-installed",
                        "trailer",
                        "requirements",
                        "game info",
                        "developer",
                        "platform",
                    ]
                ):
                    host_label = cand

            if not host_label:
                host_label = KNOWN_DOMAINS.get(domain, domain or "Direct Download")

            downloads.append({"host": host_label, "url": raw_url})

        return {
            "title": title,
            "url": page_url,
            "image": image,
            "size": size,
            "version": version,
            "downloads": downloads,
        }

    @commands.hybrid_command(
        name="gamedl",
        aliases=["steamrip"],
        description="Search SteamRIP and extract direct game download links.",
    )
    @app_commands.describe(game="The name of the game to search for")
    @commands.cooldown(1, 3.0, commands.BucketType.user)
    async def gamedl(self, ctx: commands.Context, *, game: str):
        """Search SteamRIP for a PC game and extract direct download links.

        Example:
            [p]gamedl portal 2
            [p]gamedl elden ring
        """
        async with ctx.typing():
            async with self._lock:
                results, error = await self._search_steamrip(game)

            if error:
                embed = discord.Embed(
                    title="❌ SteamRIP Error",
                    description=error,
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return

            if not results:
                embed = discord.Embed(
                    title="🔍 Game Not Found",
                    description=f"No results found on SteamRIP matching **{game}**.\nTry searching with a shorter or alternative title.",
                    color=discord.Color.orange(),
                )
                await ctx.send(embed=embed)
                return

            top_title, top_url = results[0]
            async with self._lock:
                details = await self._extract_details(top_url)

            if not details:
                embed = discord.Embed(
                    title="❌ Failed to Load Game Page",
                    description=f"Found [{top_title}]({top_url}) but could not fetch details.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return

            # Construct Embed
            embed = discord.Embed(
                title=f"🎮 {details['title']}",
                url=details["url"],
                color=discord.Color.from_rgb(147, 51, 234),  # Royal SteamRIP purple
            )
            embed.set_author(
                name="SteamRIP Game Downloader",
                url="https://steamrip.com/",
                icon_url="https://steamrip.com/favicon.ico",
            )

            desc_lines = []
            if details["version"]:
                desc_lines.append(f"🏷️ **Version:** `{details['version']}`")
            desc_lines.append(f"📦 **Game Size:** `{details['size']}`")
            embed.description = "\n".join(desc_lines)

            # Download links section
            if details["downloads"]:
                dl_lines = []
                for item in details["downloads"]:
                    host_name = item["host"]
                    link_url = item["url"]
                    dl_lines.append(f"• [**{host_name}**]({link_url})")
                embed.add_field(
                    name="📥 Direct Download Links",
                    value="\n".join(dl_lines),
                    inline=False,
                )
            else:
                embed.add_field(
                    name="📥 Download Links",
                    value=f"Visit the [SteamRIP Page]({details['url']}) directly for links.",
                    inline=False,
                )

            # Other search results (if available)
            if len(results) > 1:
                other_titles = [f"• [{t}]({u})" for t, u in results[1:5]]
                embed.add_field(
                    name="🔍 Other Matches",
                    value="\n".join(other_titles),
                    inline=False,
                )

            if details["image"]:
                embed.set_thumbnail(url=details["image"])

            embed.set_footer(
                text=f"Requested by {ctx.author.display_name} • Powered by SteamRIP",
                icon_url=ctx.author.display_avatar.url if ctx.author.display_avatar else None,
            )

            # Interactive Link Buttons View
            view = discord.ui.View()
            added_buttons = 0

            # Add buttons for download links (up to 4 so it leaves room for SteamRIP link on 1 row)
            for item in details["downloads"][:4]:
                host_label = item["host"][:20]
                view.add_item(
                    discord.ui.Button(
                        label=f"Download ({host_label})",
                        url=item["url"],
                        style=discord.ButtonStyle.link,
                    )
                )
                added_buttons += 1

            # Button to view on SteamRIP
            view.add_item(
                discord.ui.Button(
                    label="SteamRIP Page",
                    url=details["url"],
                    style=discord.ButtonStyle.link,
                )
            )

            try:
                await ctx.send(embed=embed, view=view)
            except Exception as send_err:
                log.warning("Sending with View failed (%s), falling back to embed only", send_err)
                await ctx.send(embed=embed)
