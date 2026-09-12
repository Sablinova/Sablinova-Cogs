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
    "mediafire.com": "MediaFire",
    "mega.nz": "MEGA",
    "dropgalaxy.com": "DropGalaxy",
    "krakenfiles.com": "KrakenFiles",
    "rapidgator.net": "RapidGator",
    "fileq.net": "FileQ",
    "0807.st": "0807",
}


def _clean_game_title(title: str) -> str:
    """Remove HTML entities, 'Free Download', versions, builds, and trailing branding."""
    clean = html.unescape(title)
    clean = re.sub(r"\bFree\s+Download\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"[\s»\-|–—]+\s*SteamRIP.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bSteamRIP\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bGameBounty\b", "", clean, flags=re.IGNORECASE)
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
    if "fileq" in h or "fileq" in u:
        return 6
    if "datanodes" in h or "datanodes" in u:
        return 7
    if "0807" in h or "0807" in u:
        return 8
    return 10


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

    async def _send_game_card(
        self,
        ctx: commands.Context,
        details: Dict[str, Any],
        other_matches: Optional[List[str]] = None,
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

        embed.set_footer(
            text=f"Requested by {ctx.author.display_name}",
            icon_url=ctx.author.display_avatar.url if ctx.author.display_avatar else None,
        )

        # Interactive Link Buttons View (Discord allows max 5 buttons per row)
        view = discord.ui.View()
        for item in downloads[:4]:
            host_label = item["host"][:18]
            is_recommended = any(k in host_label.lower() or k in item["url"].lower() for k in ["buzzheavier", "bzzhr", "pixeldrain", "projectsablinova", "pd-by", "pd-node"])
            btn_text = f"⭐ Download ({host_label})" if is_recommended else f"Download ({host_label})"
            if len(item["url"]) <= 512:
                view.add_item(
                    discord.ui.Button(
                        label=btn_text[:80],
                        url=item["url"],
                        style=discord.ButtonStyle.link,
                    )
                )

        store_button_label = "Steam Store" if details.get("is_steam") else "Game Page"
        if len(details["url"]) <= 512:
            view.add_item(
                discord.ui.Button(
                    label=store_button_label[:80],
                    url=details["url"],
                    style=discord.ButtonStyle.link,
                )
            )

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
        aliases=["gdl"],
        description="Search for a PC game across SteamRIP and GameBounty.",
    )
    @app_commands.describe(game="The name of the game or numeric Steam AppID to search for")
    @commands.cooldown(1, 3.0, commands.BucketType.user)
    async def gamedl(self, ctx: commands.Context, *, game: str):
        """Search for a PC game across SteamRIP and GameBounty with merged direct mirrors.

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

            # Concurrently search SteamRIP and GameBounty
            sr_task = self._search_games(clean_q)
            gb_task = self._search_gamebounty(clean_q)
            (sr_results, sr_err), (gb_results, gb_err) = await asyncio.gather(sr_task, gb_task)

            if not sr_results and not gb_results:
                msg = f"No results found matching **{clean_q}** on SteamRIP or GameBounty.\nTry searching with a shorter or alternative title."
                embed = discord.Embed(
                    title="🔍 Game Not Found",
                    description=msg,
                    color=discord.Color.orange(),
                )
                await ctx.send(embed=embed)
                return

            details: Optional[Dict[str, Any]] = None
            other_titles: List[str] = []

            # Case 1: Both sources returned matches
            if sr_results and gb_results:
                sr_top = sr_results[0]
                gb_top = gb_results[0]

                # Check if top results represent the same game
                if _is_same_game(sr_top["title"], gb_top["title"]):
                    sr_det_task = self._extract_details(sr_top["url"], default_image=sr_top.get("portrait_image"))
                    gb_det_task = self._extract_gamebounty_details(gb_top["slug"])
                    sr_det, gb_det = await asyncio.gather(sr_det_task, gb_det_task)

                    if sr_det and gb_det:
                        details = sr_det
                        # Merge GameBounty mirrors into SteamRIP downloads
                        seen_urls = {d["url"] for d in details.get("downloads", [])}
                        for gb_dl in gb_det.get("downloads", []):
                            if gb_dl["url"] not in seen_urls:
                                seen_urls.add(gb_dl["url"])
                                details["downloads"].append(gb_dl)
                        # Pick best version/size if one is missing
                        if not details.get("version") and gb_det.get("version"):
                            details["version"] = gb_det["version"]
                        if (not details.get("size") or details.get("size") == "Unknown") and gb_det.get("size"):
                            details["size"] = gb_det["size"]
                        # Prefer verified Steam CDN cover from GameBounty (e.g. hashed library_600x900)
                        if gb_det.get("image") and (
                            "library_600x900" in gb_det["image"]
                            or "steamstatic" in gb_det["image"]
                            or not details.get("image")
                            or "steamrip.com" in str(details.get("image", ""))
                        ):
                            details["image"] = gb_det["image"]
                        # Merge banner if missing
                        if gb_det.get("banner") and not details.get("banner"):
                            details["banner"] = gb_det["banner"]
                    else:
                        details = sr_det or gb_det
                else:
                    # Top games differ: pick the source with higher query relevance score
                    sr_score = sr_top.get("score", _query_relevance_score(clean_q, sr_top["title"]))
                    gb_score = gb_top.get("score", _query_relevance_score(clean_q, gb_top["title"]))
                    if gb_score > sr_score:
                        details = await self._extract_gamebounty_details(gb_top["slug"])
                        if not details:
                            details = await self._extract_details(sr_top["url"], default_image=sr_top.get("portrait_image"))
                    else:
                        details = await self._extract_details(sr_top["url"], default_image=sr_top.get("portrait_image"))
                        if not details:
                            details = await self._extract_gamebounty_details(gb_top["slug"])

                # Compile other matches from both sources
                seen_other: Set[str] = set()
                candidate_others = []
                if not _is_same_game(sr_top["title"], gb_top["title"]):
                    runner_up = sr_top if gb_score > sr_score else gb_top
                    candidate_others.append(runner_up)
                candidate_others.extend(sr_results[1:5] + gb_results[1:5])

                for item in candidate_others:
                    c_title = _clean_game_title(item["title"])
                    if details and _is_same_game(c_title, details["title"]):
                        continue
                    if c_title not in seen_other:
                        seen_other.add(c_title)
                        other_titles.append(f"• {c_title}")

            # Case 2: Only SteamRIP returned matches
            elif sr_results:
                sr_top = sr_results[0]
                details = await self._extract_details(sr_top["url"], default_image=sr_top.get("portrait_image"))
                for item in sr_results[1:5]:
                    c_title = _clean_game_title(item["title"])
                    other_titles.append(f"• {c_title}")

            # Case 3: Only GameBounty returned matches
            elif gb_results:
                gb_top = gb_results[0]
                details = await self._extract_gamebounty_details(gb_top["slug"])
                for item in gb_results[1:5]:
                    c_title = _clean_game_title(item["title"])
                    other_titles.append(f"• {c_title}")

            if not details:
                embed = discord.Embed(
                    title="❌ Failed to Load Game Details",
                    description=f"Found games matching **{clean_q}**, but could not retrieve download links.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return

            await self._send_game_card(ctx, details, other_titles[:6])
