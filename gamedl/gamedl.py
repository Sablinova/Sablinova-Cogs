import asyncio
import html
import json
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

USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0",
]

BZZHR_HEADERS = {
    "User-Agent": USER_AGENTS[0],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Referer": "https://steamrip.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Upgrade-Insecure-Requests": "1",
}

KNOWN_DOMAINS: Dict[str, str] = {
    "gofile.io": "Gofile",
    "bzzhr.to": "BZZHR",
    "ts.bzzhr.to": "BZZHR",
    "buzzheavier.com": "BZZHR",
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
    """Remove HTML entities, 'Free Download', versions, builds, and trailing SteamRIP branding."""
    clean = html.unescape(title)
    clean = re.sub(r"\bFree\s+Download\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"[\s»\-|–—]+\s*SteamRIP.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bSteamRIP\b", "", clean, flags=re.IGNORECASE)
    # Remove parenthesized versions/builds like (v1.4.5.8 + Co-op) or (Build 123)
    clean = re.sub(r"\s*\([^)]*\)", "", clean)
    # Remove bracketed versions like [v2.12] or [Build 100]
    clean = re.sub(r"\s*\[[^\]]*\]", "", clean)
    # Remove standalone trailing version like v1.4.4.9 or - 1.4.4.9
    clean = re.sub(r"[\s\-–—:]+\s*v?\d+(?:\.\d+)+[a-z0-9_.-]*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"[\s»\-|–—]+$", "", clean)
    clean = re.sub(r"^[\s»\-|–—]+", "", clean)
    return re.sub(r"\s+", " ", clean).strip()


def _clean_for_steam(raw_title: str) -> str:
    """Extract core game title suitable for Steam store search."""
    clean = _clean_game_title(raw_title)
    clean = re.sub(r"[-–—:]?\s*(?:Digital\s+)?Deluxe\s+Edition.*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"[\s»\-|–—]+$", "", clean)
    return re.sub(r"\s+", " ", clean).strip()


def _sync_fetch(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 15) -> str:
    """Synchronous HTTP GET with headers."""
    req = urllib.request.Request(url, headers=headers or HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _resolve_steam_data_sync(game_title: str) -> Optional[Dict[str, Any]]:
    """Search Steam store API for the official game page, clean name, and 600x900 vertical portrait art."""
    search_term = _clean_for_steam(game_title)
    if not search_term:
        return None
    url = f"https://store.steampowered.com/api/storesearch/?term={urllib.parse.quote(search_term)}&l=english&cc=US"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("items", [])
            if items:
                top = items[0]
                appid = top["id"]
                return {
                    "appid": appid,
                    "name": top["name"],
                    "steam_url": f"https://store.steampowered.com/app/{appid}/",
                    "portrait_url": f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg",
                }
    except Exception as exc:
        log.debug("Steam store lookup failed for %s: %s", search_term, exc)
    return None


def _resolve_bzzhr_direct(url: str, max_retries: int = 5) -> str:
    """Resolve BZZHR/Buzzheavier anti-hotlink links directly to the CDN file download URL.

    BZZHR redirects to steamrip.com unless Referer is steamrip.com.
    Resolving the link directly to the ts.bzzhr.to CDN URL allows users to download
    without any anti-hotlink redirects or browser warnings.
    """
    import time
    for attempt in range(max_retries):
        ua = USER_AGENTS[attempt % len(USER_AGENTS)]
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Referer": "https://steamrip.com/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Upgrade-Insecure-Requests": "1",
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=7) as resp:
                html_text = resp.read().decode("utf-8", errors="replace")

            m = (
                re.search(r'hx-get=[\"\'](/[^/]+/download\?[^\"\']+)[\"\']', html_text)
                or re.search(r'copyDownloadLink\([\'\"]\\?(/[^/]+/download\?[^\'\"]+)[\'\"]\)', html_text)
                or re.search(r'href=[\"\'](/[^/]+/download\?[^\"\']+)[\"\']', html_text)
            )
            if not m:
                time.sleep(0.3)
                continue

            dl_path = m.group(1).replace(r"\/", "/")
            parsed = urllib.parse.urlparse(url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            dl_url = urllib.parse.urljoin(base, dl_path)

            req_dl = urllib.request.Request(
                dl_url,
                headers={
                    "User-Agent": ua,
                    "HX-Request": "true",
                    "HX-Current-URL": url,
                    "Referer": url,
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                },
            )
            with urllib.request.urlopen(req_dl, timeout=7) as dl_resp:
                direct_cdn = dl_resp.headers.get("Hx-Redirect") or dl_resp.headers.get("Location")
                if direct_cdn:
                    return direct_cdn
        except Exception:
            time.sleep(0.25 * (attempt + 1))

    log.warning("Could not resolve BZZHR direct link for %s after %d retries", url, max_retries)
    return url


class GameDL(commands.Cog):
    """Search and extract game direct download links, size, and Steam metadata."""

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
        """Extract game title, size, version, cover art, and direct download links."""
        body = await self._fetch_html(page_url)
        if not body:
            return None

        # Clean base title
        og_title = re.search(
            r'<meta\s+property=[\"\']og:title[\"\']\s+content=[\"\']([^\"\']+)[\"\']',
            body,
            re.IGNORECASE,
        )
        raw_name = og_title.group(1) if og_title else "Unknown Game"
        cleaned_name = _clean_game_title(raw_name)

        # Lookup Steam Store info for official portrait art, clean title, and store page
        steam_info = await asyncio.to_thread(_resolve_steam_data_sync, cleaned_name)

        # Title: Prefer official clean Steam title (e.g. 'Portal 2'), else pure game name without (Build...)
        if steam_info and steam_info.get("name"):
            title = steam_info["name"]
        else:
            title = cleaned_name

        # Image priority: Steam official portrait cover -> search card portrait -> page image
        if steam_info and steam_info.get("portrait_url"):
            image = steam_info["portrait_url"]
        else:
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
        if not version:
            vm = re.search(r'[\(\[]?(v?\d+(?:\.\d+)+[^\)\]]*)[\)\]]?', raw_name)
            if vm:
                version = vm.group(1).strip()

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
            preceding = body[max(0, start_pos - 400) : start_pos]
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

            # Check if this link is for an Update or specific Part
            update_m = re.search(r'<strong>\s*(Update(?:\s+Only)?\s*[-–—]\s*[^<]+)</strong>', preceding, re.I)
            part_m = re.search(r'<strong>\s*(Part\s+\d+)\s*</strong>', preceding, re.I)

            # Normalize Buzzheavier / BZZHR label to BZZHR
            if any(k in host_label.lower() or k in raw_url.lower() for k in ["bzzhr", "buzzheavier"]):
                host_label = "BZZHR"

            if update_m:
                up_text = re.sub(r'<[^>]+>', '', update_m.group(1)).strip()
                ver_in_up = re.search(r'v?\d+(?:\.\d+)+', up_text)
                if ver_in_up:
                    host_label = f"{host_label} (Update {ver_in_up.group(0)})"
                else:
                    host_label = f"{host_label} (Update)"
            elif part_m:
                host_label = f"{host_label} ({part_m.group(1)})"

            downloads.append({"host": host_label, "url": raw_url})

        # Sort download links so Buzzheavier / BZZHR is prioritized first
        def _is_bzzhr(item: Dict[str, str]) -> bool:
            h = item["host"].lower()
            u = item["url"].lower()
            return any(k in h or k in u for k in ["buzzheavier", "bzzhr"])

        downloads.sort(key=lambda x: 0 if _is_bzzhr(x) else 1)

        # Game store page (defaults to Steam if matched)
        game_page_url = steam_info["steam_url"] if steam_info else page_url
        is_steam = bool(steam_info)

        return {
            "title": title,
            "url": game_page_url,
            "image": image,
            "size": size,
            "version": version,
            "downloads": downloads,
            "is_steam": is_steam,
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

            # Construct Embed (Clean game name, links to Steam, no SteamRIP branding)
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

            # Direct Download Links with BZZHR recommended sign
            if details["downloads"]:
                dl_lines = []
                for item in details["downloads"]:
                    host_name = item["host"]
                    link_url = item["url"]
                    is_bzzhr = any(k in host_name.lower() or k in link_url.lower() for k in ["buzzheavier", "bzzhr"])
                    display_host = host_name
                    if is_bzzhr and "⭐" not in display_host:
                        dl_lines.append(f"• [**{display_host}**]({link_url}) ⭐ *(Recommended)*")
                    else:
                        dl_lines.append(f"• [**{display_host}**]({link_url})")

                # Chunk download lines so each field strictly respects Discord's 1024-char limit
                field_chunks: List[List[str]] = []
                current_chunk: List[str] = []
                current_len = 0
                for line in dl_lines:
                    line_len = len(line) + 1
                    if current_chunk and (current_len + line_len > 900):
                        field_chunks.append(current_chunk)
                        current_chunk = [line]
                        current_len = line_len
                    else:
                        current_chunk.append(line)
                        current_len += line_len
                if current_chunk:
                    field_chunks.append(current_chunk)

                for idx, chunk in enumerate(field_chunks):
                    name = "📥 Direct Download Links" if idx == 0 else f"📥 Direct Download Links (Part {idx + 1})"
                    embed.add_field(
                        name=name,
                        value="\n".join(chunk),
                        inline=False,
                    )
            else:
                embed.add_field(
                    name="📥 Download Links",
                    value=f"Visit the [Store Page]({details['url']}) for details.",
                    inline=False,
                )

            # Other search results (if available, with Free Download removed)
            if len(results) > 1:
                other_titles = [f"• {_clean_game_title(item['title'])}" for item in results[1:5]]
                other_text = "\n".join(other_titles)
                if len(other_text) > 1000:
                    other_text = other_text[:990] + "..."
                embed.add_field(
                    name="🔍 Other Matches",
                    value=other_text,
                    inline=False,
                )

            # Set Steam portrait cover art
            if details["image"]:
                embed.set_thumbnail(url=details["image"])

            embed.set_footer(
                text=f"Requested by {ctx.author.display_name}",
                icon_url=ctx.author.display_avatar.url if ctx.author.display_avatar else None,
            )

            # Interactive Link Buttons View (Discord allows max 5 buttons per row, 25 total)
            view = discord.ui.View()

            # Add download link buttons (up to 4, with BZZHR recommended star)
            for item in details["downloads"][:4]:
                host_label = item["host"][:18]
                is_bzzhr = any(k in host_label.lower() or k in item["url"].lower() for k in ["buzzheavier", "bzzhr"])
                btn_text = "⭐ Download (BZZHR)" if is_bzzhr else f"Download ({host_label})"
                if len(item["url"]) <= 512:
                    view.add_item(
                        discord.ui.Button(
                            label=btn_text[:80],
                            url=item["url"],
                            style=discord.ButtonStyle.link,
                        )
                    )

            # Button to visit the Steam Store page
            store_button_label = "Steam Store" if details.get("is_steam") else "Game Page"
            if len(details["url"]) <= 512:
                view.add_item(
                    discord.ui.Button(
                        label=store_button_label[:80],
                        url=details["url"],
                        style=discord.ButtonStyle.link,
                    )
                )

            # Safe embed sender that guarantees field values <= 1000 chars
            def _sanitize_embed(emb: discord.Embed) -> discord.Embed:
                for f_idx, field in enumerate(emb.fields):
                    if len(field.value) > 1000:
                        emb.set_field_at(
                            f_idx,
                            name=field.name[:250],
                            value=field.value[:990] + "...",
                            inline=field.inline,
                        )
                return emb

            embed = _sanitize_embed(embed)

            try:
                await ctx.send(embed=embed, view=view)
            except Exception as send_err:
                log.warning("Sending with View failed (%s), attempting embed only", send_err)
                try:
                    await ctx.send(embed=embed)
                except Exception as final_err:
                    log.error("Failed to send embed: %s", final_err)
                    # Absolute emergency fallback: stripped plain embed
                    safe_embed = discord.Embed(
                        title=f"🎮 {details['title'][:250]}",
                        url=details["url"][:512],
                        description=f"📦 **Game Size:** `{details['size']}`",
                        color=discord.Color.blurple(),
                    )
                    safe_embed.add_field(
                        name="📥 Store Page",
                        value=f"[Click here to view game page]({details['url']})",
                        inline=False,
                    )
                    await ctx.send(embed=safe_embed)
