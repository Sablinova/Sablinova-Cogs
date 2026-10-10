import asyncio
import re
from datetime import datetime, timezone
from typing import Optional, Literal

import io
import discord
import requests
import json
import aiohttp
import time
from bs4 import BeautifulSoup
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red

# ─── Static config ─────────────────────────────────────────────────────────
MAX_GAMES = 100

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# Hardcoded depot allowlists for games with region-specific depots or unknown depot
GLOBAL_DEPOTS : set[str] = {
    "3340991",
    "3340992",
    "3340993",
    "3893181",
    "1716751",
}

DEPOT_ALLOWLIST = {
    "491540": ["491541", "491542", "897493", "897498", "898611", "898624", "898626"],
}

DEPOT_BLACKLIST = {
    "3764200": ["3764202", "3764204", "3764205", "3764206"],
    "2840770": ["2840772", "2840773", "2840774", "2840775"],
    "2424110": ["2424112"],
    "2928600": ["2928602"],
    "3059520": ["3059525", "3059526", "3893181"],
    "1761390": ["1887032", "1761392"],
    "1142710": ["372533"],
    "1029690": ["1363480", "2080150"],
    "2169200": ["2561510"],
    "801800": ["2217830"],
    "2054970": ["2054972", "2054974", "2757100", "2757110", "2757150", "2757160", "2757180", "2757190", "2757200", "2757210"],
    "1490890": ["1490892", "1490893", "1490894"],
}

SUBDLC_APPIDS = {
    "1364780": ["1792750", "1792751"],
    "2161700": ["2517300", "2517310"],
    "1273400": ["2153870"],
    "1490890": ["1777140"],
    "491540": ["3544250"],
    "2361770": ["3381250"],
}

OSLIST_FILTER = ["windows"]


# ─── Steam helpers ─────────────────────────────────────────────────────────
def format_size(size_bytes: int) -> str:
    if size_bytes >= 1_073_741_824:
        return f"{size_bytes / 1_073_741_824:.2f} GB"
    elif size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.2f} MB"
    elif size_bytes >= 1_024:
        return f"{size_bytes / 1_024:.2f} KB"
    else:
        return f"{size_bytes} B"


EXACT_RELEASE_PRECISIONS = {"date_full"}
REMINDER_WINDOW = 24 * 60 * 60  # seconds before release_ts to send the reminder


def exact_release_ts(info: dict) -> Optional[int]:
    """Steam's release timestamp, only when it's a full (exact) date."""
    ts = info.get("release_ts")
    if ts and info.get("release_precision") in EXACT_RELEASE_PRECISIONS:
        return int(ts)
    return None


# Steam's placeholder texts when no real date is known; not worth storing.
_NO_DATE_LABELS = {"coming soon", "to be announced", "tba", "tbd"}
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def label_sort_ts(label: Optional[str]) -> Optional[float]:
    """Rough sort key for a label like "2027", "Q4 2026" or "March 2027"
    (start of that period, UTC). None if no year can be found."""
    if not label:
        return None
    y = re.search(r"\b(19|20)\d{2}\b", label)
    if not y:
        return None
    month = 1
    q = re.search(r"\bq([1-4])\b", label, re.I)
    if q:
        month = (int(q.group(1)) - 1) * 3 + 1
    else:
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", label, re.I)
        if m:
            month = _MONTHS[m.group(1).lower()]
    return datetime(int(y.group(0)), month, 1, tzinfo=timezone.utc).timestamp()


def release_display(snapshot: dict) -> Optional[str]:
    """Exact date from release_ts when known; otherwise the rough label
    (e.g. "2027", "Q4 2026"); None if neither is available."""
    ts = exact_release_ts(snapshot)
    if ts:
        return f"<t:{ts}:F> (<t:{ts}:R>)"
    return snapshot.get("release_label") or None


def fetch_app_details(appid: int) -> dict:
    try:
        r = requests.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": appid, "cc": "us", "l": "en"},
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        resp_json = r.json()
        if not resp_json:
            return {}
        result = resp_json.get(str(appid))
        if not result:
            for item in resp_json.values():
                if isinstance(item, dict) and str(item.get("data", {}).get("steam_appid")) == str(appid):
                    result = item
                    break
            if not result and len(resp_json) == 1:
                result = next(iter(resp_json.values()))
        return result.get("data", {}) if (result and result.get("success")) else {}
    except Exception:
        return {}


def fetch_release_info(appid: int) -> dict:
    """Fetch a precise release timestamp from Steam's IStoreBrowseService.

    Returns ``{"release_ts": int|None, "precision": str|None,
    "coming_soon": bool|None}``; empty dict on failure.
    """
    try:
        input_json = json.dumps({
            "ids": [{"appid": appid}],
            "context": {"language": "english", "country_code": "US"},
            "data_request": {"include_release": True},
        })
        r = requests.get(
            "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
            params={"input_json": input_json},
            headers=HEADERS, timeout=10,
        )
        r.raise_for_status()
        items = r.json().get("response", {}).get("store_items", [])
        item = next(
            (i for i in items if str(i.get("appid")) == str(appid)),
            items[0] if items else None,
        )
        release = (item or {}).get("release", {})
        ts = release.get("steam_release_date")
        return {
            "release_ts": int(ts) if ts else None,
            "precision": release.get("coming_soon_display"),
            "coming_soon": release.get("is_coming_soon"),
        }
    except Exception:
        return {}

def fetch_advance_access(appid: int) -> Optional[int]:
    """Return the earliest *upcoming* advanced-access start (unix ts) across
    every edition/package of `appid`, or None if there isn't one."""
    def _get_items(ids):
        input_json = json.dumps({
            "ids": ids,
            "context": {"language": "english", "country_code": "US"},
            "data_request": {"include_release": True, "include_all_purchase_options": True},
        })
        r = requests.get(
            "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
            params={"input_json": input_json},
            headers=HEADERS, timeout=10,
        )
        r.raise_for_status()
        return r.json().get("response", {}).get("store_items", [])

    try:
        candidates: list[int] = []
        package_ids: set[int] = set()

        def scan(item: dict):
            aa = (item.get("release") or {}).get("advance_access_date")
            if aa:
                candidates.append(int(aa))
            options = list(item.get("purchase_options") or [])
            options.append(item.get("best_purchase_option") or {})
            for opt in options:
                if opt.get("packageid"):
                    package_ids.add(int(opt["packageid"]))

        for item in _get_items([{"appid": appid}]):
            scan(item)

        details = fetch_app_details(appid)
        for group in details.get("package_groups", []) or []:
            for sub in group.get("subs", []) or []:
                if sub.get("packageid"):
                    package_ids.add(int(sub["packageid"]))

        if package_ids:
            for item in _get_items([{"packageid": p} for p in sorted(package_ids)]):
                scan(item)

        now = time.time()
        upcoming = [t for t in candidates if t > now]
        return min(upcoming) if upcoming else None
    except Exception:
        return None


def fetch_build_id_only(appid: int) -> tuple:
    """Fetch only build ID and timestamp for regular check cycles."""
    try:
        r = requests.get(
            f"https://api.steamcmd.net/v1/info/{appid}",
            headers=HEADERS, timeout=10
        )
        data = r.json()
        public_branch = (
            data.get("data", {})
                .get(str(appid), {})
                .get("depots", {})
                .get("branches", {})
                .get("public", {})
        )
        build_id = public_branch.get("buildid")
        timeupdated = public_branch.get("timeupdated")
        return str(build_id) if build_id else None, int(timeupdated) if timeupdated else None
    except Exception:
        return None, None


def fetch_build_id(appid: int) -> tuple:
    try:
        r = requests.get(
            f"https://api.steamcmd.net/v1/info/{appid}",
            headers=HEADERS, timeout=10
        )
        data = r.json()
        depots = (
            data.get("data", {})
                .get(str(appid), {})
                .get("depots", {})
        )
        public_branch = depots.get("branches", {}).get("public", {})
        build_id = public_branch.get("buildid")
        timeupdated = public_branch.get("timeupdated")

        allowlist = DEPOT_ALLOWLIST.get(str(appid))
        blacklist = DEPOT_BLACKLIST.get(str(appid), [])
        manifests = {}
        depot_sizes = {}

        has_english_depot = any(
            d.get("config", {}).get("language") == "english"
            for did, d in depots.items()
            if did.isdigit()
        )

        for depot_id, depot_info in depots.items():
            if not depot_id.isdigit():
                continue
            if depot_id in GLOBAL_DEPOTS:
                continue
            if allowlist and depot_id not in allowlist:
                continue
            if not allowlist and depot_id in blacklist:
                continue

            depot_language = depot_info.get("config", {}).get("language")
            if not allowlist and depot_language:
                if not (has_english_depot and depot_language == "english"):
                    continue

            depot_oslist = depot_info.get("config", {}).get("oslist")
            if depot_oslist and not any(os_ in depot_oslist for os_ in OSLIST_FILTER):
                continue

            manifest = depot_info.get("manifests", {}).get("public")
            if isinstance(manifest, dict):
                manifest_id = manifest.get("gid")
                size = int(manifest.get("size", 0))
            else:
                manifest_id = manifest
                size = 0

            if manifest_id:
                manifests[depot_id] = str(manifest_id)
            if size > 0:
                depot_sizes[depot_id] = size

        # Fetch and merge sub-DLC depots
        for dlc_appid in SUBDLC_APPIDS.get(str(appid), []):
            try:
                r2 = requests.get(
                    f"https://api.steamcmd.net/v1/info/{dlc_appid}",
                    headers=HEADERS, timeout=10
                )
                dlc_data = r2.json()
                dlc_depots = (
                    dlc_data.get("data", {})
                            .get(str(dlc_appid), {})
                            .get("depots", {})
                )
                for depot_id, depot_info in dlc_depots.items():
                    if not depot_id.isdigit():
                        continue
                    if depot_id in GLOBAL_DEPOTS:
                        continue

                    depot_oslist = depot_info.get("config", {}).get("oslist")
                    if depot_oslist and not any(os_ in depot_oslist for os_ in OSLIST_FILTER):
                        continue

                    manifest = depot_info.get("manifests", {}).get("public")
                    if isinstance(manifest, dict):
                        manifest_id = manifest.get("gid")
                        size = int(manifest.get("size", 0))
                    else:
                        manifest_id = manifest
                        size = 0

                    if manifest_id:
                        manifests[depot_id] = str(manifest_id)
                    if size > 0:
                        depot_sizes[depot_id] = size
            except Exception:
                pass

        return str(build_id) if build_id else None, int(timeupdated) if timeupdated else None, manifests, depot_sizes
    except Exception:
        return None, None, {}, {}

def get_dlc_appids_from_steamcmd(appid: int) -> list:
    try:
        r = requests.get(
            f"https://api.steamcmd.net/v1/info/{appid}",
            headers=HEADERS, timeout=10
        )
        data = r.json()
        app_data = data.get("data", {}).get(str(appid), {})
        extended = app_data.get("extended", {})
        listofdlc = extended.get("listofdlc", "")
        if not listofdlc:
            return []
        return [int(x) for x in listofdlc.split(",") if x.strip().isdigit()]
    except Exception:
        return []

def _is_valid_app_page(appid: int, soup: BeautifulSoup, final_url: str) -> bool:
    """Detects Steam maintenance/interstitial/redirect pages so they don't get
    misread as a real 'no denuvo' result."""
    if f"/app/{appid}" not in final_url:
        return False
    if not soup.select_one("div.apphub_AppName"):
        return False
    return True


def check_denuvo_api(data: dict) -> bool:
    return "denuvo" in data.get("drm_notice", "").lower()


def check_denuvo_scrape(appid: int) -> Optional[bool]:
    """Returns True/False if the scrape succeeded, None if the page load failed
    or looked like a maintenance/interstitial page rather than a real app page."""
    try:
        r = requests.get(
            f"https://store.steampowered.com/app/{appid}/",
            headers=HEADERS,
            cookies={"birthtime": "0", "mature_content": "1"},
            timeout=10
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        if not _is_valid_app_page(appid, soup, str(r.url)):
            return None
        return "denuvo" in soup.get_text().lower()
    except Exception:
        return None

def has_denuvo(appid: int, data: dict) -> bool:
    scrape_result = check_denuvo_scrape(appid)
    if scrape_result is not None:
        return scrape_result
    return check_denuvo_api(data)


def search_steam(query: str) -> list:
    try:
        r = requests.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": query, "cc": "us", "l": "en"},
            headers=HEADERS, timeout=10
        )
        items = r.json().get("items", [])
        return [{"appid": i["id"], "name": i["name"]} for i in items]
    except Exception:
        return []


def get_game_snapshot(appid: int) -> Optional[dict]:
    data = fetch_app_details(appid)
    if not data:
        return None
    build_id, build_time = fetch_build_id_only(appid)
    release = data.get("release_date", {})
    coming_soon = release.get("coming_soon", False)
    coming_soon = bool(coming_soon) if isinstance(coming_soon, bool) else coming_soon == "true"
    release_label = release.get("date", "").strip() if coming_soon else ""
    if release_label.lower() in _NO_DATE_LABELS:
        release_label = ""
    rel = fetch_release_info(appid) if coming_soon else {}
    return {
        "name": data.get("name", f"AppID {appid}"),
        "denuvo": has_denuvo(appid, data),
        "header": data.get("header_image", ""),
        "build_id": build_id,
        "build_time": build_time,
        "coming_soon": coming_soon,
        "release_label": release_label or None,
        "release_ts": rel.get("release_ts"),
        "release_precision": rel.get("precision"),
    }

_TRADEMARK_CHARS = "™®©"
_PUNCT_RE = re.compile(r"[:\-\u2013\u2014_'’,.!?\"“”«»]")
_WS_RE = re.compile(r"\s+")

_ROMAN_NUMERAL_RE = re.compile(
    r'^(?=[MDCLXVI])M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$'
)
_ROMAN_VALUES = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}

def roman_to_int(word: str) -> Optional[int]:
    """Returns the integer value if `word` is a valid Roman numeral, else None."""
    w = word.upper()
    if not _ROMAN_NUMERAL_RE.match(w):
        return None
    total, prev = 0, 0
    for ch in reversed(w):
        val = _ROMAN_VALUES[ch]
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total

def convert_roman_numerals(text: str) -> str:
    """Replaces standalone Roman-numeral words with their Arabic equivalent."""
    words = text.split()
    return " ".join(
        str(roman_to_int(w)) if roman_to_int(w) is not None else w
        for w in words
    )

def normalize_game_name(name: str) -> str:
    if not name:
        return ""
    name = name.strip("\"' \t\r\n")
    for ch in _TRADEMARK_CHARS:
        name = name.replace(ch, "")
    name = _PUNCT_RE.sub(" ", name)
    name = _WS_RE.sub(" ", name).strip()
    name = convert_roman_numerals(name)
    name = name.lower()
    return name

async def resolve_best_game_match(query: str, app_type: str = "game") -> Optional[int]:
    """Search Steam and return the AppID of the best-matching item of the given
    Steam store type ('game', 'dlc', 'demo', 'application', 'video', 'music', 'hardware')."""
    raw_candidates = await asyncio.to_thread(search_steam, query)
    raw_candidates = raw_candidates[:10]
    if not raw_candidates:
        return None

    matched_candidates = []
    for c in raw_candidates:
        details = await asyncio.to_thread(fetch_app_details, c["appid"])
        if details.get("type") == app_type:
            matched_candidates.append(c)
        if len(matched_candidates) >= 5:
            break

    if not matched_candidates:
        return None

    query_norm = normalize_game_name(query)

    exact = [c for c in matched_candidates if normalize_game_name(c["name"]) == query_norm]
    if exact:
        return exact[0]["appid"]

    starts = [c for c in matched_candidates if normalize_game_name(c["name"]).startswith(query_norm)]
    if starts:
        return starts[0]["appid"]

    starts_rev = [c for c in matched_candidates if query_norm.startswith(normalize_game_name(c["name"]))]
    if starts_rev:
        return starts_rev[0]["appid"]

    query_words = query_norm.split()
    word_matches = [
        c for c in matched_candidates
        if all(w in normalize_game_name(c["name"]) for w in query_words)
    ]
    if word_matches:
        return word_matches[0]["appid"]

    return matched_candidates[0]["appid"]

# ─── Embed builders ────────────────────────────────────────────────────────
def build_denuvo_embed(appid: int, change_type: str, old: dict, new: dict) -> discord.Embed:
    name = new.get("name", old.get("name", f"AppID {appid}"))
    url = f"https://store.steampowered.com/app/{appid}/"
    if change_type == "denuvo_removed":
        embed = discord.Embed(
            title="🎉 Denuvo Removed!",
            description=f"**[{name}]({url})** no longer has Denuvo anti-tamper.",
            color=discord.Color.green()
        )
        embed.add_field(name="Before", value="⚠️ Had Denuvo", inline=True)
        embed.add_field(name="After", value="✅ Denuvo-free", inline=True)
    else:
        embed = discord.Embed(
            title="⚠️ Denuvo Added",
            description=f"**[{name}]({url})** now has Denuvo anti-tamper.",
            color=discord.Color.red()
        )
        embed.add_field(name="Before", value="✅ Denuvo-free", inline=True)
        embed.add_field(name="After", value="⚠️ Has Denuvo", inline=True)
    if new.get("header"):
        embed.set_thumbnail(url=new["header"])
    embed.set_footer(text=f"AppID {appid} • DenuvoWatch")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_depot_embed(appid: int, old_build: str, new_build: str, new: dict) -> discord.Embed:
    name = new.get("name", f"AppID {appid}")
    url = f"https://store.steampowered.com/app/{appid}/"
    embed = discord.Embed(
        title="🔧 Build Updated",
        description=f"**[{name}]({url})** received a new build.",
        color=discord.Color.blue()
    )
    embed.add_field(name="Build Change", value=f"`{old_build}` → `{new_build}`", inline=True)
    if new.get("new_build_size_bytes"):
        old_bytes = new.get("old_build_size_bytes", 0)
        new_bytes = new["new_build_size_bytes"]
        if old_bytes and old_bytes != new_bytes:
            diff = new_bytes - old_bytes
            diff_str = f"+{format_size(abs(diff))}" if diff > 0 else f"-{format_size(abs(diff))}"
            size_value = f"`{format_size(old_bytes)}` → `{format_size(new_bytes)}` (`{diff_str}`)"
        else:
            size_value = f"`{format_size(new_bytes)}`"
        embed.add_field(name="Build Size", value=size_value, inline=True)
    if new.get("build_time"):
        embed.add_field(name="\u200b", value="\u200b", inline=False)
        embed.add_field(name="Build Pushed", value=f"<t:{new['build_time']}:T>", inline=True)
        embed.add_field(name="Patch Notes", value=f"[View on SteamDB](https://steamdb.info/patchnotes/{new_build})", inline=True)
    if new.get("header"):
        embed.set_thumbnail(url=new["header"])
    embed.set_footer(text=f"AppID {appid} • DenuvoWatch")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_release_embed(appid: int, old: dict, new: dict) -> discord.Embed:
    name = new.get("name", old.get("name", f"AppID {appid}"))
    url = f"https://store.steampowered.com/app/{appid}/"
    embed = discord.Embed(
        title="🚀 Game Released!",
        description=f"**[{name}]({url})** is now available.",
        color=discord.Color.gold()
    )
    expected = release_display(old)
    if expected:
        embed.add_field(name="Expected Date", value=expected, inline=True)
    if new.get("build_id"):
        embed.add_field(name="Build ID", value=f"`{new['build_id']}`", inline=True)
    embed.add_field(name="Denuvo", value="⚠️ Yes" if new.get("denuvo") else "✅ No", inline=True)
    if new.get("header"):
        embed.set_thumbnail(url=new["header"])
    embed.set_footer(text=f"AppID {appid} • DenuvoWatch")
    embed.timestamp = datetime.now(timezone.utc)
    return embed

def build_advance_access_embed(appid: int, info: dict) -> discord.Embed:
    name = info.get("name", f"AppID {appid}")
    url = f"https://store.steampowered.com/app/{appid}/"
    embed = discord.Embed(
        title="✈️ Advanced Access Started!",
        description=f"**[{name}]({url})** advanced access is now live for eligible editions.",
        color=discord.Color.orange()
    )
    ts = info.get("advance_access_ts")
    if ts:
        embed.add_field(name="Started", value=f"<t:{ts}:F>", inline=True)
    full_release = release_display(info)
    if full_release:
        embed.add_field(name="Full Release", value=full_release, inline=True)
    embed.add_field(name="Denuvo", value="⚠️ Yes" if info.get("denuvo") else "✅ No", inline=True)
    if info.get("header"):
        embed.set_thumbnail(url=info["header"])
    embed.set_footer(text=f"AppID {appid} • DenuvoWatch")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_release_reminder_embed(appid: int, info: dict) -> discord.Embed:
    name = info.get("name", f"AppID {appid}")
    url = f"https://store.steampowered.com/app/{appid}/"
    ts = info["release_ts"]
    embed = discord.Embed(
        title="⏰ Releasing Soon",
        description=f"**[{name}]({url})** releases <t:{ts}:R>.",
        color=discord.Color.teal()
    )
    embed.add_field(name="Release", value=f"<t:{ts}:F>", inline=True)
    aa = info.get("advance_access_ts")
    if aa:
        embed.add_field(name="Advanced Access", value=f"<t:{aa}:F>", inline=True)
    embed.add_field(name="Denuvo", value="⚠️ Yes" if info.get("denuvo") else "✅ No", inline=True)
    if info.get("header"):
        embed.set_thumbnail(url=info["header"])
    embed.set_footer(text=f"AppID {appid} • DenuvoWatch")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


# ─── UI ────────────────────────────────────────────────────────────────────
class ListView(discord.ui.View):
    def __init__(
        self,
        ctx: commands.Context,
        games: list,
        embed_color: discord.Color,
        max_games: int = 100,
        timeout: float = 60.0
    ):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.games = games
        self.embed_color = embed_color
        self.max_games = max_games
        self.page = 0
        self.page_size = 25
        self.total_pages = max(1, (len(games) + self.page_size - 1) // self.page_size)
        self.message: discord.Message | None = None
        self._embed_cache: dict[int, discord.Embed] = {}

        self._warm_neighbors()
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Lock view interaction to the command invoker."""
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "You cannot interact with this menu.",
                ephemeral=True
            )
            return False
        return True

    def _build_embed(self, page: int) -> discord.Embed:
        start = page * self.page_size
        slice_ = self.games[start : start + self.page_size]

        embed = discord.Embed(
            title=f"🎮 Steam Watchlist ({len(self.games)}/{self.max_games})",
            color=self.embed_color,
        )

        if not slice_:
            embed.description = "*No games in watchlist.*"
            embed.set_footer(text="Page 1/1")
            return embed

        lines = []
        for appid_str, info in slice_:
            icon = "⚠️" if info.get("denuvo") else "✅"
            build = (
                f" • build `{info['build_id']}`"
                if info.get("build_id") and not info.get("coming_soon")
                else ""
            )
            lines.append(f"{icon} **{info.get('name', 'Unknown')}** `{appid_str}`{build}")

        embed.description = "\n".join(lines)
        embed.set_footer(
            text=f"Page {page + 1}/{self.total_pages} • ⚠️ = has Denuvo   ✅ = no Denuvo"
        )
        return embed

    def _get_or_build(self, page: int) -> discord.Embed:
        if page not in self._embed_cache:
            self._embed_cache[page] = self._build_embed(page)
        return self._embed_cache[page]

    def _warm_neighbors(self):
        keep = {self.page}
        if self.page > 0:
            keep.add(self.page - 1)
        if self.page < self.total_pages - 1:
            keep.add(self.page + 1)

        for p in keep:
            self._get_or_build(p)

        for cached_page in list(self._embed_cache.keys()):
            if cached_page not in keep:
                del self._embed_cache[cached_page]

    def _sync_buttons(self):
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    def build_embed(self) -> discord.Embed:
        return self._get_or_build(self.page)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._warm_neighbors()
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._warm_neighbors()
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self):
        self.clear_items()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class ConfirmView(discord.ui.View):
    """Minimal yes/no confirmation, locked to one user."""

    def __init__(self, author_id: int, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This confirmation isn't for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Clear", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()
        await interaction.response.edit_message(content="🗑️ Clearing…", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()
        await interaction.response.edit_message(content="❌ Cancelled.", view=None)


# ─── Cog ───────────────────────────────────────────────────────────────────
class DenuvoWatch(commands.Cog):
    """Tracks Denuvo status, build updates, and release dates for a Steam watchlist."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=849201337, force_registration=True)
        self.config.register_global(
            games={},      # master store: appid -> snapshot (shared across guilds)
            history={},    # master build history: appid -> {build_id: {...}}
        )
        self.config.register_guild(
            watched_appids=[],      # this guild's watchlist — references into `games`
            notify_channel_id=None,
            notify_user_id=None,
            notify_role_id=None,
            mirror_guild_id=None,   # when set, this guild reads another guild's watchlist (read-only)
        )
        self.session = aiohttp.ClientSession() # Added session
        self._startup_task: Optional[asyncio.Task] = None

        # Short-lived cache for dadd Steam-search autocomplete, keyed on the
        # lowercased query. Dampens per-keystroke fetches to the Steam API.
        self._dadd_search_cache: dict[str, tuple[float, list]] = {}
        self._dadd_search_cache_ttl: float = 60.0

        self._pending_denuvo_confirms: dict[str, asyncio.Task] = {}
        self._pending_release_confirms: dict[str, asyncio.Task] = {}

        # Serialises scans (background loop, dforcecheck, startup) and the
        # release-confirm task so overlapping load/modify/save cycles can't
        # overwrite each other or double-notify.
        self._check_lock = asyncio.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────
    async def cog_load(self):
        self._startup_task = asyncio.create_task(self._startup_sequence())

    def cog_unload(self):
        if self._startup_task is not None:
            self._startup_task.cancel()
        if self.check_games_loop.is_running():
            self.check_games_loop.cancel()
        for task in (
            *self._pending_denuvo_confirms.values(),
            *self._pending_release_confirms.values(),
        ):
            task.cancel()
        asyncio.create_task(self.session.close())

    async def _startup_sequence(self):
        await self.bot.wait_until_red_ready()
        games = await self.config.games()
        # One-time migration: legacy free-text release_date -> release_label,
        # kept only for upcoming games that don't have an exact date.
        migrated = False
        for g in games.values():
            if "release_date" in g:
                label = g.pop("release_date")
                if (
                    label
                    and g.get("coming_soon")
                    and not exact_release_ts(g)
                    and str(label).strip().lower() not in _NO_DATE_LABELS
                ):
                    g["release_label"] = str(label).strip()
                migrated = True
        if migrated:
            await self._save_games(games)
        if games:
            print("[DenuvoWatch] Running startup forcecheck…")
            await self.check_games_internal(full_refresh=True)
            print("[DenuvoWatch] Startup forcecheck complete.")
        if not self.check_games_loop.is_running():
            self.check_games_loop.start()
            print("[DenuvoWatch] Background check started (every 10 mins)")

    # ── owner-only check ──────────────────────────────────────────────────
    def owner_only():
        async def predicate(ctx: commands.Context) -> bool:
            return await ctx.bot.is_owner(ctx.author)
        return commands.check(predicate)

    # ── persistence helpers ───────────────────────────────────────────────
    async def _load_games(self) -> dict:
        return await self.config.games()

    async def _save_games(self, games: dict):
        games = dict(sorted(games.items(), key=lambda x: x[1].get("name", "").lower()))
        await self.config.games.set(games)

    async def _load_history(self) -> dict:
        return await self.config.history()

    async def _save_history(self, history: dict):
        await self.config.history.set(history)

    def _mention_from(self, gconf: dict) -> str:
        role_id = gconf.get("notify_role_id")
        if role_id:
            return f"<@&{role_id}>"
        user_id = gconf.get("notify_user_id")
        if user_id:
            return f"<@{user_id}>"
        return ""

    async def _watchers_for(self, appid_str: str) -> dict:
        """Guild configs ({guild_id: conf}) that currently watch this appid."""
        all_guilds = await self.config.all_guilds()
        return {
            gid: gconf
            for gid, gconf in all_guilds.items()
            if appid_str in (gconf.get("watched_appids") or [])
        }

    async def _dispatch_change(self, appid_str: str, embed, *, mention: bool = False):
        """Send an embed to every guild watching `appid_str`, each to its own
        notify channel (pinging that guild's role/user when `mention` is set)."""
        allowed = discord.AllowedMentions(users=True, roles=True)
        for gid, gconf in (await self._watchers_for(appid_str)).items():
            channel_id = gconf.get("notify_channel_id")
            if not channel_id:
                continue
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                print(f"[DenuvoWatch][WARN] notify channel {channel_id} for guild {gid} not found.")
                continue
            content = self._mention_from(gconf) if mention else None
            try:
                await channel.send(content=content, embed=embed, allowed_mentions=allowed)
            except Exception as e:
                print(f"[DenuvoWatch][WARN] dispatch to guild {gid} failed: {e}")

    async def _guild_games(self, guild) -> dict:
        """Subset of the master `games` store that `guild` watches.

        If `guild` mirrors another server, resolves against that server's
        watchlist instead (read-only view)."""
        source_id = await self.config.guild(guild).mirror_guild_id() or guild.id
        watched = set(await self.config.guild_from_id(source_id).watched_appids())
        games = await self._load_games()
        return {a: info for a, info in games.items() if a in watched}

    async def _require_guild(self, ctx) -> bool:
        if ctx.guild is None:
            await ctx.send("❌ This command can only be used in a server, not in DMs.", ephemeral=True)
            return False
        return True

    async def _block_if_mirrored(self, ctx) -> bool:
        """True (and warns) if this guild mirrors another, so it can't manage
        its own list. Turn the mirror off first."""
        mirror = await self.config.guild(ctx.guild).mirror_guild_id()
        if mirror:
            await ctx.send(
                f"❌ This server is mirroring server `{mirror}` (read-only). "
                f"Run `dmirror off` first to manage its own watchlist."
            )
            return True
        return False

    async def _unwatch(self, guild, appid_str: str) -> bool:
        """Unsubscribe `guild` from `appid_str`. When no guild watches it any
        longer, drop it from the master `games` and `history` stores.
        Returns True if the guild was actually watching it."""
        watched = await self.config.guild(guild).watched_appids()
        if appid_str not in watched:
            return False
        watched.remove(appid_str)
        await self.config.guild(guild).watched_appids.set(watched)

        # Reference counting: only purge master data once nobody watches it.
        if not await self._watchers_for(appid_str):
            games = await self._load_games()
            if appid_str in games:
                del games[appid_str]
                await self._save_games(games)
            history = await self._load_history()
            if appid_str in history:
                del history[appid_str]
                await self._save_history(history)
        return True

    async def _confirm_denuvo_change(self, appid_str: str, expected_new_value: bool):
        try:
            await asyncio.sleep(120)
            appid = int(appid_str)

            recheck = await asyncio.to_thread(get_game_snapshot, appid)
            if recheck is None:
                return

            if recheck["denuvo"] != expected_new_value:
                print(f"[DenuvoWatch] {recheck['name']}: denuvo change did not persist, ignoring.")
                return

            async with self._check_lock:
                games = await self._load_games()
                current = games.get(appid_str)
                if current is None:
                    return

                old_denuvo = current.get("denuvo")
                if old_denuvo == expected_new_value:
                    return

                old_snapshot = dict(current)
                new_snapshot = dict(current)
                new_snapshot["denuvo"] = expected_new_value
                new_snapshot["name"] = recheck["name"]
                new_snapshot["header"] = recheck.get("header")

                change_type = "denuvo_removed" if (old_denuvo and not expected_new_value) else "denuvo_added"
                await self._dispatch_change(
                    appid_str, build_denuvo_embed(appid, change_type, old_snapshot, new_snapshot)
                )

                games[appid_str]["denuvo"] = expected_new_value
                await self._save_games(games)
                print(f"[DenuvoWatch] {recheck['name']}: denuvo change confirmed -> {expected_new_value}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[DenuvoWatch][ERROR] confirm task for {appid_str} crashed: {e}")
        finally:
            self._pending_denuvo_confirms.pop(appid_str, None)

    async def _confirm_release(self, appid_str: str):
        """Re-verify a possible release after 2 minutes before announcing it,
        so a transient coming_soon flip on Steam's side can't cause a
        misfire or duplicate embeds."""
        try:
            await asyncio.sleep(120)
            appid = int(appid_str)

            recheck = await asyncio.to_thread(get_game_snapshot, appid)
            if recheck is None:
                return
            if recheck.get("coming_soon"):
                print(f"[DenuvoWatch] {recheck['name']}: release did not persist, ignoring.")
                return

            async with self._check_lock:
                games = await self._load_games()
                current = games.get(appid_str)
                if not current or not current.get("coming_soon"):
                    return  # removed, or already handled

                exact_ts = exact_release_ts(current)
                if exact_ts and time.time() < exact_ts:
                    print(f"[DenuvoWatch] {recheck['name']}: Steam says released but release_ts "
                          f"is still ahead — not announcing yet.")
                    return

                old_snapshot = dict(current)   # still holds release_ts for "Expected Date"
                new_snapshot = dict(current)
                new_snapshot.update(
                    name=recheck["name"],
                    denuvo=recheck["denuvo"],
                    header=recheck.get("header") or current.get("header"),
                    build_id=recheck.get("build_id") or current.get("build_id"),
                )
                await self._dispatch_change(
                    appid_str, build_release_embed(appid, old_snapshot, new_snapshot)
                )

                for key in ("coming_soon", "release_ts", "release_precision", "release_label",
                            "advance_access_ts", "reminded_for"):
                    current.pop(key, None)
                current["released"] = True
                await self._save_games(games)
                print(f"[INFO] {recheck['name']} release confirmed and announced.")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[DenuvoWatch][ERROR] release confirm for {appid_str} crashed: {e}")
        finally:
            self._pending_release_confirms.pop(appid_str, None)

    # ── background check ─────────────────────────────────────────────────
    async def check_games_internal(self, full_refresh: bool = False, scope=None) -> bool:
        """Serialised entry point so overlapping scans can't double-notify
        or overwrite each other's saves."""
        async with self._check_lock:
            return await self._check_games_locked(full_refresh=full_refresh, scope=scope)

    async def _check_games_locked(self, full_refresh: bool = False, scope=None) -> bool:
        """Scan watched games and fan out notifications.

        `scope=None` scans every game in the master store (background loop).
        `scope=<guild>` scans only the appids that guild watches (dforcecheck).
        Detected changes are always dispatched to *all* guilds watching the
        affected appid, regardless of scope.
        """
        changes = False
        try:
            games = await self._load_games()
            if not games:
                return False

            if scope is not None:
                watched = set(await self.config.guild(scope).watched_appids())
                target_ids = [a for a in games if a in watched]
            else:
                target_ids = list(games.keys())

            if not target_ids:
                return False

            print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking {len(target_ids)} games…")

            async def check_single(appid_str, old):
                await asyncio.sleep(0.5)
                appid = int(appid_str)
                new = await asyncio.to_thread(get_game_snapshot, appid)
                if new is None:
                    return appid_str, None
                return appid_str, new

            results = await asyncio.gather(*[
                check_single(appid_str, games[appid_str])
                for appid_str in target_ids
            ])
            changed_appids = set()

            for appid_str, new in results:
                if new is None:
                    continue
                old = games[appid_str]
                appid = int(appid_str)

                # Steam's appdetails can briefly flip back to coming_soon=True after
                # launch (stale cache). A released game never un-releases, so ignore it.
                if new.get("coming_soon") and old.get("released"):
                    print(f"[INFO] {new['name']}: ignoring stale coming_soon=True (already released).")
                    new["coming_soon"] = False
                    new["release_ts"] = None
                    new["release_precision"] = None

                # Denuvo change — don't notify immediately, confirm with a
                # targeted re-check in 2 minutes to filter out flakiness/outages
                if appid_str in self._pending_denuvo_confirms:
                    pass
                elif old.get("denuvo") != new["denuvo"]:
                    task = asyncio.create_task(self._confirm_denuvo_change(appid_str, new["denuvo"]))
                    self._pending_denuvo_confirms[appid_str] = task
                    print(f"[DenuvoWatch] {new['name']}: possible denuvo change "
                          f"({old.get('denuvo')} -> {new['denuvo']}), confirming in 2 min…")

                 # Release needs BOTH: Steam says it's out (coming_soon went false) AND,
                # when an exact release_ts is known, the clock is at/after it. Then a
                # 2-min recheck confirms before the embed goes out.
                flipped = bool(old.get("coming_soon")) and not new.get("coming_soon")
                rts = exact_release_ts(old) if flipped else None
                awaiting_clock = bool(rts) and time.time() < rts  # flipped before release_ts: hold

                if flipped and not awaiting_clock and appid_str not in self._pending_release_confirms:
                    self._pending_release_confirms[appid_str] = asyncio.create_task(
                        self._confirm_release(appid_str)
                    )
                    print(f"[DenuvoWatch] {new['name']}: possible release, confirming in 2 min…")

                # Build ID change
                old_build = old.get("build_id")
                new_build = new.get("build_id")
                build_actually_changed = False 
                if old_build and new_build and old_build != new_build:
                    changed_appids.add(appid_str)
                    _, _, new_manifests, new_depot_sizes = await asyncio.to_thread(fetch_build_id, appid)
                    old_manifests = old.get("manifests", {})
                    if new_manifests != old_manifests:
                        build_actually_changed = True
                        old_total_bytes = sum(old.get("depot_sizes", {}).values())
                        new_total_bytes = sum(new_depot_sizes.values()) if new_depot_sizes else 0
                        new["old_build_size_bytes"] = old_total_bytes
                        new["new_build_size_bytes"] = new_total_bytes

                        await self._dispatch_change(
                            appid_str,
                            build_depot_embed(appid, old_build, new_build, new),
                            mention=True,
                        )
                        changes = True

                        history = await self._load_history()
                        game_history = history.setdefault(appid_str, {})
                        if old_build and old.get("manifests"):
                            last_entry = next(iter(reversed(game_history.values())), None)
                            old_manifests_changed = (
                                last_entry is None or
                                last_entry.get("manifests") != old["manifests"]
                            )
                            if old_manifests_changed:
                                game_history[old_build] = {
                                    "manifests": old["manifests"],
                                    "depot_sizes": old.get("depot_sizes", {}),
                                }
                        if len(game_history) > 3:
                            oldest = list(game_history.keys())[0]
                            del game_history[oldest]
                        await self._save_history(history)
                        games[appid_str]["manifests"] = new_manifests
                        games[appid_str]["depot_sizes"] = new_depot_sizes
                    else:
                        print(f"[INFO] {new['name']}: buildid {old_build} → {new_build} but no tracked depot changed (likely non-windows push) — ignoring.")

                update_fields = {
                    "name": new["name"],
                }
                if new.get("header"):
                    update_fields["header"] = new["header"]
                if appid_str not in self._pending_denuvo_confirms:
                    update_fields["denuvo"] = new["denuvo"]
                if build_actually_changed or not old_build:
                    update_fields["build_id"] = new["build_id"]
                    update_fields["build_time"] = new.get("build_time")
                games[appid_str].update(update_fields)

                if new.get("coming_soon"):
                    games[appid_str]["coming_soon"] = True
                    if new.get("release_ts"):
                        games[appid_str]["release_ts"] = new["release_ts"]
                    if new.get("release_precision"):
                        games[appid_str]["release_precision"] = new["release_precision"]
                    # Rough label only while there's no exact date
                    if exact_release_ts(games[appid_str]):
                        games[appid_str].pop("release_label", None)
                    elif new.get("release_label"):
                        games[appid_str]["release_label"] = new["release_label"]
                elif appid_str in self._pending_release_confirms or awaiting_clock:
                    pass  # keep coming_soon/release data until the confirm task / release time decides
                else:
                    games[appid_str]["released"] = True
                    dropped = False
                    for key in ("coming_soon", "release_ts", "release_precision", "release_label", "advance_access_ts", "reminded_for"):
                        if key in games[appid_str]:
                            games[appid_str].pop(key)
                            dropped = True
                    if dropped:
                        print(f"[INFO] {new['name']} has released, cleared coming_soon + release data.")

            # Advanced access start: notify once, then drop the value
            now_ts = int(time.time())
            for appid_str in target_ids:
                info = games.get(appid_str)
                aa_ts = info.get("advance_access_ts") if info else None
                if aa_ts and now_ts >= aa_ts and appid_str not in self._pending_release_confirms:
                    await self._dispatch_change(
                        appid_str,
                        build_advance_access_embed(int(appid_str), info),
                        # mention=True,  # uncomment to ping the notify role/user
                    )
                    info.pop("advance_access_ts", None)
                    changes = True
                    print(f"[INFO] {info.get('name', appid_str)} advanced access has started.")

            # Release reminder: once per release_ts, within 24h of an exact release time.
            # (Re-arms automatically if Steam moves the date.)
            for appid_str in target_ids:
                info = games.get(appid_str)
                if (
                    not info
                    or not info.get("coming_soon")
                    or appid_str in self._pending_release_confirms
                ):
                    continue
                rts = exact_release_ts(info)
                if (
                    rts
                    and rts - REMINDER_WINDOW <= now_ts < rts
                    and info.get("reminded_for") != rts
                ):
                    await self._dispatch_change(
                        appid_str,
                        build_release_reminder_embed(int(appid_str), info),
                        mention=False,
                    )
                    info["reminded_for"] = rts
                    changes = True
                    print(f"[INFO] {info.get('name', appid_str)}: sent 24h release reminder.")

            # Full refresh for unchanged games (within scope)
            if full_refresh:
                refresh_targets = [a for a in target_ids if a not in changed_appids]

                async def refresh_single(appid_str):
                    _, _, manifests, depot_sizes = await asyncio.to_thread(fetch_build_id, int(appid_str))
                    return appid_str, manifests, depot_sizes

                refresh_results = await asyncio.gather(*[refresh_single(a) for a in refresh_targets])

                for appid_str, manifests, depot_sizes in refresh_results:
                    if manifests:
                        games[appid_str]["manifests"] = manifests
                    if depot_sizes:
                        games[appid_str]["depot_sizes"] = depot_sizes

                aa_targets = [
                    a for a in target_ids
                    if games[a].get("coming_soon") and not games[a].get("advance_access_ts")
                ]

                async def aa_single(appid_str):
                    ts = await asyncio.to_thread(fetch_advance_access, int(appid_str))
                    return appid_str, ts

                aa_results = await asyncio.gather(*[aa_single(a) for a in aa_targets])
                for appid_str, ts in aa_results:
                    if ts:
                        games[appid_str]["advance_access_ts"] = ts
                        print(f"[INFO] {games[appid_str].get('name', appid_str)}: advanced access set to {ts}.")

            await self._save_games(games)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Check complete.")

        except Exception as e:
            print(f"[DenuvoWatch][ERROR] check_games crashed: {e}")
            import traceback
            traceback.print_exc()

        return changes

    @tasks.loop(minutes=10)
    async def check_games_loop(self):
        await self.check_games_internal()

    # ── shared add logic ──────────────────────────────────────────────────
    async def _add_appid(self, guild, appid: int, send_func):
        appid_str = str(appid)
        watched = await self.config.guild(guild).watched_appids()
        games = await self._load_games()

        if appid_str in watched:
            name = games.get(appid_str, {}).get("name", f"AppID {appid_str}")
            await send_func(f"ℹ️ **{name}** is already on this server's watchlist.")
            return

        if len(watched) >= MAX_GAMES:
            await send_func(f"❌ This server's watchlist is full ({MAX_GAMES} games max).")
            return

        if appid_str in games:
            # Already tracked by another guild — just subscribe, no API call.
            entry = games[appid_str]
        else:
            snapshot = await asyncio.to_thread(get_game_snapshot, appid)
            if snapshot is None:
                await send_func(f"❌ Couldn't fetch data for AppID `{appid}`.")
                return

            _, _, manifests, depot_sizes = await asyncio.to_thread(fetch_build_id, appid)

            entry = {
                "name": snapshot["name"],
                "denuvo": snapshot["denuvo"],
                "build_id": snapshot["build_id"],
                "build_time": snapshot.get("build_time"),
                "header": snapshot.get("header"),
            }
            if snapshot.get("coming_soon"):
                entry["coming_soon"] = True
                if snapshot.get("release_ts"):
                    entry["release_ts"] = snapshot["release_ts"]
                if snapshot.get("release_precision"):
                    entry["release_precision"] = snapshot["release_precision"]
                if snapshot.get("release_label") and not exact_release_ts(entry):
                    entry["release_label"] = snapshot["release_label"]
                aa_ts = await asyncio.to_thread(fetch_advance_access, appid)
                if aa_ts:
                    entry["advance_access_ts"] = aa_ts
            else:
                entry["released"] = True
            entry["manifests"] = manifests
            entry["depot_sizes"] = depot_sizes

            games[appid_str] = entry
            await self._save_games(games)

        watched.append(appid_str)
        await self.config.guild(guild).watched_appids.set(watched)

        embed = discord.Embed(
            title="✅ Added to Watchlist",
            description=f"**[{entry['name']}](https://store.steampowered.com/app/{appid}/)**",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Denuvo", value="⚠️ Yes" if entry.get("denuvo") else "✅ No", inline=True)
        embed.add_field(name="Build ID", value=f"`{entry['build_id']}`" if entry.get("build_id") else "Unknown", inline=True)
        embed.add_field(name="Watchlist", value=f"{len(watched)}/{MAX_GAMES} games", inline=True)
        if entry.get("advance_access_ts"):
            embed.add_field(name="Advanced Access", value=f"<t:{entry['advance_access_ts']}:F>", inline=False)
        if entry.get("header"):
            embed.set_thumbnail(url=entry["header"])
        embed.set_footer(text=f"AppID {appid}")
        await send_func(embed=embed)

    async def _game_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list:
        guild = interaction.guild
        if guild is None:
            return []
        try:
            names = [info.get("name", "") for info in (await self._guild_games(guild)).values()]
        except Exception:
            return []  # never let autocomplete hang past Discord's ~3s window

        current_lower = current.lower()
        matches = [name for name in names if current_lower in name.lower()]
        return [
            discord.app_commands.Choice(name=name, value=name)
            for name in matches[:25]
        ]

    async def fetch_dlc_depots_info(self, dlc_appid: int) -> dict[str, int]:
        depot_sizes = {}
        try:
            r = await asyncio.to_thread(
                requests.get,
                f"https://api.steamcmd.net/v1/info/{dlc_appid}",
                headers=HEADERS,
                timeout=10,
            )
            data = r.json()
            app_data = data.get("data", {}).get(str(dlc_appid), {})

            app_type = app_data.get("common", {}).get("type", "").lower()
            if app_type != "dlc":
                return depot_sizes  # skip demos, base-game cross-refs, tools, etc.

            dlc_depots = app_data.get("depots", {})
            if not dlc_depots:
                return depot_sizes

            for depot_id, depot_info in dlc_depots.items():
                if not depot_id.isdigit() or not isinstance(depot_info, dict):
                    continue
                if depot_id in GLOBAL_DEPOTS:
                    continue

                depot_oslist = depot_info.get("config", {}).get("oslist")
                if depot_oslist and not any(os_ in depot_oslist for os_ in OSLIST_FILTER):
                    continue

                manifest = depot_info.get("manifests", {}).get("public")
                size = 0
                if isinstance(manifest, dict):
                    size = int(manifest.get("size", 0))
                elif "maxsize" in depot_info:
                    try:
                        size = int(depot_info.get("maxsize", 0))
                    except (ValueError, TypeError):
                        pass

                if size > 0:
                    depot_sizes[depot_id] = size
        except Exception:
            pass

        return depot_sizes

    async def get_total_size_with_dlc(self, appid: int) -> tuple[int, dict[str, int]]:
        """Dynamic DLC size lookup used exclusively for ad-hoc / unwatched checks."""
        # 1. Base game depots (via fetch_build_id which includes SUBDLC_APPIDS)
        _, _, _, base_depots = await asyncio.to_thread(fetch_build_id, appid)
        depot_sizes = dict(base_depots)

        # 2. Discover all DLC AppIDs
        details = await asyncio.to_thread(fetch_app_details, appid)
        store_dlc_ids = set((details or {}).get("dlc", []))
        steamcmd_dlc_ids = set(await asyncio.to_thread(get_dlc_appids_from_steamcmd, appid))
        all_dlc_ids = store_dlc_ids | steamcmd_dlc_ids

        # 3. Fetch sizes for all discovered DLCs concurrently
        if all_dlc_ids:
            tasks = [self.fetch_dlc_depots_info(d) for d in all_dlc_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for dlc_sizes in results:
                if isinstance(dlc_sizes, dict):
                    depot_sizes.update(dlc_sizes)

        total_bytes = sum(depot_sizes.values())
        return total_bytes, depot_sizes

    # ── command group (denuvowatch) ───────────────────────────────────────

    @commands.hybrid_command(name="dadd")
    @discord.app_commands.describe(query="Game name or Steam AppID")
    @owner_only()
    async def dadd(self, ctx: commands.Context, *, query: str):
        """Add a game to this server's watchlist by name or AppID."""
        if not await self._require_guild(ctx):
            return
        if await self._block_if_mirrored(ctx):
            return
        query = query.strip("\"' \t\r\n")
        async with ctx.typing():
            if query.isdigit():
                appid = int(query)
                snapshot = await asyncio.to_thread(get_game_snapshot, appid)
                if snapshot is None:
                    await ctx.send(f"❌ Couldn't find a game with AppID `{appid}`.")
                    return
                candidates = [{"appid": appid, "name": snapshot["name"]}]
            else:
                raw_candidates = await asyncio.to_thread(search_steam, query)
                raw_candidates = raw_candidates[:10]
                if not raw_candidates:
                    await ctx.send("❌ No results found on Steam.")
                    return

                if len(raw_candidates) == 1:
                    candidates = raw_candidates
                else:
                    candidates = []
                    for c in raw_candidates:
                        details = await asyncio.to_thread(fetch_app_details, c["appid"])
                        if details.get("type") == "game":
                            candidates.append(c)
                        if len(candidates) >= 5:
                            break
                    if not candidates:
                        await ctx.send("❌ No games found (all results were DLC/other).")
                        return

                    query_norm = normalize_game_name(query)
                    exact = [c for c in candidates if normalize_game_name(c["name"]) == query_norm]
                    if exact:
                        candidates = [exact[0]]
                    else:
                        starts = [c for c in candidates if normalize_game_name(c["name"]).startswith(query_norm)]
                        if starts:
                            candidates = [starts[0]]

        if len(candidates) == 1:
            await self._add_appid(ctx.guild, candidates[0]["appid"], ctx.send)
            return

        options = [
            discord.SelectOption(label=c["name"][:100], value=str(c["appid"]))
            for c in candidates
        ]
        select = discord.ui.Select(placeholder="Choose a game…", options=options)

        async def select_callback(inter: discord.Interaction):
            await inter.response.defer(thinking=True)
            await self._add_appid(inter.guild, int(select.values[0]), inter.followup.send)

        select.callback = select_callback
        view = discord.ui.View(timeout=60)
        view.add_item(select)
        await ctx.send("Multiple results found — pick one:", view=view)

    @dadd.autocomplete("query")
    async def dadd_query_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.strip()
        # Need at least 3 chars before hitting Steam, so it does not fetch on
        # every keystroke. Digits are treated as a raw AppID — no search.
        if len(current) < 3 or current.isdigit():
            return []

        key = current.lower()
        now = time.monotonic()
        cached = self._dadd_search_cache.get(key)
        if cached and (now - cached[0]) < self._dadd_search_cache_ttl:
            results = cached[1]
        else:
            try:
                results = await asyncio.wait_for(
                    asyncio.to_thread(search_steam, current), timeout=2.5
                )
            except Exception:
                return []  # never let autocomplete hang past Discord's ~3s window
            self._dadd_search_cache[key] = (now, results)

        # Value is the AppID string, so picking a choice routes dadd straight
        # into its isdigit() branch and skips a second Steam search.
        return [
            discord.app_commands.Choice(name=r["name"][:100], value=str(r["appid"]))
            for r in results[:25]
        ]

    @commands.hybrid_command(name="dremove")
    @owner_only()
    @discord.app_commands.describe(query="Game name or AppID")
    async def dremove(self, ctx: commands.Context, *, query: str):
        """Remove a game from this server's watchlist."""
        if not await self._require_guild(ctx):
            return
        if await self._block_if_mirrored(ctx):
            return
        games = await self._guild_games(ctx.guild)
        if not games:
            await ctx.send("📭 This server's watchlist is empty.", ephemeral=True)
            return

        query = query.strip("\"' \t\r\n")
        query_norm = normalize_game_name(query)

        # Exact normalized-name match first (covers autocomplete selections,
        # which supply the exact stored name, plus punctuation/trademark/roman
        # numeral differences like "elden ring 3" vs "ELDEN RING™ III")
        exact_matches = [
            (appid_str, info) for appid_str, info in games.items()
            if normalize_game_name(info["name"]) == query_norm
        ]
        if exact_matches:
            matches = exact_matches
        else:
            matches = []
            for appid_str, info in games.items():
                if query.isdigit() and appid_str == query:
                    matches = [(appid_str, info)]
                    break
                elif query_norm in normalize_game_name(info["name"]):
                    matches.append((appid_str, info))

            if not matches:
                await ctx.send(f"❌ No game matching `{query}` on this server's watchlist.", ephemeral=True)
                return

        if len(matches) == 1:
            appid_str, info = matches[0]
            await self._unwatch(ctx.guild, appid_str)
            await ctx.send(f"🗑️ Removed **{info['name']}** from this server's watchlist.", ephemeral=True)
            return

        options = [
            discord.SelectOption(label=info["name"][:100], value=appid_str)
            for appid_str, info in matches[:5]
        ]
        select = discord.ui.Select(placeholder="Which game to remove?", options=options)

        async def cb(inter: discord.Interaction):
            chosen_id = select.values[0]
            guild_games = await self._guild_games(inter.guild)
            name = guild_games.get(chosen_id, {}).get("name", chosen_id)
            await self._unwatch(inter.guild, chosen_id)
            await inter.response.send_message(f"🗑️ Removed **{name}** from this server's watchlist.", ephemeral=True)

        select.callback = cb
        view = discord.ui.View(timeout=60)
        view.add_item(select)
        await ctx.send("Multiple matches — choose one:", view=view, ephemeral=True)

    @dremove.autocomplete("query")
    async def dremove_query_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._game_name_autocomplete(interaction, current)

    @commands.hybrid_command(name="dlist")
    async def dlist(self, ctx: commands.Context):
        """Show all watched games and their status."""
        if not await self._require_guild(ctx):
            return
        games_dict = await self._guild_games(ctx.guild)
        if not games_dict:
            await ctx.send("📭 This server's watchlist is empty. Use `dadd` to add games.")
            return

        games = sorted(games_dict.items(), key=lambda x: x[1].get("name", "").lower())

        if len(games) <= 25:
            embed = discord.Embed(
                title=f"🎮 Steam Watchlist ({len(games)}/{MAX_GAMES})",
                color=discord.Color.blurple()
            )
            lines = []
            for appid_str, info in games:
                icon = "⚠️" if info.get("denuvo") else "✅"
                build = f" • build `{info['build_id']}`" if info.get("build_id") and not info.get("coming_soon") else ""
                lines.append(f"{icon} **{info['name']}** `{appid_str}`{build}")
            embed.description = "\n".join(lines)
            embed.set_footer(text="⚠️ = has Denuvo    ✅ = no Denuvo")
            await ctx.send(embed=embed)
            return

        view = ListView(ctx, games, discord.Color.blurple())
        msg = await ctx.send(embed=view.build_embed(), view=view)
        view.message = msg

    @commands.hybrid_command(name="dcheck")
    @discord.app_commands.describe(
        query="Game name or AppID",
        item_type="Type of Steam item to search for (default: game)"
    )
    async def dcheck(
        self,
        ctx: commands.Context,
        query: str,
        item_type: Literal["game", "dlc", "demo"] = "game"
    ):
        """Instantly check a game's (or other Steam item type's) current status."""
        query = query.strip("\"' \t\r\n")
        async with ctx.typing():
            games = await self._load_games()

            appid = None
            if query.isdigit():
                appid = int(query)
            else:
                if item_type == "game":
                    query_norm = normalize_game_name(query)
                    for appid_str, info in games.items():
                        if query_norm in normalize_game_name(info["name"]):
                            appid = int(appid_str)
                            break
                if appid is None:
                    appid = await resolve_best_game_match(query, item_type)

            if appid is None:
                await ctx.send(f"❌ Couldn't resolve `{query}` to a Steam {item_type}.")
                return

            in_watchlist = str(appid) in games
            stored = games.get(str(appid), {})

            if not in_watchlist and not await self.bot.is_owner(ctx.author):
                await ctx.send("❌ Only owners can check games that aren't on the watchlist.")
                return

            if in_watchlist:
                snapshot = {
                    "name": stored.get("name", f"AppID {appid}"),
                    "denuvo": stored.get("denuvo", False),
                    "header": stored.get("header", ""),
                    "build_id": stored.get("build_id"),
                    "build_time": stored.get("build_time"),
                    "coming_soon": stored.get("coming_soon", False),
                    "release_ts": stored.get("release_ts"),
                    "release_precision": stored.get("release_precision"),
                    "release_label": stored.get("release_label"),
                    "advance_access_ts": stored.get("advance_access_ts"),
                }
            else:
                snapshot = await asyncio.to_thread(get_game_snapshot, appid)
                if snapshot is None:
                    await ctx.send(f"❌ Couldn't fetch data for AppID `{appid}`.")
                    return
                if snapshot.get("coming_soon"):
                    snapshot["advance_access_ts"] = await asyncio.to_thread(fetch_advance_access, appid)

            depot_sizes = stored.get("depot_sizes", {})
            if not depot_sizes:
                _, depot_sizes = await self.get_total_size_with_dlc(appid)

        embed = discord.Embed(
            title=f"🔍 {snapshot['name']}",
            url=f"https://store.steampowered.com/app/{appid}/",
            color=discord.Color.blurple()
        )

        is_coming_soon = snapshot.get("coming_soon")
        embed.add_field(name="Denuvo", value="⚠️ Yes" if snapshot["denuvo"] else "✅ No", inline=True)
        if not is_coming_soon:
            embed.add_field(name="Build ID", value=f"`{snapshot['build_id']}`" if snapshot["build_id"] else "Unknown", inline=True)
        embed.add_field(name="Watchlist", value="👁️ Watching" if in_watchlist else "➕ Use `dadd`", inline=True)
        if depot_sizes:
            total = sum(depot_sizes.values())
            embed.add_field(name="Build Size", value=format_size(total), inline=True)
        if not is_coming_soon and snapshot.get("build_time"):
            embed.add_field(name="Build Pushed", value=f"<t:{snapshot['build_time']}:R>", inline=True)

        if is_coming_soon:
            aa = snapshot.get("advance_access_ts")
            if aa:
                embed.add_field(name="Advanced Access", value=f"<t:{aa}:F>", inline=True)
            release_field = release_display(snapshot)
            if release_field:
                embed.add_field(name="Release Date", value=release_field, inline=True)
        if snapshot.get("header"):
            embed.set_thumbnail(url=snapshot["header"])
        embed.set_footer(text=f"AppID {appid}")
        embed.timestamp = datetime.now(timezone.utc)
        await ctx.send(embed=embed)

    @dcheck.autocomplete("query")
    async def dcheck_query_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._game_name_autocomplete(interaction, current)

    @commands.hybrid_command(name="dforcecheck")
    @owner_only()
    async def dforcecheck(self, ctx: commands.Context):
        """Manually trigger a full scan of this server's watchlist."""
        if not await self._require_guild(ctx):
            return
        if await self._block_if_mirrored(ctx):
            return
        await ctx.send("🔄 Running check for this server's watchlist…", ephemeral=True)
        changes = await self.check_games_internal(full_refresh=True, scope=ctx.guild)
        if not changes:
            await ctx.send("✅ Check complete — no changes detected.", ephemeral=True)

    @commands.hybrid_command(name="dupcoming")
    async def dupcoming(self, ctx: commands.Context):
        """Show all upcoming (unreleased) games in this server's watchlist."""
        if not await self._require_guild(ctx):
            return
        games = await self._guild_games(ctx.guild)

        upcoming = [
            (appid_str, info) for appid_str, info in games.items()
            if info.get("coming_soon")
        ]

        if not upcoming:
            await ctx.send("📭 No upcoming games on this server's watchlist right now.")
            return

        def sort_key(item):
            _, info = item
            ts = exact_release_ts(info)
            base = float(ts) if ts else label_sort_ts(info.get("release_label"))
            aa = info.get("advance_access_ts")
            if aa:
                base = min(base, float(aa)) if base is not None else float(aa)
            return (0, base) if base is not None else (1, float("inf"))

        upcoming.sort(key=sort_key)

        embed = discord.Embed(
            title=f"🚀 Upcoming Games ({len(upcoming)})",
            color=discord.Color.gold()
        )
        lines = []
        for appid_str, info in upcoming:
            ts = exact_release_ts(info)
            date = f"<t:{ts}:f>" if ts else (info.get("release_label") or "Date TBA")
            line = f"**{info['name']}** `{appid_str}` — {date}"
            aa = info.get("advance_access_ts")
            if aa:
                line += f"\n> ✈️ Advanced access: <t:{aa}:f>"
            lines.append(line)
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="ddepots")
    @discord.app_commands.describe(
        query="Game name or AppID",
        index="Which build: 0=current (default), 1=previous, 2=two builds ago, 3=three builds ago",
        show_manifests="Also show manifest IDs (default: False)"
    )
    async def ddepots(self, ctx: commands.Context, query: str, index: int = 0, show_manifests: bool = False):
        """Show depot info for a game on this server's watchlist."""
        if not await self._require_guild(ctx):
            return
        query = query.strip("\"' \t\r\n")
        games = await self._guild_games(ctx.guild)

        appid = None
        if query.isdigit():
            appid = int(query)
        else:
            query_norm = normalize_game_name(query)
            for appid_str, info in games.items():
                if query_norm in normalize_game_name(info["name"]):
                    appid = int(appid_str)
                    break

        if appid is None:
            await ctx.send(f"❌ `{query}` not found in this server's watchlist.")
            return

        info = games.get(str(appid))
        if not info:
            await ctx.send(f"❌ `{query}` not found in this server's watchlist.")
            return

        if index < 0 or index > 3:
            await ctx.send("❌ Index must be 0 (current), 1, 2, or 3.")
            return

        if index == 0:
            manifests = info.get("manifests", {})
            depot_sizes = info.get("depot_sizes", {})
            build_id = info.get("build_id", "unknown")
            label = f"Current (build `{build_id}`)"
            if not manifests and not depot_sizes:
                await ctx.send("No depot data recorded yet — run `dforcecheck` to populate.")
                return
        else:
            history = await self._load_history()
            game_history = history.get(str(appid), {})
            history_entries = list(reversed(list(game_history.items())))
            if index > len(history_entries):
                await ctx.send(f"❌ Only {len(history_entries)} previous build(s) recorded so far.")
                return
            build_id, entry = history_entries[index - 1]
            manifests = entry["manifests"]
            depot_sizes = entry.get("depot_sizes", {})
            label = f"Previous {index} (build `{build_id}`)"

        all_depots = set(list(manifests.keys()) + list(depot_sizes.keys()))
        lines = []
        total = 0
        for depot_id in sorted(all_depots):
            size_bytes = depot_sizes.get(depot_id, 0)
            size_str = format_size(size_bytes) if size_bytes > 0 else "unknown"
            manifest_str = f" → `{manifests[depot_id]}`" if show_manifests and depot_id in manifests else ""
            lines.append(f"Depot `{depot_id}` `{size_str}`{manifest_str}")
            total += size_bytes

        lines.append(f"\n**Total: `{format_size(total)}`**")

        embed = discord.Embed(
            title=f"📦 Depot Info — {info['name']}",
            url=f"https://steamdb.info/app/{appid}/depots/",
            color=discord.Color.blue()
        )
        embed.add_field(name=label, value="\n".join(lines)[:1024], inline=False)
        embed.set_footer(text=f"AppID {appid} • 0=current, 1-3=previous builds")
        embed.timestamp = datetime.now(timezone.utc)
        await ctx.send(embed=embed)

    @ddepots.autocomplete("query")
    async def ddepots_query_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._game_name_autocomplete(interaction, current)

    @commands.command(name="dexport")
    async def dexport(self, ctx: commands.Context):
        """Export this server's watchlist as a JSON file (re-importable via dimport)."""
        if not await self._require_guild(ctx):
            return
        games = await self._guild_games(ctx.guild)
        if not games:
            await ctx.send("📭 This server's watchlist is empty — nothing to export.")
            return

        payload = {"games": games}
        buffer = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
        filename = f"steam_data_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"

        await ctx.send(
            f"📤 Exported **{len(games)}** game(s).",
            file=discord.File(fp=buffer, filename=filename),
        )

    @commands.command(name="dimport")
    @owner_only()
    async def dimport(self, ctx: commands.Context, url: str = None):
        """Import games into this server's watchlist from a JSON file or URL."""
        if not await self._require_guild(ctx):
            return
        if await self._block_if_mirrored(ctx):
            return
        raw = None

        if url:
            url = url.strip("<>")
            if not url.lower().startswith(("http://", "https://")):
                await ctx.send("❌ That doesn't look like a valid URL.")
                return
            try:
                async with self.session.get(
                    url, timeout=aiohttp.ClientTimeout(total=20)
                ) as r:
                    r.raise_for_status()
                    raw = await r.read()
            except Exception as e:
                await ctx.send(f"❌ Couldn't download the file: `{e}`")
                return
        elif ctx.message.attachments:
            try:
                raw = await ctx.message.attachments[0].read()
            except Exception as e:
                await ctx.send(f"❌ Couldn't read the attached file: `{e}`")
                return
        else:
            await ctx.send(
                "❌ Attach a JSON file or pass a direct JSON URL "
                "(`{\"games\": {...}}` or a bare `{appid: {...}}` mapping)."
            )
            return

        try:
            text = raw.decode("utf-8").lstrip()
            if text[:1] not in ("{", "["):
                await ctx.send("❌ The source didn't return JSON (got HTML/other).")
                return
            payload = json.loads(text)
        except Exception as e:
            await ctx.send(f"❌ Couldn't parse the JSON: `{e}`")
            return

        incoming = payload.get("games", payload) if isinstance(payload, dict) else None
        if not isinstance(incoming, dict) or not incoming:
            await ctx.send("❌ No games found in the file.")
            return

        games = await self._load_games()
        watched = await self.config.guild(ctx.guild).watched_appids()
        added, skipped_existing, skipped_full, invalid = 0, 0, 0, 0
        master_changed = False

        for appid_str, info in incoming.items():
            if not str(appid_str).isdigit() or not isinstance(info, dict):
                invalid += 1
                continue

            appid_str = str(appid_str)
            if appid_str in watched:
                skipped_existing += 1
                continue
            if len(watched) >= MAX_GAMES:
                skipped_full += 1
                continue

            # Ensure the game exists in the master store. If another guild
            # already tracks it, keep the live (fresher) snapshot rather than
            # overwriting it with possibly-stale export data.
            if appid_str not in games:
                games[appid_str] = {
                    "name": info.get("name", f"AppID {appid_str}"),
                    "denuvo": bool(info.get("denuvo", False)),
                    "build_id": info.get("build_id"),
                    "build_time": info.get("build_time"),
                    "header": info.get("header"),
                    "manifests": info.get("manifests", {}),
                    "depot_sizes": info.get("depot_sizes", {}),
                }
                # Only add release data if it exists in the import
                if info.get("coming_soon"):
                    games[appid_str]["coming_soon"] = True
                    if info.get("release_ts"):
                        games[appid_str]["release_ts"] = info["release_ts"]
                    if info.get("release_precision"):
                        games[appid_str]["release_precision"] = info["release_precision"]
                    if info.get("release_label") and not exact_release_ts(games[appid_str]):
                        games[appid_str]["release_label"] = info["release_label"]
                    if info.get("advance_access_ts") and int(info["advance_access_ts"]) > time.time():
                        games[appid_str]["advance_access_ts"] = int(info["advance_access_ts"])
                master_changed = True

            # Subscribe this guild.
            watched.append(appid_str)
            added += 1

        if master_changed:
            await self._save_games(games)
        await self.config.guild(ctx.guild).watched_appids.set(watched)

        lines = [f"✅ Imported **{added}** game(s). This server's watchlist now {len(watched)}/{MAX_GAMES}."]
        if skipped_existing:
            lines.append(f"• Skipped {skipped_existing} already on this server's watchlist.")
        if skipped_full:
            lines.append(f"• Skipped {skipped_full} — watchlist full ({MAX_GAMES} cap).")
        if invalid:
            lines.append(f"• Ignored {invalid} invalid entr(y/ies).")

        await ctx.send("\n".join(lines))

    @commands.command(name="dmirror")
    @owner_only()
    async def dmirror(self, ctx: commands.Context, source: str = None):
        """Mirror another server's watchlist here (owner only).

        `dmirror <guild_id>` — read that server's list live (no duplication,
        always in sync). `dmirror off` — stop. `dmirror` — show status.

        While mirroring, this server is a read-only view: `dlist`, `dcheck`,
        `dupcoming` and `ddepots` read the source list; management commands
        (`dadd`/`dremove`/`dimport`/`dclear`/`dforcecheck`) are disabled until
        you turn the mirror off.
        """
        if not await self._require_guild(ctx):
            return

        current = await self.config.guild(ctx.guild).mirror_guild_id()

        # Status
        if source is None:
            if current:
                n = len(await self.config.guild_from_id(current).watched_appids())
                await ctx.send(
                    f"🔗 This server is mirroring server `{current}` ({n} game(s)). "
                    f"Use `dmirror off` to stop."
                )
            else:
                await ctx.send(
                    "This server is not mirroring any other server. "
                    "Use `dmirror <guild_id>` to start."
                )
            return

        # Turn off
        if source.lower() == "off":
            if not current:
                await ctx.send("This server isn't mirroring anything.")
                return
            await self.config.guild(ctx.guild).mirror_guild_id.set(None)
            await ctx.send("✅ Stopped mirroring. This server uses its own watchlist again.")
            return

        # Set
        if not source.isdigit():
            await ctx.send("❌ Provide a server ID, `off`, or nothing to see status.")
            return
        source_id = int(source)
        if source_id == ctx.guild.id:
            await ctx.send("❌ A server can't mirror itself.")
            return
        if await self.config.guild_from_id(source_id).mirror_guild_id():
            await ctx.send(
                f"❌ Server `{source_id}` is itself a mirror — point at the server that owns the list."
            )
            return

        source_watched = await self.config.guild_from_id(source_id).watched_appids()
        await self.config.guild(ctx.guild).mirror_guild_id.set(source_id)
        note = "" if source_watched else " (its watchlist is currently empty)"
        await ctx.send(
            f"🔗 Now mirroring server `{source_id}` — {len(source_watched)} game(s){note}. "
            f"`dlist`, `dcheck`, `dupcoming`, and `ddepots` here now read that server's list live."
        )

    @commands.command(name="dclear")
    @owner_only()
    async def dclear(self, ctx: commands.Context):
        """Clear this server's entire watchlist (owner only)."""
        if not await self._require_guild(ctx):
            return
        if await self._block_if_mirrored(ctx):
            return
        watched = await self.config.guild(ctx.guild).watched_appids()
        if not watched:
            await ctx.send("📭 This server's watchlist is already empty.")
            return

        view = ConfirmView(ctx.author.id)
        prompt = await ctx.send(
            f"⚠️ This will clear **{len(watched)}** game(s) from this server's watchlist. Continue?",
            view=view,
        )
        await view.wait()
        if view.value is not True:
            if view.value is None:
                await prompt.edit(content="❌ Timed out — watchlist unchanged.", view=None)
            return

        count = len(watched)
        async with ctx.typing():
            for appid_str in list(watched):
                await self._unwatch(ctx.guild, appid_str)
        await ctx.send(f"🗑️ Cleared **{count}** game(s) from this server's watchlist.")

    # ── settings commands (Prefix Only) ───────────────────────────────────
    @commands.group(name="denuvowatch", invoke_without_command=True)
    @owner_only()
    async def denuvowatch(self, ctx: commands.Context):
        """DenuvoWatch — Configuration and settings."""
        await ctx.send_help(ctx.command)
        
    @denuvowatch.command(name="settings", with_app_command=False)
    @owner_only()
    async def denuvowatch_settings(self, ctx: commands.Context):
        """View this server's DenuvoWatch settings."""
        if not await self._require_guild(ctx):
            return
        gconf = self.config.guild(ctx.guild)
        channel_id = await gconf.notify_channel_id()
        user_id = await gconf.notify_user_id()
        role_id = await gconf.notify_role_id()
        watched = await gconf.watched_appids()
        embed = discord.Embed(title="⚙️ DenuvoWatch Settings", color=discord.Color.blurple())
        embed.add_field(name="Notify Channel", value=f"<#{channel_id}>" if channel_id else "Not set", inline=False)
        embed.add_field(name="Notify User", value=f"<@{user_id}>" if user_id else "Not set", inline=True)
        embed.add_field(name="Notify Role", value=f"<@&{role_id}>" if role_id else "Not set", inline=True)
        embed.add_field(name="Watchlist", value=f"{len(watched)}/{MAX_GAMES} games", inline=True)
        await ctx.send(embed=embed)

    @denuvowatch.command(name="channel", with_app_command=False)
    @owner_only()
    async def denuvowatch_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where this server's update embeds are posted."""
        if not await self._require_guild(ctx):
            return
        await self.config.guild(ctx.guild).notify_channel_id.set(channel.id)
        await ctx.send(f"✅ Notify channel set to {channel.mention}.")

    @denuvowatch.command(name="role", with_app_command=False)
    @owner_only()
    async def denuvowatch_role(self, ctx: commands.Context, role: Optional[discord.Role] = None):
        """Set (or clear, if omitted) the role pinged on build changes in this server."""
        if not await self._require_guild(ctx):
            return
        await self.config.guild(ctx.guild).notify_role_id.set(role.id if role else None)
        await ctx.send(f"✅ Notify role set to {role.mention}." if role else "✅ Notify role cleared.")

    @denuvowatch.command(name="user", with_app_command=False)
    @owner_only()
    async def denuvowatch_user(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """Set (or clear, if omitted) the user pinged on build changes in this server."""
        if not await self._require_guild(ctx):
            return
        await self.config.guild(ctx.guild).notify_user_id.set(user.id if user else None)
        await ctx.send(f"✅ Notify user set to {user.mention}." if user else "✅ Notify user cleared.")