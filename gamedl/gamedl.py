import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import re
import struct
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple, Union

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

GAMEBOUNTY_SECRET = "sYcRjbpmNLTKNgzGvjaPfPeJxpEHIbSWthvDHxNehxcITTCpmA"

KNOWN_DOMAINS: Dict[str, str] = {
    "gofile.io": "Gofile",
    "bzzhr.to": "BZZHR",
    "ts.bzzhr.to": "BZZHR",
    "buzzheavier.com": "BZZHR",
    "megadb.net": "MegaDB",
    "1fichier.com": "1Fichier",
    "fileditchfiles.me": "FileDitch",
    "fileditchfiles.st": "FileDitch",
    "fileditch.com": "FileDitch",
    "qiwi.gg": "Qiwi",
    "datanodes.to": "DataNodes",
    "pixeldrain.com": "PixelDrain",
    "cdn.pixeldrain.eu.cc": "PixelDrain",
    "pixeldrain.eu.cc": "PixelDrain",
    "pd-by.projectsablinova.workers.dev": "PixelDrain",
    "pd-node1.projectsablinova.workers.dev": "PixelDrain",
    "pd-node2.projectsablinova.workers.dev": "PixelDrain",
    "pd-node3.projectsablinova.workers.dev": "PixelDrain",
    "pd-node4.projectsablinova.workers.dev": "PixelDrain",
    "pd-node5.projectsablinova.workers.dev": "PixelDrain",
    "filekeeper.net": "FileKeeper",
    "mediafire.com": "MediaFire",
    "mega.nz": "MEGA",
    "dropgalaxy.com": "DropGalaxy",
    "krakenfiles.com": "KrakenFiles",
    "fileq.net": "FileQ",
    "0807.st": "0807",
    "mocha.my": "Mocha",
    "vikingfile.com": "VikingFile",
    "dropdrive.org": "DropDrive",
    "transfer.it": "TransferIt",
}


def _clean_game_title(title: str) -> str:
    """Remove HTML entities, 'Free Download', versions, builds, and trailing branding."""
    clean = html.unescape(title)
    clean = re.sub(r"\bFree\s+Download\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"[\s»\-|–—]+\s*(?:SteamRIP|SteamUnderground|WorldOfPCGames).*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bSteamRIP\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bGameBounty\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bSteamUnderground(?:\.net)?\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bWorld\s*Of\s*PC\s*Games(?:\.com)?\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\b(?:PC\s*(?:Game|Download)|Steam\s*Game|Direct\s*Download)\b", "", clean, flags=re.IGNORECASE)
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


def _extract_domain(url: str) -> str:
    """Extract normalized domain without www. from a URL."""
    parsed = urllib.parse.urlparse(url)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _sync_fetch_ipv4(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 15) -> str:
    """Fallback HTTP GET forcing IPv4 address family to avoid IPv6 unreachable errors on container networks."""
    import socket
    orig_gai = socket.getaddrinfo

    def _gai_ipv4(*args, **kwargs):
        res = orig_gai(*args, **kwargs)
        v4 = [r for r in res if r[0] == socket.AF_INET]
        return v4 if v4 else res

    socket.getaddrinfo = _gai_ipv4
    try:
        req = urllib.request.Request(url, headers=headers or HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    finally:
        socket.getaddrinfo = orig_gai


def _sync_fetch(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 15) -> str:
    """Synchronous HTTP GET with headers and automatic IPv4 fallback."""
    req = urllib.request.Request(url, headers=headers or HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except OSError as exc:
        if getattr(exc, "errno", None) == 101 or "unreachable" in str(exc).lower():
            return _sync_fetch_ipv4(url, headers, timeout)
        raise


def _lookup_appid_sync(appid: str) -> Optional[str]:
    """If search query is a numeric Steam AppID, look up the official game name."""
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get(appid, {}).get("data", {}).get("name")
    except Exception as exc:
        log.debug("AppID lookup failed for %s: %s", appid, exc)
    return None


def _find_working_steam_image(appid: Union[int, str], tiny_image: Optional[str] = None) -> Optional[str]:
    """Find a verified, fast-loading Steam CDN image that will never 404 or hang in Discord."""
    aid = str(appid).strip()
    candidates = [
        f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{aid}/library_600x900.jpg",
        f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{aid}/library_hero.jpg",
        f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{aid}/header.jpg",
    ]
    if tiny_image:
        prefix = tiny_image.rsplit("/", 1)[0]
        candidates.insert(1, f"{prefix}/library_600x900.jpg")
        candidates.append(tiny_image)

    for url in candidates:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]}, method="HEAD")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return url
        except Exception:
            continue

    return tiny_image or f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{aid}/library_hero.jpg"


def _find_working_steam_banner(appid: Union[int, str]) -> Optional[str]:
    """Find a verified, fast-loading wide Steam hero banner (1920x620) for Discord embed."""
    aid = str(appid).strip()
    candidates = [
        f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{aid}/library_hero.jpg",
        f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{aid}/header.jpg",
        f"https://cdn.cloudflare.steamstatic.com/steam/apps/{aid}/header.jpg",
    ]
    for url in candidates:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]}, method="HEAD")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return url
        except Exception:
            continue
    return f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{aid}/library_hero.jpg"


def _pick_best_steam_item(search_term: str, items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Select the Steam item that best matches the search term, preventing sequels (e.g. Subnautica 2) from hijacking base games."""
    if not items:
        return None
    st_norm = "".join(c for c in search_term.lower() if c.isalnum())
    st_words = set(re.findall(r"[a-z0-9]+", search_term.lower()))
    has_number = any(w.isdigit() or w in ["ii", "iii", "iv", "v"] for w in st_words)

    best_item = None
    best_score = -1.0

    for it in items:
        name = it.get("name", "")
        n_norm = "".join(c for c in name.lower() if c.isalnum())
        n_words = set(re.findall(r"[a-z0-9]+", name.lower()))

        score = 0.0
        # Exact match (e.g. "subnautica" == "subnautica")
        if n_norm == st_norm:
            score = 100.0
        # If user searched base game without number, penalize sequels (e.g. "2", "3")
        elif not has_number and any(w.isdigit() or w in ["ii", "iii", "iv", "v"] for w in (n_words - st_words)):
            score = 10.0
        # Substring or word overlap
        elif st_norm in n_norm:
            score = 50.0 - (len(n_norm) - len(st_norm))
        else:
            common = st_words & n_words
            score = len(common) * 10.0

        # Penalize soundtracks, DLCs, and skin packs
        if any(bad in name.lower() for bad in ["soundtrack", "dlc", "expansion", "pack", "artbook", "ost"]):
            score -= 30.0

        if score > best_score:
            best_score = score
            best_item = it

    return best_item or items[0]


def _resolve_steam_data_sync(game_title: str) -> Optional[Dict[str, Any]]:
    """Search Steam store API for the official game page, clean name, verified cover art, and wide banner."""
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
                top = _pick_best_steam_item(search_term, items) or items[0]
                appid = top["id"]
                image_url = _find_working_steam_image(appid, top.get("tiny_image"))
                banner_url = _find_working_steam_banner(appid)
                return {
                    "appid": appid,
                    "name": top["name"],
                    "steam_url": f"https://store.steampowered.com/app/{appid}/",
                    "portrait_url": image_url,
                    "banner_url": banner_url,
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


def _clean_pixeldrain_url(url: str) -> str:
    """Normalize any PixelDrain link back to official clean URL (https://pixeldrain.com/u/<id>)."""
    m = re.search(r"pixeldrain\.(?:com|eu\.cc|net|org)/(?:u|api/file|d)/([a-zA-Z0-9_-]+)", url)
    if m:
        return f"https://pixeldrain.com/u/{m.group(1)}"
    m2 = re.search(r"cdn\.pixeldrain\.eu\.cc/([a-zA-Z0-9_-]+)", url)
    if m2:
        return f"https://pixeldrain.com/u/{m2.group(1)}"
    m3 = re.search(r"projectsablinova\.workers\.dev/([a-zA-Z0-9_-]+)", url)
    if m3:
        return f"https://pixeldrain.com/u/{m3.group(1)}"
    return url


def _is_pixeldrain_alive(url: str) -> bool:
    """Check if a PixelDrain file is active and not removed for legal/DMCA reasons or deleted."""
    m = re.search(r"pixeldrain\.(?:com|eu\.cc|net|org)/(?:u|api/file|d)/([a-zA-Z0-9_-]+)", url)
    if not m:
        m = re.search(r"cdn\.pixeldrain\.eu\.cc/([a-zA-Z0-9_-]+)", url)
    if not m:
        m = re.search(r"projectsablinova\.workers\.dev/([a-zA-Z0-9_-]+)", url)
    if not m:
        return True
    file_id = m.group(1)
    try:
        req = urllib.request.Request(
            f"https://pixeldrain.com/api/file/{file_id}/info",
            headers={"User-Agent": HEADERS["User-Agent"]},
        )
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("success", True):
                return False
            if data.get("can_download") is False:
                return False
            if data.get("availability") in ("unavailable_for_legal_reasons", "deleted"):
                return False
            return True
    except urllib.error.HTTPError as err:
        if err.code in (404, 410, 451):
            return False
        return True
    except Exception:
        return True


def _gamebounty_state() -> str:
    """Generate the 30-second time-window HMAC token required by GameBounty's API."""
    step = int(time.time() / 30)
    msg = struct.pack(">Q", step)
    return hmac.new(GAMEBOUNTY_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _search_gamebounty_sync(query: str) -> List[Dict[str, Any]]:
    """Query GameBounty API for matching games."""
    sig = _gamebounty_state()
    url = f"https://gamebounty.world/api/v1/search?q={urllib.parse.quote(query)}&page=1&size=10"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "application/json",
            "x-gb-state": sig,
            "Referer": f"https://gamebounty.world/search?q={urllib.parse.quote(query)}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("data", {}).get("results", [])
            parsed = []
            for r in results:
                slug = r.get("slug")
                parsed.append({
                    "title": _clean_game_title(r.get("title", "")),
                    "slug": slug,
                    "url": f"https://gamebounty.world/{slug}-free-pc-download" if slug else "",
                    "appid": r.get("appid"),
                    "version": r.get("version"),
                    "size": r.get("size_human"),
                    "library_capsule": r.get("library_capsule"),
                    "source": "gamebounty",
                })
            return parsed
    except Exception as exc:
        log.debug("GameBounty search failed for %s: %s", query, exc)
    return []


def _extract_gamebounty_details_sync(slug: str) -> Optional[Dict[str, Any]]:
    """Fetch game details and direct mirrors from GameBounty."""
    sig = _gamebounty_state()
    url = f"https://gamebounty.world/api/v1/posts/{urllib.parse.quote(slug)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "application/json",
            "x-gb-state": sig,
            "Referer": f"https://gamebounty.world/{slug}-free-pc-download",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_data = json.loads(resp.read().decode("utf-8"))
            data = raw_data.get("data", {})
            if not data:
                return None

            title = _clean_game_title(data.get("title", "Unknown Game"))
            appid = data.get("appid")
            version = data.get("version")
            container = data.get("container", {}).get("data", {})
            size = container.get("sizeHuman", "Unknown")

            # Cover art and wide banner
            gb_image = data.get("library_capsule")
            gb_banner = data.get("banner")
            if appid:
                game_url = f"https://store.steampowered.com/app/{appid}/"
                image = gb_image or _find_working_steam_image(appid)
                banner = gb_banner or _find_working_steam_banner(appid)
                is_steam = True
            else:
                steam_info = _resolve_steam_data_sync(title)
                if steam_info:
                    game_url = steam_info["steam_url"]
                    image = gb_image or steam_info["portrait_url"]
                    banner = gb_banner or steam_info.get("banner_url")
                    is_steam = True
                else:
                    game_url = f"https://gamebounty.world/{slug}-free-pc-download"
                    image = gb_image or gb_banner
                    banner = gb_banner
                    is_steam = False

            downloads: List[Dict[str, str]] = []
            seen_urls = set()

            for mirror in container.get("mirrors", []):
                raw_m_name = mirror.get("name", "Direct Download")
                links = mirror.get("links", [])
                is_multipart = len(links) > 1

                for idx, link_obj in enumerate(links):
                    raw_dl_url = link_obj.get("url", "")
                    if not raw_dl_url:
                        continue

                    # Decode base64 payload from /api/dl/<slug>/<base64>
                    target_url = raw_dl_url
                    b64_part = raw_dl_url.split("/")[-1]
                    try:
                        padded = b64_part + "=" * (-len(b64_part) % 4)
                        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
                        if decoded.startswith("http://") or decoded.startswith("https://"):
                            target_url = decoded
                    except Exception:
                        pass

                    # Auto-resolve BZZHR links if present
                    if "bzzhr.to" in target_url or "buzzheavier.com" in target_url:
                        resolved = _resolve_bzzhr_direct(target_url)
                        if resolved:
                            target_url = resolved

                    # Clean PixelDrain links and filter out dead/DMCA files
                    if any(k in target_url.lower() for k in ["pixeldrain", "pixeldrain.eu.cc"]):
                        target_url = _clean_pixeldrain_url(target_url)
                        if not _is_pixeldrain_alive(target_url):
                            continue

                    if target_url in seen_urls:
                        continue
                    seen_urls.add(target_url)

                    # Determine host label
                    parsed = urllib.parse.urlparse(target_url)
                    domain = parsed.netloc.lower()
                    if domain.startswith("www."):
                        domain = domain[4:]

                    host_label = KNOWN_DOMAINS.get(domain)
                    if not host_label:
                        m_clean = raw_m_name.lower().replace("www.", "")
                        host_label = KNOWN_DOMAINS.get(m_clean, domain.split(".")[0].capitalize() if domain else "Direct Download")

                    if any(k in host_label.lower() or k in target_url.lower() for k in ["bzzhr", "buzzheavier"]):
                        host_label = "BZZHR"
                    elif any(k in host_label.lower() or k in target_url.lower() for k in ["pixeldrain", "projectsablinova", "pd-by", "pd-node"]):
                        host_label = "PixelDrain"

                    if is_multipart:
                        host_label = f"{host_label} (Part {idx + 1})"

                    downloads.append({"host": host_label, "url": target_url})

            return {
                "title": title,
                "url": game_url,
                "image": image,
                "banner": banner,
                "size": size,
                "version": version,
                "downloads": downloads,
                "is_steam": is_steam,
                "slug": slug,
            }
    except Exception as exc:
        log.warning("GameBounty details failed for %s: %s", slug, exc)
    return None


def _download_sort_key(item: Dict[str, str]) -> int:
    """Prioritize recommended hosts (BZZHR, PixelDrain) first, followed by other fast direct hosts."""
    h = item["host"].lower()
    u = item["url"].lower()
    if any(k in h or k in u for k in ["buzzheavier", "bzzhr"]):
        return 0
    if any(k in h or k in u for k in ["pixeldrain", "projectsablinova", "pd-by", "pd-node"]):
        return 1
    if "gofile" in h or "gofile" in u:
        return 2
    if "fileditch" in h or "fileditch" in u:
        return 3
    if "megadb" in h or "megadb" in u:
        return 4
    if "1fichier" in h or "1fichier" in u:
        return 5
    if "datanodes" in h or "datanodes" in u:
        return 6
    if "filekeeper" in h or "filekeeper" in u:
        return 7
    if "mocha" in h or "mocha" in u:
        return 8
    if "fileq" in h or "fileq" in u:
        return 9
    if "vikingfile" in h or "vikingfile" in u:
        return 10
    if "dropdrive" in h or "dropdrive" in u:
        return 11
    if "transfer" in h or "transfer" in u:
        return 12
    if "0807" in h or "0807" in u:
        return 13
    if "torrent" in h or "torrent" in u or "trnt" in u or "magnet:" in u:
        return 14
    return 15


_GOG_CACHE: Optional[List[Dict[str, Any]]] = None
_GOG_CACHE_TIME: float = 0.0
_GOG_CACHE_TTL: float = 3600.0  # 1 hour in-memory cache


def _fetch_gog_catalog_sync() -> List[Dict[str, Any]]:
    """Fetch and cache the full GOG Revived game catalog embedded in /search/."""
    global _GOG_CACHE, _GOG_CACHE_TIME
    now = time.time()
    if _GOG_CACHE is not None and (now - _GOG_CACHE_TIME) < _GOG_CACHE_TTL:
        return _GOG_CACHE

    url = "https://gog-rev.com/search/"
    try:
        body = _sync_fetch(url, timeout=15)
        if not body:
            return _GOG_CACHE or []
        m = re.search(r"initialGames\s*=\s*(\[.*?\]);", body)
        if m:
            data = json.loads(m.group(1))
            _GOG_CACHE = data
            _GOG_CACHE_TIME = now
            log.info("Fetched GOG catalog with %d games", len(data))
            return data
    except Exception as exc:
        log.warning("Failed to fetch GOG catalog: %s", exc)

    return _GOG_CACHE or []


def _search_gog_sync(query: str) -> List[Dict[str, Any]]:
    """Search GOG Revived catalog using query relevance scoring."""
    catalog = _fetch_gog_catalog_sync()
    if not catalog:
        return []

    q_lower = query.lower().strip()
    q_norm = re.sub(r"[^\w\s]", "", q_lower)
    results: List[Dict[str, Any]] = []

    for game in catalog:
        title = game.get("title", "")
        slug = game.get("slug", "")
        score = _query_relevance_score(query, title)
        if score > 0.0 or (q_norm and q_norm in re.sub(r"[^\w\s]", "", title.lower())):
            results.append({
                "title": title,
                "slug": slug,
                "url": f"https://gog-rev.com/games/{slug}/",
                "version": game.get("currentVersion", "N/A"),
                "size": game.get("downloadSizeBadge", "Unknown"),
                "cover": game.get("coverImage", ""),
                "platforms": game.get("platforms", {}),
                "score": score if score > 0.0 else 0.5,
                "source": "gog",
            })

    results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return results[:10]


def _unseal_gog_link(payload: str, key: List[int]) -> str:
    """Decrypt a GOG Revived sealed link payload using the page linkKey."""
    rem = len(payload) % 4
    if rem:
        payload += "=" * (4 - rem)
    try:
        data = base64.urlsafe_b64decode(payload)
        if len(data) < 4 or data[0] != 1:
            return ""
        n = data[1]
        a = 2 + n
        if n < 8 or a >= len(data):
            return ""
        c = data[2:a]
        s = data[a:]
        res = bytearray(len(s))
        for o in range(len(s)):
            l = key[(o + c[o % len(c)]) % len(key)]
            u = c[(o * 5 + 3) % len(c)]
            f = (l + u + ((o * 31 + 17) & 255)) & 255
            m = s[o] ^ f
            g = (o % 5) + 1
            t = g & 7
            res[o] = m & 255 if t == 0 else ((m >> t) | (m << (8 - t))) & 255
        return res.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_gog_details_sync(page_url: str, meta: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Extract and unseal GOG Revived direct download mirrors, version, and info."""
    if not page_url.endswith("/"):
        page_url += "/"
    body = _sync_fetch(page_url, timeout=15)
    if not body:
        return None

    key_m = re.search(r"linkKey\s*=\s*(\[[^\]]+\])", body)
    attr_m = re.search(r"linkPayloadAttribute\s*=\s*[\"\x27]([^\"]+)[\"\x27]", body)
    if not key_m or not attr_m:
        return None

    try:
        key = json.loads(key_m.group(1))
    except Exception:
        return None
    attr_name = attr_m.group(1)

    downloads = []
    seen = set()

    # Unseal mirror links
    button_pattern = re.compile(rf"<a[^>]+{attr_name}=[\"\x27]([^\"]+)[\"\x27][^>]*>", re.DOTALL)
    for m in button_pattern.finditer(body):
        tag = m.group(0)
        payload = m.group(1)
        host_m = re.search(r"data-host=[\"\x27]([^\"]+)[\"\x27]", tag)
        raw_host = host_m.group(1) if host_m else "Direct"
        link = _unseal_gog_link(payload, key)
        if link and link not in seen and link.startswith("http"):
            if "pixeldrain" in link or "pd-" in link:
                link = _clean_pixeldrain_url(link)
                if not _is_pixeldrain_alive(link):
                    continue
            domain = _extract_domain(link)
            host_clean = KNOWN_DOMAINS.get(domain)
            if not host_clean:
                host_clean = raw_host.capitalize() if raw_host else "Direct"
            seen.add(link)
            downloads.append({"host": host_clean, "url": link})

    # Unseal torrent / magnet links
    magnet_pattern = re.compile(rf"<button[^>]+copy-magnet-btn[^>]+{attr_name}=[\"\x27]([^\"]+)[\"\x27]", re.DOTALL)
    for m in magnet_pattern.finditer(body):
        payload = m.group(1)
        link = _unseal_gog_link(payload, key)
        if link and link not in seen and link.startswith("magnet:"):
            seen.add(link)
            downloads.append({"host": "Magnet", "url": link})

    downloads.sort(key=_download_sort_key)

    title = meta.get("title") if meta and meta.get("title") else "Game"
    version = meta.get("version", "N/A") if meta else "N/A"
    size = meta.get("size", "Unknown") if meta else "Unknown"
    cover = meta.get("cover", "") if meta else ""
    platforms = meta.get("platforms", {}) if meta else {}

    return {
        "title": title,
        "url": page_url,
        "version": version,
        "size": size,
        "cover": cover,
        "platforms": platforms,
        "downloads": downloads,
        "source": "gog",
    }


def _search_steamunderground_sync(query: str) -> List[Dict[str, Any]]:
    """Search steamunderground.net via WordPress REST API."""
    search_url = f"https://steamunderground.net/wp-json/wp/v2/posts?search={urllib.parse.quote(query)}&per_page=10"
    try:
        content = _sync_fetch(search_url, timeout=10)
        posts = json.loads(content)
        results: List[Dict[str, Any]] = []
        for p in posts:
            raw_title = p.get("title", {}).get("rendered", "")
            clean_title = _clean_game_title(raw_title)
            img_url = None
            if p.get("yoast_head_json") and p["yoast_head_json"].get("og_image"):
                og_imgs = p["yoast_head_json"]["og_image"]
                if isinstance(og_imgs, list) and og_imgs:
                    img_url = og_imgs[0].get("url")
            results.append({
                "title": clean_title,
                "raw_title": raw_title,
                "url": p.get("link", ""),
                "portrait_image": img_url,
                "source": "steamunderground",
            })
        return results
    except Exception as exc:
        log.warning("SteamUnderground search error: %s", exc)
        return []


def _search_worldofpcgames_sync(query: str) -> List[Dict[str, Any]]:
    """Search worldofpcgames.com via WordPress REST API."""
    search_url = f"https://worldofpcgames.com/wp-json/wp/v2/posts?search={urllib.parse.quote(query)}&per_page=10"
    try:
        content = _sync_fetch(search_url, timeout=10)
        posts = json.loads(content)
        results: List[Dict[str, Any]] = []
        for p in posts:
            raw_title = p.get("title", {}).get("rendered", "")
            clean_title = _clean_game_title(raw_title)
            img_url = None
            if p.get("aioseo_head_json") and p["aioseo_head_json"].get("og:image"):
                img_url = p["aioseo_head_json"]["og:image"]
            results.append({
                "title": clean_title,
                "raw_title": raw_title,
                "url": p.get("link", ""),
                "portrait_image": img_url,
                "source": "worldofpcgames",
            })
        return results
    except Exception as exc:
        log.warning("WorldOfPCGames search error: %s", exc)
        return []


def _is_same_game(t1: str, t2: str) -> bool:
    """Check if two game titles likely represent the exact same game."""
    s1 = "".join(c for c in t1.lower() if c.isalnum())
    s2 = "".join(c for c in t2.lower() if c.isalnum())
    if s1 == s2:
        return True

    # If numbers/sequels differ (e.g. Subnautica vs Subnautica 2), they are DIFFERENT games
    nums1 = re.findall(r"\d+", s1)
    nums2 = re.findall(r"\d+", s2)
    if nums1 != nums2:
        return False

    # Check Roman numerals
    romans = {"ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}
    w1 = set(re.findall(r"[a-z0-9]+", t1.lower()))
    w2 = set(re.findall(r"[a-z0-9]+", t2.lower()))
    if (w1 & romans) != (w2 & romans):
        return False

    # Check standalone subtitles: if one has distinct subtitle words that are not common edition terms
    edition_words = {"the", "a", "an", "of", "and", "in", "on", "for", "to", "with", "edition", "remastered", "deluxe", "complete", "goty", "vr", "cut", "directors", "anniversary", "enhanced"}
    diff = (w1 ^ w2) - edition_words
    if len(diff) >= 2:
        return False

    return s1 in s2 or s2 in s1


def _query_relevance_score(query: str, title: str) -> float:
    """Calculate relevance between user search query and game title (0.0 to 1.5).

    Returns 0.0 if there is zero overlap/relevance (e.g. false-positive WordPress search results).
    """
    q_clean = "".join(c for c in query.lower() if c.isalnum() or c.isspace()).strip()
    t_clean = "".join(c for c in title.lower() if c.isalnum() or c.isspace()).strip()
    if not q_clean or not t_clean:
        return 0.0

    q_norm = "".join(c for c in q_clean if c.isalnum())
    t_norm = "".join(c for c in t_clean if c.isalnum())

    if q_norm == t_norm:
        return 1.5
    if q_norm in t_norm:
        return 1.2
    if t_norm in q_norm:
        return 1.1

    q_words = set(q_clean.split())
    t_words = set(t_clean.split())
    stop_words = {"the", "a", "an", "of", "and", "in", "on", "for", "to", "with", "edition", "remastered"}
    sig_q = q_words - stop_words or q_words
    sig_t = t_words - stop_words or t_words
    common = sig_q & sig_t
    if common:
        return len(common) / max(len(sig_q), 1)

    # Acronym / initials check (e.g., gta -> Grand Theft Auto, cod -> Call of Duty)
    all_words = [w for w in t_clean.split() if w]
    initials_all = "".join(w[0] for w in all_words)
    initials_sig = "".join(w[0] for w in all_words if w not in stop_words)
    if q_norm and (q_norm in (initials_all, initials_sig) or initials_all.startswith(q_norm) or initials_sig.startswith(q_norm)):
        return 0.8

    return 0.0


class GameDL(commands.Cog):
    """Search and extract game direct download links from SteamRIP and GameBounty."""

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
        """Search SteamRIP for games matching query."""
        search_url = f"https://steamrip.com/?s={urllib.parse.quote(query)}"
        html_content = await self._fetch_html(search_url)
        if not html_content:
            return [], "Could not connect to SteamRIP database. Please try again later."

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
                "source": "steamrip",
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
                    "source": "steamrip",
                })

        # Filter and rank results by query relevance to discard false-positive comment matches
        filtered_results: List[Dict[str, Any]] = []
        for r in results:
            score = _query_relevance_score(query, r["title"])
            if score > 0.0:
                r["score"] = score
                filtered_results.append(r)

        filtered_results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return filtered_results, None

    async def _extract_details(self, page_url: str, default_image: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Extract SteamRIP game title, size, version, cover art, and direct download links."""
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

        # Title: Prefer official clean Steam title (if matching), else pure game name without (Build...)
        if steam_info and steam_info.get("name") and _is_same_game(cleaned_name, steam_info["name"]):
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

        # Wide landscape hero banner
        banner = steam_info.get("banner_url") if steam_info else None

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

            # Clean PixelDrain links and filter out dead/DMCA files
            if any(k in raw_url.lower() for k in ["pixeldrain", "pixeldrain.eu.cc"]):
                raw_url = _clean_pixeldrain_url(raw_url)
                is_alive = await asyncio.to_thread(_is_pixeldrain_alive, raw_url)
                if not is_alive:
                    continue

            if raw_url in seen_urls:
                continue
            seen_urls.add(raw_url)

            # Determine host label
            start_pos = m.start()
            preceding = html.unescape(body[max(0, start_pos - 400) : start_pos])
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

            # Normalize Buzzheavier / BZZHR label to BZZHR and PixelDrain label
            if any(k in host_label.lower() or k in raw_url.lower() for k in ["bzzhr", "buzzheavier"]):
                host_label = "BZZHR"
            elif any(k in host_label.lower() or k in raw_url.lower() for k in ["pixeldrain", "projectsablinova", "pd-by", "pd-node"]):
                host_label = "PixelDrain"

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

        # Game store page (defaults to Steam if matched)
        game_page_url = steam_info["steam_url"] if steam_info else page_url
        is_steam = bool(steam_info)

        return {
            "title": title,
            "url": game_page_url,
            "image": image,
            "banner": banner,
            "size": size,
            "version": version,
            "downloads": downloads,
            "is_steam": is_steam,
        }

    async def _search_gamebounty(self, query: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Search GameBounty for games matching query."""
        try:
            results = await asyncio.to_thread(_search_gamebounty_sync, query)
            filtered: List[Dict[str, Any]] = []
            for r in results:
                score = _query_relevance_score(query, r["title"])
                if score > 0.0:
                    r["score"] = score
                    filtered.append(r)
            filtered.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            return filtered, None
        except Exception as exc:
            log.warning("GameBounty search error: %s", exc)
            return [], str(exc)

    async def _extract_gamebounty_details(self, slug: str) -> Optional[Dict[str, Any]]:
        """Extract GameBounty game details and mirrors."""
        try:
            return await asyncio.to_thread(_extract_gamebounty_details_sync, slug)
        except Exception as exc:
            log.warning("GameBounty details error for %s: %s", slug, exc)
            return None

    async def _search_steamunderground(self, query: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Search SteamUnderground for games matching query."""
        try:
            results = await asyncio.to_thread(_search_steamunderground_sync, query)
            filtered: List[Dict[str, Any]] = []
            for r in results:
                score = _query_relevance_score(query, r["title"])
                if score > 0.0:
                    r["score"] = score
                    filtered.append(r)
            filtered.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            return filtered, None
        except Exception as exc:
            log.warning("SteamUnderground search error: %s", exc)
            return [], str(exc)

    async def _extract_steamunderground_details(self, page_url: str, meta: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Extract SteamUnderground game title, size, version, cover art, and direct download links."""
        body = await self._fetch_html(page_url)
        if not body:
            return None

        raw_name = meta.get("raw_title") if meta and meta.get("raw_title") else (meta.get("title") if meta and meta.get("title") else "Unknown Game")
        og_title = re.search(
            r'<meta\s+property=[\"\']og:title[\"\']\s+content=[\"\']([^\"\']+)[\"\']',
            body,
            re.IGNORECASE,
        )
        if og_title:
            raw_name = og_title.group(1)
        cleaned_name = _clean_game_title(raw_name)

        steam_info = await asyncio.to_thread(_resolve_steam_data_sync, cleaned_name)
        if steam_info and steam_info.get("name") and _is_same_game(cleaned_name, steam_info["name"]):
            title = steam_info["name"]
        else:
            title = cleaned_name

        if steam_info and steam_info.get("portrait_url"):
            image = steam_info["portrait_url"]
        else:
            image = meta.get("portrait_image") if meta else None
            if not image:
                og_img = re.search(
                    r'<meta\s+property=[\"\']og:image[\"\']\s+content=[\"\']([^\"\']+)[\"\']',
                    body,
                    re.IGNORECASE,
                )
                image = og_img.group(1).strip() if og_img else None

        banner = steam_info.get("banner_url") if steam_info else None

        ver_m = re.search(r'class=[\"\'][^\"\']*gameVersionValue[^\"\']*[\"\'][^>]*>([^<]+)<', body)
        if not ver_m:
            ver_m = re.search(r'\((?:v\s*|Build\s*)([0-9\.\_a-zA-Z\s\+]+)[^)]*\)', raw_name)
        version = html.unescape(ver_m.group(1).strip()) if ver_m else ""

        size_m = re.search(r'Size\s*:\s*([0-9\.]+\s*(?:GB|MB))', body, re.I)
        if not size_m:
            size_m = re.search(r'<strong>(?:Game\s+)?Size:\s*</strong>\s*([^<]+)', body, re.I)
        size = size_m.group(1).strip() if size_m else "Unknown"

        downloads = []
        seen_urls = set()
        buttons = re.findall(r'<a\s+[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>', body, re.DOTALL)
        for href, text in buttons:
            raw_url = href.strip()
            btn_text = re.sub(r'<[^>]+>', '', text).strip()
            if not raw_url.startswith("http"):
                continue
            if any(ign in raw_url for ign in ["steamunderground.net", "facebook.com", "twitter.com", "pinterest.com", "predb.net", "steampowered.com", "disqus.com"]):
                continue

            domain = _extract_domain(raw_url)
            host_label = KNOWN_DOMAINS.get(domain)
            if not host_label:
                if "torrent" in btn_text.lower() or "trnt" in raw_url.lower():
                    host_label = "Torrent"
                else:
                    continue

            if "torrent" in btn_text.lower() or "-trnt" in raw_url.lower() or "_trnt" in raw_url.lower():
                host_label = "Torrent"

            if "bzzhr" in raw_url.lower() or "buzzheavier" in raw_url.lower():
                res_bzzhr = _resolve_bzzhr_direct(raw_url)
                if res_bzzhr:
                    raw_url = res_bzzhr
                    host_label = "BZZHR"

            if "pixeldrain" in raw_url.lower():
                raw_url = _clean_pixeldrain_url(raw_url)
                if not _is_pixeldrain_alive(raw_url):
                    continue
                host_label = "PixelDrain"

            if raw_url not in seen_urls:
                seen_urls.add(raw_url)
                downloads.append({"host": host_label, "url": raw_url})

        game_page_url = steam_info["steam_url"] if steam_info else page_url
        return {
            "title": title,
            "url": game_page_url,
            "image": image,
            "banner": banner,
            "size": size,
            "version": version,
            "downloads": downloads,
            "is_steam": bool(steam_info),
            "source": "steamunderground",
        }

    async def _search_worldofpcgames(self, query: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Search WorldOfPCGames for games matching query."""
        try:
            results = await asyncio.to_thread(_search_worldofpcgames_sync, query)
            filtered: List[Dict[str, Any]] = []
            for r in results:
                score = _query_relevance_score(query, r["title"])
                if score > 0.0:
                    r["score"] = score
                    filtered.append(r)
            filtered.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            return filtered, None
        except Exception as exc:
            log.warning("WorldOfPCGames search error: %s", exc)
            return [], str(exc)

    async def _extract_worldofpcgames_details(self, page_url: str, meta: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Extract WorldOfPCGames game title, size, version, cover art, and direct download links."""
        body = await self._fetch_html(page_url)
        if not body:
            return None

        raw_name = meta.get("raw_title") if meta and meta.get("raw_title") else (meta.get("title") if meta and meta.get("title") else "Unknown Game")
        og_title = re.search(
            r'<meta\s+property=[\"\']og:title[\"\']\s+content=[\"\']([^\"\']+)[\"\']',
            body,
            re.IGNORECASE,
        )
        if og_title:
            raw_name = og_title.group(1)
        cleaned_name = _clean_game_title(raw_name)

        steam_info = await asyncio.to_thread(_resolve_steam_data_sync, cleaned_name)
        if steam_info and steam_info.get("name") and _is_same_game(cleaned_name, steam_info["name"]):
            title = steam_info["name"]
        else:
            title = cleaned_name

        if steam_info and steam_info.get("portrait_url"):
            image = steam_info["portrait_url"]
        else:
            image = meta.get("portrait_image") if meta else None
            if not image:
                og_img = re.search(
                    r'<meta\s+property=[\"\']og:image[\"\']\s+content=[\"\']([^\"\']+)[\"\']',
                    body,
                    re.IGNORECASE,
                )
                image = og_img.group(1).strip() if og_img else None

        banner = steam_info.get("banner_url") if steam_info else None

        ver_m = re.search(r'class=[\"\'][^\"\']*gameVersionValue[^\"\']*[\"\'][^>]*>([^<]+)<', body)
        if not ver_m:
            ver_m = re.search(r'\((?:v\s*|Build\s*)([0-9\.\_a-zA-Z\s\+]+)[^)]*\)', raw_name)
        version = html.unescape(ver_m.group(1).strip()) if ver_m else ""

        size_m = re.search(r'(?:Game\s+)?Size\s*:\s*([0-9\.]+\s*(?:GB|MB))', body, re.I)
        if not size_m:
            size_m = re.search(r'([0-9\.]+\s*(?:GB|MB))\s*(?:available space|storage)', body, re.I)
        size = size_m.group(1).strip() if size_m else "Unknown"

        downloads = []
        seen_urls = set()
        buttons = re.findall(r'<a\s+[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>', body, re.DOTALL)
        for href, text in buttons:
            raw_url = href.strip()
            btn_text = re.sub(r'<[^>]+>', '', text).strip()
            if not raw_url.startswith("http"):
                continue
            if any(ign in raw_url for ign in ["worldofpcgames.com", "facebook.com", "twitter.com", "pinterest.com", "steampowered.com", "disqus.com"]):
                continue

            domain = _extract_domain(raw_url)
            host_label = KNOWN_DOMAINS.get(domain)
            if not host_label:
                if "torrent" in btn_text.lower() or "trnt" in raw_url.lower():
                    host_label = "Torrent"
                else:
                    continue

            if "torrent" in btn_text.lower() or "-trnt" in raw_url.lower() or "_trnt" in raw_url.lower():
                host_label = "Torrent"

            if "bzzhr" in raw_url.lower() or "buzzheavier" in raw_url.lower():
                res_bzzhr = _resolve_bzzhr_direct(raw_url)
                if res_bzzhr:
                    raw_url = res_bzzhr
                    host_label = "BZZHR"

            if "pixeldrain" in raw_url.lower():
                raw_url = _clean_pixeldrain_url(raw_url)
                if not _is_pixeldrain_alive(raw_url):
                    continue
                host_label = "PixelDrain"

            if raw_url not in seen_urls:
                seen_urls.add(raw_url)
                downloads.append({"host": host_label, "url": raw_url})

        game_page_url = steam_info["steam_url"] if steam_info else page_url
        return {
            "title": title,
            "url": game_page_url,
            "image": image,
            "banner": banner,
            "size": size,
            "version": version,
            "downloads": downloads,
            "is_steam": bool(steam_info),
            "source": "worldofpcgames",
        }

    async def _search_gog(self, query: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Search GOG Revived for games matching query."""
        try:
            results = await asyncio.to_thread(_search_gog_sync, query)
            return results, None
        except Exception as exc:
            log.warning("GOG search error: %s", exc)
            return [], str(exc)

    async def _extract_gog_details(self, page_url: str, meta: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Extract and unseal GOG Revived direct download mirrors, version, and info."""
        try:
            return await asyncio.to_thread(_extract_gog_details_sync, page_url, meta)
        except Exception as exc:
            log.warning("GOG details error for %s: %s", page_url, exc)
            return None


class GogView(discord.ui.View):
    """Ephemeral view providing quick links for GOG downloads."""

    def __init__(self, gog_details: Dict[str, Any], timeout: float = 600.0):
        super().__init__(timeout=timeout)
        self.gog_details = gog_details

        # Recommended download button for GOG
        downloads = gog_details.get("downloads", [])
        for d in downloads:
            host = d.get("host", "")
            url = d.get("url", "")
            is_rec = any(
                k in host.lower() or k in url.lower()
                for k in ["pixeldrain", "buzzheavier", "bzzhr", "projectsablinova", "pd-by", "pd-node"]
            )
            if is_rec and len(url) <= 512:
                self.add_item(
                    discord.ui.Button(
                        label=f"⭐ Download ({host[:16]})",
                        url=url,
                        style=discord.ButtonStyle.link,
                    )
                )
                break

        # GOG page button
        if gog_details.get("url") and len(gog_details["url"]) <= 512:
            self.add_item(
                discord.ui.Button(
                    label="GOG Page",
                    url=gog_details["url"],
                    style=discord.ButtonStyle.link,
                )
            )


class GameDLMainView(discord.ui.View):
    """Interactive button view for GameDL main embed."""

    def __init__(
        self,
        downloads: List[Dict[str, Any]],
        details: Dict[str, Any],
        gog_details: Optional[Dict[str, Any]] = None,
        timeout: float = 600.0,
    ):
        super().__init__(timeout=timeout)
        self.details = details
        self.gog_details = gog_details

        # 1. Add ONLY recommended download buttons (user request: "remove all buttons except recommended")
        added_rec = 0
        for item in downloads:
            host_label = item["host"][:18]
            is_recommended = any(
                k in host_label.lower() or k in item["url"].lower()
                for k in ["buzzheavier", "bzzhr", "pixeldrain", "projectsablinova", "pd-by", "pd-node"]
            )
            if not is_recommended:
                continue
            if len(item["url"]) <= 512 and added_rec < 3:
                btn_text = f"⭐ Download ({host_label})"
                self.add_item(
                    discord.ui.Button(
                        label=btn_text[:80],
                        url=item["url"],
                        style=discord.ButtonStyle.link,
                    )
                )
                added_rec += 1

        # 2. Store / Game Page button
        store_button_label = "Steam Store" if details.get("is_steam") else "Game Page"
        if len(details.get("url", "")) <= 512 and details.get("url"):
            self.add_item(
                discord.ui.Button(
                    label=store_button_label[:80],
                    url=details["url"],
                    style=discord.ButtonStyle.link,
                )
            )

        # 3. GOG button (user request: "add gog button shows gog info and links if no gog then button wont show")
        if self.gog_details and self.gog_details.get("downloads"):
            gog_btn = discord.ui.Button(
                label="GOG Version",
                style=discord.ButtonStyle.secondary,
                emoji="💿",
            )
            gog_btn.callback = self.on_gog_click
            self.add_item(gog_btn)

    async def on_gog_click(self, interaction: discord.Interaction):
        if not self.gog_details:
            await interaction.response.send_message("No GOG release available for this game.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"💿 {self.gog_details.get('title', 'Game')} (GOG / DRM-Free)",
            url=self.gog_details.get("url"),
            description="📦 Standalone DRM-free installer release from GOG.",
            color=discord.Color.gold(),
        )
        if self.gog_details.get("cover"):
            embed.set_thumbnail(url=self.gog_details["cover"])

        embed.add_field(
            name="ℹ️ Version",
            value=f"`{self.gog_details.get('version', 'N/A')}`",
            inline=True,
        )
        embed.add_field(
            name="💾 File Size",
            value=f"`{self.gog_details.get('size', 'Unknown')}`",
            inline=True,
        )
        if self.gog_details.get("platforms"):
            plats = [k.capitalize() for k, v in self.gog_details["platforms"].items() if v]
            if plats:
                embed.add_field(
                    name="🖥️ Platforms",
                    value=", ".join(plats),
                    inline=True,
                )

        downloads = self.gog_details.get("downloads", [])
        if downloads:
            links_text = []
            for d in downloads:
                host = d.get("host", "Direct")
                url = d.get("url", "")
                is_rec = any(
                    k in host.lower() or k in url.lower()
                    for k in ["pixeldrain", "buzzheavier", "bzzhr", "projectsablinova", "pd-by", "pd-node"]
                )
                badge = " ⭐ *(Recommended)*" if is_rec else ""
                links_text.append(f"• [{host}]({url}){badge}")

            embed.add_field(
                name="📥 GOG Download Mirrors",
                value="\n".join(links_text)[:1000],
                inline=False,
            )
        else:
            embed.add_field(
                name="📥 GOG Download Mirrors",
                value="*No direct download links available.*",
                inline=False,
            )

        embed.set_footer(text="DRM-Free Offline Installer")

        sub_view = GogView(self.gog_details)
        await interaction.response.send_message(embed=embed, view=sub_view, ephemeral=True)

    async def _send_game_card(
        self,
        ctx: commands.Context,
        details: Dict[str, Any],
        other_matches: Optional[List[str]] = None,
        gog_details: Optional[Dict[str, Any]] = None,
    ):
        """Construct embed with field chunking, buttons, and send safely to Discord."""
        embed = discord.Embed(
            title=f"🎮 {details['title'][:250]}",
            url=details["url"][:512],
            color=discord.Color.from_rgb(147, 51, 234),
        )

        desc_lines = []
        if details.get("version"):
            desc_lines.append(f"🏷️ **Version:** `{details['version']}`")
        if details.get("size") and details["size"] != "Unknown":
            desc_lines.append(f"📦 **Game Size:** `{details['size']}`")
        if desc_lines:
            embed.description = "\n".join(desc_lines)[:4000]

        # Sort downloads: Recommended first (BZZHR, PixelDrain), then fast mirrors (Gofile, FileDitch, MegaDB, etc.)
        downloads = sorted(details.get("downloads", []), key=_download_sort_key)

        if downloads:
            dl_lines = []
            for item in downloads:
                host_name = item["host"]
                link_url = item["url"]
                is_recommended = any(k in host_name.lower() or k in link_url.lower() for k in ["buzzheavier", "bzzhr", "pixeldrain", "projectsablinova", "pd-by", "pd-node"])
                display_host = host_name
                if is_recommended and "⭐" not in display_host:
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
                    value="\n".join(chunk)[:1020],
                    inline=False,
                )
        else:
            embed.add_field(
                name="📥 Download Links",
                value=f"Visit the [Store Page]({details['url']}) for details.",
                inline=False,
            )

        if other_matches:
            other_text = "\n".join(other_matches)
            if len(other_text) > 1000:
                other_text = other_text[:990] + "..."
            embed.add_field(
                name="🔍 Other Matches",
                value=other_text,
                inline=False,
            )

        if details.get("image"):
            embed.set_thumbnail(url=details["image"])

        if details.get("banner"):
            embed.set_image(url=details["banner"])

        footer_parts = [f"Requested by {ctx.author.display_name}"]
        if gog_details and gog_details.get("downloads"):
            footer_parts.append("💿 GOG version available! Click the GOG button below.")
        embed.set_footer(
            text=" • ".join(footer_parts),
            icon_url=ctx.author.display_avatar.url if ctx.author.display_avatar else None,
        )

        # Interactive Link Buttons View (only recommended downloads, store page, and GOG button)
        view = GameDLMainView(downloads=downloads, details=details, gog_details=gog_details)

        # Sanitize embed field lengths
        for f_idx, field in enumerate(embed.fields):
            if len(field.value) > 1000:
                embed.set_field_at(
                    f_idx,
                    name=field.name[:250],
                    value=field.value[:990] + "...",
                    inline=field.inline,
                )

        try:
            await ctx.send(embed=embed, view=view)
        except Exception as send_err:
            log.warning("Sending with View failed (%s), attempting embed only", send_err)
            try:
                await ctx.send(embed=embed)
            except Exception as final_err:
                log.error("Failed to send embed: %s", final_err)
                safe_embed = discord.Embed(
                    title=f"🎮 {details['title'][:250]}",
                    url=details["url"][:512],
                    description=f"📦 **Game Size:** `{details.get('size', 'Unknown')}`",
                    color=discord.Color.blurple(),
                )
                safe_embed.add_field(
                    name="📥 Store Page",
                    value=f"[Click here to view game page]({details['url']})",
                    inline=False,
                )
                await ctx.send(embed=safe_embed)

    @commands.hybrid_command(
        name="gamedl",
        aliases=["gdl", "steamrip"],
        description="Search for a PC game across supported direct sources.",
    )
    @app_commands.describe(game="The name of the game or numeric Steam AppID to search for")
    @commands.cooldown(1, 3.0, commands.BucketType.user)
    async def gamedl(self, ctx: commands.Context, *, game: str):
        """Search for a PC game across supported sources with merged direct mirrors.

        Example:
            [p]gamedl terraria
            [p]gamedl cyberpunk 2077
            [p]gamedl 2651280
        """
        async with ctx.typing():
            # If query is a numeric AppID, resolve to official title first
            clean_q = game.strip()
            if clean_q.isdigit():
                steam_name = await asyncio.to_thread(_lookup_appid_sync, clean_q)
                if steam_name:
                    clean_q = steam_name

            # Concurrently search all 5 sources
            sr_task = self._search_games(clean_q)
            gb_task = self._search_gamebounty(clean_q)
            su_task = self._search_steamunderground(clean_q)
            wp_task = self._search_worldofpcgames(clean_q)
            gog_task = self._search_gog(clean_q)
            (
                (sr_results, sr_err),
                (gb_results, gb_err),
                (su_results, su_err),
                (wp_results, wp_err),
                (gog_results, gog_err),
            ) = await asyncio.gather(sr_task, gb_task, su_task, wp_task, gog_task)

            all_sources = {
                "steamrip": sr_results,
                "gamebounty": gb_results,
                "steamunderground": su_results,
                "worldofpcgames": wp_results,
            }

            if not any(all_sources.values()):
                if gog_results:
                    gog_top = gog_results[0]
                    gog_det = await self._extract_gog_details(gog_top["url"], meta=gog_top)
                    if gog_det and gog_det.get("downloads"):
                        await self._send_game_card(ctx, gog_det, other_matches=None, gog_details=None)
                        return

                msg = f"No results found matching **{clean_q}** across supported sources.\nTry searching with a shorter or alternative title."
                embed = discord.Embed(
                    title="🔍 Game Not Found",
                    description=msg,
                    color=discord.Color.orange(),
                )
                await ctx.send(embed=embed)
                return

            # Pick the top result from each source and score query relevance
            top_candidates = []
            for src, r_list in all_sources.items():
                if r_list:
                    top = r_list[0]
                    score = top.get("score", _query_relevance_score(clean_q, top["title"]))
                    top_candidates.append((score, src, top))

            # Highest relevance score determines our best match
            top_candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_src, best_top = top_candidates[0]

            # Extract details for all sources that match this target game
            extract_tasks = []
            if sr_results and _is_same_game(sr_results[0]["title"], best_top["title"]):
                extract_tasks.append(self._extract_details(sr_results[0]["url"], default_image=sr_results[0].get("portrait_image")))
            if gb_results and _is_same_game(gb_results[0]["title"], best_top["title"]):
                extract_tasks.append(self._extract_gamebounty_details(gb_results[0]["slug"]))
            if su_results and _is_same_game(su_results[0]["title"], best_top["title"]):
                extract_tasks.append(self._extract_steamunderground_details(su_results[0]["url"], meta=su_results[0]))
            if wp_results and _is_same_game(wp_results[0]["title"], best_top["title"]):
                extract_tasks.append(self._extract_worldofpcgames_details(wp_results[0]["url"], meta=wp_results[0]))

            # Check if GOG has a matching release for this game
            gog_extract_task = None
            if gog_results and _is_same_game(gog_results[0]["title"], best_top["title"]):
                gog_extract_task = self._extract_gog_details(gog_results[0]["url"], meta=gog_results[0])

            if gog_extract_task:
                extracted_list, gog_details = await asyncio.gather(
                    asyncio.gather(*extract_tasks),
                    gog_extract_task,
                )
            else:
                extracted_list = await asyncio.gather(*extract_tasks)
                gog_details = None

            valid_details = [d for d in extracted_list if d]

            if not valid_details:
                if gog_details and gog_details.get("downloads"):
                    await self._send_game_card(ctx, gog_details, other_matches=None, gog_details=None)
                    return
                embed = discord.Embed(
                    title="❌ Failed to Load Game Details",
                    description=f"Found games matching **{clean_q}**, but could not retrieve download links.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return

            # Merge all valid sources into primary details
            details = valid_details[0]
            seen_urls = {d["url"] for d in details.get("downloads", [])}

            for other in valid_details[1:]:
                for dl in other.get("downloads", []):
                    if dl["url"] not in seen_urls:
                        seen_urls.add(dl["url"])
                        details["downloads"].append(dl)

                if not details.get("version") and other.get("version"):
                    details["version"] = other["version"]

                if (not details.get("size") or details.get("size") == "Unknown") and other.get("size"):
                    details["size"] = other["size"]

                if other.get("image") and (
                    "library_600x900" in other["image"]
                    or "steamstatic" in other["image"]
                    or not details.get("image")
                    or "steamrip.com" in str(details.get("image", ""))
                ):
                    details["image"] = other["image"]

                if other.get("banner") and not details.get("banner"):
                    details["banner"] = other["banner"]

            # Compile other matches
            other_titles: List[str] = []
            seen_other: Set[str] = set()

            for score, src, top in top_candidates[1:]:
                c_title = _clean_game_title(top["title"])
                if not _is_same_game(c_title, details["title"]):
                    if c_title not in seen_other:
                        seen_other.add(c_title)
                        other_titles.append(f"• {c_title}")

            all_tail = sr_results[1:4] + gb_results[1:4] + su_results[1:4] + wp_results[1:4]
            for item in all_tail:
                c_title = _clean_game_title(item["title"])
                if _is_same_game(c_title, details["title"]):
                    continue
                if c_title not in seen_other:
                    seen_other.add(c_title)
                    other_titles.append(f"• {c_title}")

            await self._send_game_card(ctx, details, other_titles[:6], gog_details=gog_details)
