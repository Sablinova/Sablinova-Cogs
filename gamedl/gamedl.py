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
    "Sec-Fetch-Site": "cross-site",
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


def _clean_game_title(title: str) -> str:
    """Remove HTML entities and trailing SteamRIP branding."""
    clean = html.unescape(title)
    clean = re.sub(r"[\s»\-|–—]+\s*SteamRIP.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bSteamRIP\b", "", clean, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", clean).strip()


def _sync_fetch(url: str, timeout: int = 15) -> str:
    """Synchronous HTTP GET with browser headers to avoid anti-bot blocks."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _resolve_bzzhr_direct(url: str) -> str:
    """Resolve BZZHR/Buzzheavier anti-hotlink links directly to the CDN file download URL."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html_text = resp.read().decode("utf-8", errors="replace")

        m = re.search(r'hx-get=[\"\'](/[^/]+/download\?[^\"\']+)[\"\']', html_text)
        if not m:
            m = re.search(r'copyDownloadLink\([\'\"]\\?(/[^/]+/download\?[^\'\"]+)[\'\"]\)', html_text)
        if not m:
            return url

        dl_path = m.group(1).replace(r"\/", "/")
        parsed = urllib.parse.urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        dl_url = urllib.parse.urljoin(base, dl_path)

        req_dl = urllib.request.Request(
            dl_url,
            headers={
                "User-Agent": HEADERS["User-Agent"],
                "HX-Request": "true",
                "Referer": url,
            },
        )
        with urllib.request.urlopen(req_dl, timeout=10) as dl_resp:
            direct_cdn = dl_resp.headers.get("Hx-Redirect") or dl_resp.headers.get("Location")
            if direct_cdn:
                return direct_cdn
    except Exception as exc:
        log.debug("Could not resolve BZZHR direct link for %s: %s", url, exc)
    return url


class GameDL(commands.Cog):
    """Search and extract game direct download links, size, and metadata."""

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

    async def _search_games(self, query: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Search for games matching query. Returns list of game dicts or error."""
        search_url = f"https://steamrip.com/?s={urllib.parse.quote(query)}"
        html_content = await self._fetch_html(search_url)
        if not html_content:
            return [], "Could not connect to the game database. Please try again later."

        cards = re.findall(
            r'<div[^>]+class=[\"\'][^\"\']*post-element[^\"\']*[\"\'][^>]*>(.*?)</div>\s*</div>',
            html_content,
            re.DOTALL,
        )

        results: List[Dict[str, Any]] = []
        for card in cards:
            title_m = re.search(r'<h2[^>]*><a[^>]+href=[\"\']([^\"\']+)[\"\'][^>]*>([^<]+)</a>', card)
            if not title_m:
                continue
            href = title_m.group(1).strip()
            raw_title = title_m.group(2).strip()
            clean_title = _clean_game_title(raw_title)
            full_url = urllib.parse.urljoin("https://steamrip.com/", href)

            img_m = re.search(r'data-back=[\"\']([^\"\']+)[\"\']', card)
            if not img_m:
                img_m = re.search(r'data-back-webp=[\"\']([^\"\']+)[\"\']', card)
            portrait_img = img_m.group(1).strip() if img_m else None
            if portrait_img and portrait_img.startswith("//"):
                portrait_img = "https:" + portrait_img

            results.append({
                "title": clean_title,
                "url": full_url,
                "portrait_image": portrait_img,
            })

        if not results:
            raw_posts = re.findall(
                r'<h2[^>]*>\s*<a[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>([^<]+)</a>',
                html_content,
            )
            for href, title in raw_posts:
                clean_title = _clean_game_title(title)
                full_url = urllib.parse.urljoin("https://steamrip.com/", href)
                results.append({
                    "title": clean_title,
                    "url": full_url,
                    "portrait_image": None,
                })

        return results, None

    async def _extract_details(self, page_url: str, default_image: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Extract game title, size, version, portrait cover image, and direct download links."""
        body = await self._fetch_html(page_url)
        if not body:
            return None

        # Title
        og_title = re.search(
            r'<meta\s+property=[\"\']og:title[\"\']\s+content=[\"\']([^\"\']+)[\"\']',
            body,
            re.IGNORECASE,
        )
        if og_title:
            title = _clean_game_title(og_title.group(1))
        else:
            title = "Unknown Game"

        # Image priority: portrait from search card -> portrait/poster in page -> og:image
        image = default_image
        if not image:
            portrait_match = re.search(
                r'(https?://steamrip\.com/wp-content/uploads/[^\s\"\'<>]*(?:portrait|poster|torrent)[^\s\"\'<>]*\.(?:jpg|png|webp))',
                body,
                re.IGNORECASE,
            )
            if portrait_match:
                image = portrait_match.group(1)
            else:
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

        # Direct download links (shortc-button links)
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

            # Skip self-links
            if "steamrip.com" in raw_url:
                continue

            # Auto-resolve BZZHR / Buzzheavier anti-hotlink redirect links to direct CDN downloads
            if "bzzhr.to" in raw_url or "buzzheavier.com" in raw_url:
                direct_bzzhr = await asyncio.to_thread(_resolve_bzzhr_direct, raw_url)
                if direct_cdn_resolved := direct_bzzhr:
                    raw_url = direct_cdn_resolved

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
        description="Search for a PC game and extract direct download links.",
    )
    @app_commands.describe(game="The name of the game to search for")
    @commands.cooldown(1, 3.0, commands.BucketType.user)
    async def gamedl(self, ctx: commands.Context, *, game: str):
        """Search for a PC game and extract direct download links.

        Example:
            [p]gamedl portal 2
            [p]gamedl elden ring
        """
        async with ctx.typing():
            async with self._lock:
                results, error = await self._search_games(game)

            if error:
                embed = discord.Embed(
                    title="❌ Search Error",
                    description=error,
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return

            if not results:
                embed = discord.Embed(
                    title="🔍 Game Not Found",
                    description=f"No results found matching **{game}**.\nTry searching with a shorter or alternative title.",
                    color=discord.Color.orange(),
                )
                await ctx.send(embed=embed)
                return

            top_game = results[0]
            top_title = top_game["title"]
            top_url = top_game["url"]
            top_portrait = top_game.get("portrait_image")

            async with self._lock:
                details = await self._extract_details(top_url, default_image=top_portrait)

            if not details:
                embed = discord.Embed(
                    title="❌ Failed to Load Game Page",
                    description=f"Found [{top_title}]({top_url}) but could not fetch details.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return

            # Construct Embed (No author header / no broken icon / no SteamRIP branding)
            embed = discord.Embed(
                title=f"🎮 {details['title']}",
                url=details["url"],
                color=discord.Color.from_rgb(147, 51, 234),
            )

            desc_lines = []
            if details["version"]:
                desc_lines.append(f"🏷️ **Version:** `{details['version']}`")
            desc_lines.append(f"📦 **Game Size:** `{details['size']}`")
            embed.description = "\n".join(desc_lines)

            # Direct Download Links
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
                    value=f"Visit the [Game Page]({details['url']}) directly for links.",
                    inline=False,
                )

            # Other search results (if available)
            if len(results) > 1:
                other_titles = [f"• [{item['title']}]({item['url']})" for item in results[1:5]]
                embed.add_field(
                    name="🔍 Other Matches",
                    value="\n".join(other_titles),
                    inline=False,
                )

            # Set portrait game picture thumbnail
            if details["image"]:
                embed.set_thumbnail(url=details["image"])

            embed.set_footer(
                text=f"Requested by {ctx.author.display_name}",
                icon_url=ctx.author.display_avatar.url if ctx.author.display_avatar else None,
            )

            # Interactive Link Buttons View
            view = discord.ui.View()

            # Add download link buttons (up to 4)
            for item in details["downloads"][:4]:
                host_label = item["host"][:20]
                view.add_item(
                    discord.ui.Button(
                        label=f"Download ({host_label})",
                        url=item["url"],
                        style=discord.ButtonStyle.link,
                    )
                )

            # Button to visit the game page (neutral label without SteamRIP)
            view.add_item(
                discord.ui.Button(
                    label="Game Page",
                    url=details["url"],
                    style=discord.ButtonStyle.link,
                )
            )

            try:
                await ctx.send(embed=embed, view=view)
            except Exception as send_err:
                log.warning("Sending with View failed (%s), falling back to embed only", send_err)
                await ctx.send(embed=embed)
