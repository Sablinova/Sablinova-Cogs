import asyncio
import re
from datetime import datetime, timezone
from typing import Optional

import io
import discord
import requests
import json
import aiohttp
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red

# ─── Static config ─────────────────────────────────────────────────────────
MAX_GAMES = 100

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# Hardcoded depot allowlists for games with region-specific depots or unknown depot
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


def parse_release_date(date_str: str):
    """Best-effort parse of Steam's free-text release date. None if unparseable."""
    if not date_str:
        return None
    try:
        return date_parser.parse(date_str, fuzzy=True, default=datetime(1900, 1, 1))
    except (ValueError, OverflowError):
        return None


def fetch_app_details(appid: int) -> dict:
    try:
        r = requests.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": appid, "cc": "us", "l": "en"},
            headers=HEADERS, timeout=10
        )
        r.raise_for_status()
        result = r.json().get(str(appid), {})
        return result.get("data", {}) if result.get("success") else {}
    except Exception:
        return {}


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


def check_denuvo_api(data: dict) -> bool:
    return "denuvo" in data.get("drm_notice", "").lower()


def check_denuvo_scrape(appid: int) -> bool:
    try:
        r = requests.get(
            f"https://store.steampowered.com/app/{appid}/",
            headers=HEADERS,
            cookies={"birthtime": "0", "mature_content": "1"},
            timeout=10
        )
        soup = BeautifulSoup(r.text, "html.parser")
        return "denuvo" in soup.get_text().lower()
    except Exception:
        return False


def has_denuvo(appid: int, data: dict) -> bool:
    return check_denuvo_api(data) or check_denuvo_scrape(appid)


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


def strip_html(text: str) -> str:
    """Steam descriptions are HTML — strip tags for a clean Discord embed."""
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    clean = soup.get_text(separator=" ")
    return " ".join(clean.split())


def truncate_to_sentence(text: str, max_words: int = 300, overhead: int = 20) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text

    extended = " ".join(words[:max_words + overhead])
    base_len = len(" ".join(words[:max_words]))
    lookahead_region = extended[base_len:]
    period_in_overhead = lookahead_region.find(".")
    if period_in_overhead != -1:
        return extended[:base_len + period_in_overhead + 1]

    truncated = " ".join(words[:max_words])
    last_period = truncated.rfind(".")
    if last_period != -1:
        return truncated[:last_period + 1]
    return truncated.rstrip() + "…"


def get_game_snapshot(appid: int) -> Optional[dict]:
    data = fetch_app_details(appid)
    if not data:
        return None
    build_id, build_time = fetch_build_id_only(appid)
    release = data.get("release_date", {})
    coming_soon = release.get("coming_soon", False)
    release_date_str = release.get("date", "").strip()
    return {
        "name": data.get("name", f"AppID {appid}"),
        "denuvo": has_denuvo(appid, data),
        "header": data.get("header_image", ""),
        "build_id": build_id,
        "build_time": build_time,
        "coming_soon": bool(coming_soon) if isinstance(coming_soon, bool) else coming_soon == "true",
        "release_date": release_date_str if (coming_soon and release_date_str) else None,
    }

_TRADEMARK_CHARS = "™®©"
_PUNCT_RE = re.compile(r"[:\-–—_'’,.!?]")
_WS_RE = re.compile(r"\s+")

def normalize_game_name(name: str) -> str:
    if not name:
        return ""
    for ch in _TRADEMARK_CHARS:
        name = name.replace(ch, "")
    name = _PUNCT_RE.sub(" ", name)
    name = _WS_RE.sub(" ", name).strip().lower()
    return name

async def resolve_best_game_match(query: str) -> Optional[int]:
    """Search Steam and return the AppID of the best-matching actual game (filters DLC/tools/editors)."""
    raw_candidates = await asyncio.to_thread(search_steam, query)
    raw_candidates = raw_candidates[:10]
    if not raw_candidates:
        return None

    game_candidates = []
    for c in raw_candidates:
        details = await asyncio.to_thread(fetch_app_details, c["appid"])
        if details.get("type") == "game":
            game_candidates.append(c)
        if len(game_candidates) >= 5:
            break

    if not game_candidates:
        return None

    query_norm = normalize_game_name(query)

    exact = [c for c in game_candidates if normalize_game_name(c["name"]) == query_norm]
    if exact:
        return exact[0]["appid"]

    starts = [c for c in game_candidates if normalize_game_name(c["name"]).startswith(query_norm)]
    if starts:
        return starts[0]["appid"]

    starts_rev = [c for c in game_candidates if query_norm.startswith(normalize_game_name(c["name"]))]
    if starts_rev:
        return starts_rev[0]["appid"]

    query_words = query_norm.split()
    word_matches = [
        c for c in game_candidates
        if all(w in normalize_game_name(c["name"]) for w in query_words)
    ]
    if word_matches:
        return word_matches[0]["appid"]

    return game_candidates[0]["appid"]

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
    if old.get("release_date"):
        embed.add_field(name="Expected Date", value=old["release_date"], inline=True)
    if new.get("build_id"):
        embed.add_field(name="Build ID", value=f"`{new['build_id']}`", inline=True)
    embed.add_field(name="Denuvo", value="⚠️ Yes" if new.get("denuvo") else "✅ No", inline=True)
    if new.get("header"):
        embed.set_thumbnail(url=new["header"])
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


# ─── Cog ───────────────────────────────────────────────────────────────────
class DenuvoWatch(commands.Cog):
    """Tracks Denuvo status, build updates, and release dates for a Steam watchlist."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=849201337, force_registration=True)
        self.config.register_global(
            notify_channel_id=None,
            notify_user_id=None,
            notify_role_id=None,
            games={},
            history={},
        )
        self.session = aiohttp.ClientSession() # Added session
        self._startup_task: Optional[asyncio.Task] = None

        # In-memory cache of game names for autocomplete, so it never has to
        # await a Config read (and risk Discord's ~3s autocomplete timeout).
        self._name_cache: list[str] = []
        self._name_cache_ts: float = 0.0
        self._name_cache_ttl: float = 30.0

    # ── lifecycle ────────────────────────────────────────────────────────
    async def cog_load(self):
        self._startup_task = asyncio.create_task(self._startup_sequence())

    def cog_unload(self):
        if self._startup_task is not None:
            self._startup_task.cancel()
        if self.check_games_loop.is_running():
            self.check_games_loop.cancel()
        # Cleanly close the web session
        asyncio.create_task(self.session.close())

    async def _startup_sequence(self):
        await self.bot.wait_until_red_ready()
        games = await self.config.games()
        self._refresh_name_cache_from(games)
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
        self._refresh_name_cache_from(games)

    def _refresh_name_cache_from(self, games: dict):
        self._name_cache = [info.get("name", "") for info in games.values()]
        self._name_cache_ts = asyncio.get_event_loop().time()

    async def _refresh_name_cache(self):
        games = await self._load_games()
        self._refresh_name_cache_from(games)

    async def _load_history(self) -> dict:
        return await self.config.history()

    async def _save_history(self, history: dict):
        await self.config.history.set(history)

    async def _get_notify_mention(self) -> str:
        role_id = await self.config.notify_role_id()
        if role_id:
            return f"<@&{role_id}>"
        user_id = await self.config.notify_user_id()
        if user_id:
            return f"<@{user_id}>"
        return ""

    # ── background check ─────────────────────────────────────────────────
    async def check_games_internal(self, full_refresh: bool = False) -> bool:
        changes = False
        try:
            channel_id = await self.config.notify_channel_id()
            if not channel_id:
                print("[DenuvoWatch][WARN] No notify channel configured.")
                return False
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                print(f"[DenuvoWatch][WARN] Notify channel {channel_id} not found.")
                return False

            games = await self._load_games()
            if not games:
                return False

            allowed = discord.AllowedMentions(users=True, roles=True)

            print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking {len(games)} games…")

            async def check_single(appid_str, old):
                await asyncio.sleep(0.5)
                appid = int(appid_str)
                new = await asyncio.to_thread(get_game_snapshot, appid)
                if new is None:
                    return appid_str, None
                return appid_str, new

            results = await asyncio.gather(*[
                check_single(appid_str, old)
                for appid_str, old in games.items()
            ])
            changed_appids = set()

            for appid_str, new in results:
                if new is None:
                    continue
                old = games[appid_str]
                appid = int(appid_str)

                # Denuvo change
                if old.get("denuvo") and not new["denuvo"]:
                    await channel.send(embed=build_denuvo_embed(appid, "denuvo_removed", old, new))
                    changes = True
                elif not old.get("denuvo") and new["denuvo"]:
                    await channel.send(embed=build_denuvo_embed(appid, "denuvo_added", old, new))
                    changes = True

                # Release notification
                if old.get("coming_soon") and not new.get("coming_soon"):
                    await channel.send(embed=build_release_embed(appid, old, new))
                    changes = True
                    print(f"[INFO] {new['name']} has released!")

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

                        mention = await self._get_notify_mention()
                        await channel.send(
                            content=mention, 
                            embed=build_depot_embed(appid, old_build, new_build, new),
                            allowed_mentions=allowed
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
                    "denuvo": new["denuvo"],
                }
                if build_actually_changed or not old_build:
                    update_fields["build_id"] = new["build_id"]
                    update_fields["build_time"] = new.get("build_time")
                games[appid_str].update(update_fields)

                if new.get("coming_soon"):
                    games[appid_str]["coming_soon"] = True
                    if new.get("release_date"):
                        games[appid_str]["release_date"] = new["release_date"]
                else:
                    dropped = False
                    for key in ("coming_soon", "release_date"):
                        if key in games[appid_str]:
                            games[appid_str].pop(key)
                            dropped = True
                    if dropped:
                        print(f"[INFO] {new['name']} has released, cleared coming_soon + release_date.")

            # Full refresh for unchanged games
            if full_refresh:
                refresh_targets = [a for a in games if a not in changed_appids]

                async def refresh_single(appid_str):
                    _, _, manifests, depot_sizes = await asyncio.to_thread(fetch_build_id, int(appid_str))
                    return appid_str, manifests, depot_sizes

                refresh_results = await asyncio.gather(*[refresh_single(a) for a in refresh_targets])

                for appid_str, manifests, depot_sizes in refresh_results:
                    if manifests:
                        games[appid_str]["manifests"] = manifests
                    if depot_sizes:
                        games[appid_str]["depot_sizes"] = depot_sizes

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
    async def _add_appid(self, ctx_or_interaction, games: dict, appid: int, send_func):
        if str(appid) in games:
            await send_func(f"ℹ️ **{games[str(appid)]['name']}** is already on the watchlist.")
            return

        if len(games) >= MAX_GAMES:
            await send_func(f"❌ Watchlist is full ({MAX_GAMES} games max).")
            return

        snapshot = await asyncio.to_thread(get_game_snapshot, appid)
        if snapshot is None:
            await send_func(f"❌ Couldn't fetch data for AppID `{appid}`.")
            return

        _, _, manifests, depot_sizes = await asyncio.to_thread(fetch_build_id, appid)

        entry = {
            "name": snapshot["name"],
            "denuvo": snapshot["denuvo"],
            "build_id": snapshot["build_id"],
        }
        if snapshot.get("coming_soon"):
            entry["coming_soon"] = True
            if snapshot.get("release_date"):
                entry["release_date"] = snapshot["release_date"]
        entry["manifests"] = manifests
        entry["depot_sizes"] = depot_sizes

        games[str(appid)] = entry
        await self._save_games(games)

        embed = discord.Embed(
            title="✅ Added to Watchlist",
            description=f"**[{snapshot['name']}](https://store.steampowered.com/app/{appid}/)**",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Denuvo", value="⚠️ Yes" if snapshot["denuvo"] else "✅ No", inline=True)
        embed.add_field(name="Build ID", value=f"`{snapshot['build_id']}`" if snapshot["build_id"] else "Unknown", inline=True)
        embed.add_field(name="Watchlist", value=f"{len(games)}/{MAX_GAMES} games", inline=True)
        if snapshot.get("header"):
            embed.set_thumbnail(url=snapshot["header"])
        embed.set_footer(text=f"AppID {appid}")
        await send_func(embed=embed)

    async def _game_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list:
        now = asyncio.get_event_loop().time()
        if now - self._name_cache_ts > self._name_cache_ttl:
            try:
                await asyncio.wait_for(self._refresh_name_cache(), timeout=1.5)
            except Exception:
                pass  # fall back to whatever's already cached (even if empty/stale)

        current_lower = current.lower()
        matches = [name for name in self._name_cache if current_lower in name.lower()]
        return [
            discord.app_commands.Choice(name=name, value=name)
            for name in matches[:25]
        ]

    async def get_total_size_with_dlc(self, appid: int) -> tuple[int, dict]:
        _, _, _, base_depot_sizes = await asyncio.to_thread(fetch_build_id, appid)
        depot_sizes = dict(base_depot_sizes)

        details = await asyncio.to_thread(fetch_app_details, appid)
        store_dlc_ids = set((details or {}).get("dlc", []))

        steamcmd_dlc_ids = set(await asyncio.to_thread(get_dlc_appids_from_steamcmd, appid))

        all_dlc_ids = store_dlc_ids | steamcmd_dlc_ids

        async def fetch_dlc(dlc_appid: int):
            _, _, _, dlc_depot_sizes = await asyncio.to_thread(fetch_build_id, dlc_appid)
            return dlc_depot_sizes

        if all_dlc_ids:
            results = await asyncio.gather(*[fetch_dlc(d) for d in all_dlc_ids])
            for dlc_depot_sizes in results:
                depot_sizes.update(dlc_depot_sizes)

        total = sum(depot_sizes.values())
        return total, depot_sizes

    # ── command group (denuvowatch) ───────────────────────────────────────

    @commands.hybrid_command(name="dadd")
    @discord.app_commands.describe(query="Game name or Steam AppID")
    @owner_only()
    async def dadd(self, ctx: commands.Context, *, query: str):
        """Add a game to the watchlist by name or AppID."""
        async with ctx.typing():
            games = await self._load_games()

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

        if len(candidates) == 1:
            await self._add_appid(ctx, games, candidates[0]["appid"], ctx.send)
            return

        options = [
            discord.SelectOption(label=c["name"][:100], value=str(c["appid"]))
            for c in candidates
        ]
        select = discord.ui.Select(placeholder="Choose a game…", options=options)

        async def select_callback(inter: discord.Interaction):
            await inter.response.defer(thinking=True)
            fresh_games = await self._load_games()
            await self._add_appid(inter, fresh_games, int(select.values[0]), inter.followup.send)

        select.callback = select_callback
        view = discord.ui.View(timeout=60)
        view.add_item(select)
        await ctx.send("Multiple results found — pick one:", view=view)

    @commands.hybrid_command(name="dremove")
    @owner_only()
    @discord.app_commands.describe(query="Game name or AppID")
    async def dremove(self, ctx: commands.Context, *, query: str):
        """Remove a game from the watchlist."""
        games = await self._load_games()
        if not games:
            await ctx.send("📭 Watchlist is empty.", ephemeral=True)
            return

        matches = []
        query_lower = query.lower()

        # Exact name match first (covers autocomplete selections, which supply the exact stored name)
        exact_matches = [
            (appid_str, info) for appid_str, info in games.items()
            if info["name"].lower() == query_lower
        ]
        if exact_matches:
            matches = exact_matches
        else:
            for appid_str, info in games.items():
                if query.isdigit() and appid_str == query:
                    matches = [(appid_str, info)]
                    break
                elif query_lower in info["name"].lower():
                    matches.append((appid_str, info))

            if not matches:
                await ctx.send(f"❌ No game matching `{query}` on the watchlist.", ephemeral=True)
                return

        if len(matches) == 1:
            appid_str, info = matches[0]
            del games[appid_str]
            await self._save_games(games)
            await ctx.send(f"🗑️ Removed **{info['name']}** from the watchlist.", ephemeral=True)
            return

        options = [
            discord.SelectOption(label=info["name"][:100], value=appid_str)
            for appid_str, info in matches[:5]
        ]
        select = discord.ui.Select(placeholder="Which game to remove?", options=options)

        async def cb(inter: discord.Interaction):
            fresh_games = await self._load_games()
            chosen_id = select.values[0]
            name = fresh_games[chosen_id]["name"]
            del fresh_games[chosen_id]
            await self._save_games(fresh_games)
            await inter.response.send_message(f"🗑️ Removed **{name}** from the watchlist.", ephemeral=True)

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
        games_dict = await self._load_games()
        if not games_dict:
            await ctx.send("📭 Watchlist is empty. Use `dadd` to add games.")
            return

        games = list(games_dict.items())

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
    @discord.app_commands.describe(query="Game name or AppID")
    async def dcheck(self, ctx: commands.Context, *, query: str):
        """Instantly check a game's current status."""
        async with ctx.typing():
            games = await self._load_games()

            appid = None
            if query.isdigit():
                appid = int(query)
            else:
                for appid_str, info in games.items():
                    if query.lower() in info["name"].lower():
                        appid = int(appid_str)
                        break
                if appid is None:
                    appid = await resolve_best_game_match(query)

            if appid is None:
                await ctx.send(f"❌ Couldn't resolve `{query}` to a Steam game.")
                return

            snapshot = await asyncio.to_thread(get_game_snapshot, appid)
            if snapshot is None:
                await ctx.send(f"❌ Couldn't fetch data for AppID `{appid}`.")
                return

            in_watchlist = str(appid) in games
            stored = games.get(str(appid), {})

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

        if is_coming_soon and snapshot.get("release_date"):
            parsed_date = parse_release_date(snapshot["release_date"])
            if parsed_date:
                if parsed_date.tzinfo is None:
                    parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                unix_ts = int(parsed_date.timestamp())
                embed.add_field(
                    name="Release Date",
                    value=f"<t:{unix_ts}:D> (<t:{unix_ts}:R>)",
                    inline=True
                )
            else:
                embed.add_field(name="Release Date", value=snapshot["release_date"], inline=True)
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
        """Manually trigger a full watchlist scan."""
        await ctx.send("🔄 Running full watchlist check now…")
        changes = await self.check_games_internal(full_refresh=True)
        if not changes:
            await ctx.send("✅ Check complete — no changes detected.")

    @commands.hybrid_command(name="dupcoming")
    async def dupcoming(self, ctx: commands.Context):
        """Show all upcoming (unreleased) games in the watchlist."""
        games = await self._load_games()

        upcoming = [
            (appid_str, info) for appid_str, info in games.items()
            if info.get("coming_soon")
        ]

        if not upcoming:
            await ctx.send("📭 No upcoming games on the watchlist right now.")
            return

        def sort_key(item):
            _, info = item
            parsed = parse_release_date(info.get("release_date"))
            return (parsed is None, parsed or datetime.max)

        upcoming.sort(key=sort_key)

        embed = discord.Embed(
            title=f"🚀 Upcoming Games ({len(upcoming)})",
            color=discord.Color.gold()
        )
        lines = []
        for appid_str, info in upcoming:
            date = info.get("release_date") or "Date TBA"
            lines.append(f"**{info['name']}** `{appid_str}` — {date}")
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="dsummary")
    @discord.app_commands.describe(query="Game name or Steam AppID")
    async def dsummary(self, ctx: commands.Context, *, query: str):
        """Show a game's description from its Steam store page."""
        async with ctx.typing():
            appid = None
            if query.isdigit():
                appid = int(query)
            else:
                appid = await resolve_best_game_match(query)

            if appid is None:
                await ctx.send(f"❌ Couldn't resolve `{query}` to a Steam game.")
                return

            data = await asyncio.to_thread(fetch_app_details, appid)
            if not data:
                await ctx.send(f"❌ Couldn't fetch data for AppID `{appid}`.")
                return

            name = data.get("name", f"AppID {appid}")
            short_desc = strip_html(data.get("short_description", ""))
            about = strip_html(data.get("about_the_game", "")) or strip_html(data.get("detailed_description", ""))

            full_text = short_desc or "No description available."
            if about and about != short_desc:
                full_text += f"\n\n{about}"

            description = truncate_to_sentence(full_text, max_words=150)

        embed = discord.Embed(
            title=f"📖 {name}",
            url=f"https://store.steampowered.com/app/{appid}/",
            description=description[:2000],
            color=discord.Color.blurple()
        )

        price_overview = data.get("price_overview")
        if data.get("is_free"):
            embed.add_field(name="Price", value="Free to Play", inline=True)
        elif price_overview:
            final = price_overview["final_formatted"]
            discount = price_overview.get("discount_percent", 0)
            if discount > 0:
                initial = price_overview["initial_formatted"]
                embed.add_field(name="Price", value=f"~~{initial}~~ **{final}** (-{discount}%)", inline=True)
            else:
                embed.add_field(name="Price", value=final, inline=True)

        genres = data.get("genres", [])
        if genres:
            embed.add_field(name="Genres", value=", ".join(g["description"] for g in genres), inline=True)

        developers = data.get("developers", [])
        if developers:
            embed.add_field(name="Developer", value=", ".join(developers), inline=True)

        release = data.get("release_date", {})
        if release.get("date"):
            embed.add_field(name="Release Date", value=release["date"], inline=True)

        if data.get("header_image"):
            embed.set_thumbnail(url=data["header_image"])
        embed.set_footer(text=f"AppID {appid}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="ddepots")
    @discord.app_commands.describe(
        query="Game name or AppID",
        index="Which build: 0=current (default), 1=previous, 2=two builds ago, 3=three builds ago",
        show_manifests="Also show manifest IDs (default: False)"
    )
    async def ddepots(self, ctx: commands.Context, query: str, index: int = 0, show_manifests: bool = False):
        """Show depot info for a watched game."""
        games = await self._load_games()

        appid = None
        if query.isdigit():
            appid = int(query)
        else:
            for appid_str, info in games.items():
                if query.lower() in info["name"].lower():
                    appid = int(appid_str)
                    break

        if appid is None:
            await ctx.send(f"❌ `{query}` not found in your watchlist.")
            return

        info = games.get(str(appid))
        if not info:
            await ctx.send(f"❌ `{query}` not found in your watchlist.")
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

    @commands.hybrid_command(name="dexport")
    async def dexport(self, ctx: commands.Context):
        """Export the current watchlist as a JSON file (re-importable via dimport)."""
        games = await self._load_games()
        if not games:
            await ctx.send("📭 Watchlist is empty — nothing to export.")
            return

        payload = {"games": games}
        buffer = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
        filename = f"steam_data_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"

        await ctx.send(
            f"📤 Exported **{len(games)}** game(s).",
            file=discord.File(fp=buffer, filename=filename),
        )
	
    @commands.hybrid_command(name="dimport")
    @owner_only()
    async def dimport(self, ctx: commands.Context, url: str = None):
        """Import games into the watchlist from a JSON file or URL."""
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
        added, skipped_existing, skipped_full, invalid = 0, 0, 0, 0

        for appid_str, info in incoming.items():
            if not str(appid_str).isdigit() or not isinstance(info, dict):
                invalid += 1
                continue
            
            appid_str = str(appid_str)
            if appid_str in games:
                skipped_existing += 1
                continue
            if len(games) >= MAX_GAMES:
                skipped_full += 1
                continue
            
            # Safely extract all fields the current bot relies on
            games[appid_str] = {
                "name": info.get("name", f"AppID {appid_str}"),
                "denuvo": bool(info.get("denuvo", False)),
                "build_id": info.get("build_id"),
                "build_time": info.get("build_time"),
                "manifests": info.get("manifests", {}),
                "depot_sizes": info.get("depot_sizes", {}),
            }
            
            # Only add release data if it exists in the import
            if info.get("coming_soon"):
                games[appid_str]["coming_soon"] = True
                if info.get("release_date"):
                    games[appid_str]["release_date"] = info["release_date"]
                    
            added += 1

        # Use our custom helper to ensure alphabetical sorting
        await self._save_games(games)

        lines = [f"✅ Imported **{added}** game(s). Watchlist now {len(games)}/{MAX_GAMES}."]
        if skipped_existing:
            lines.append(f"• Skipped {skipped_existing} already on the watchlist.")
        if skipped_full:
            lines.append(f"• Skipped {skipped_full} — watchlist full ({MAX_GAMES} cap).")
        if invalid:
            lines.append(f"• Ignored {invalid} invalid entr(y/ies).")
            
        await ctx.send("\n".join(lines))
    	
    # ── settings commands (Prefix Only) ───────────────────────────────────
    @commands.group(name="denuvowatch", invoke_without_command=True)
    @owner_only()
    async def denuvowatch(self, ctx: commands.Context):
        """DenuvoWatch — Configuration and settings."""
        await ctx.send_help(ctx.command)
        
    @denuvowatch.command(name="settings", with_app_command=False)
    @owner_only()
    async def denuvowatch_settings(self, ctx: commands.Context):
        """View current DenuvoWatch settings."""
        channel_id = await self.config.notify_channel_id()
        user_id = await self.config.notify_user_id()
        role_id = await self.config.notify_role_id()
        embed = discord.Embed(title="⚙️ DenuvoWatch Settings", color=discord.Color.blurple())
        embed.add_field(name="Notify Channel", value=f"<#{channel_id}>" if channel_id else "Not set", inline=False)
        embed.add_field(name="Notify User", value=f"<@{user_id}>" if user_id else "Not set", inline=True)
        embed.add_field(name="Notify Role", value=f"<@&{role_id}>" if role_id else "Not set", inline=True)
        await ctx.send(embed=embed)

    @denuvowatch.command(name="channel", with_app_command=False)
    @owner_only()
    async def denuvowatch_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where update embeds are posted."""
        await self.config.notify_channel_id.set(channel.id)
        await ctx.send(f"✅ Notify channel set to {channel.mention}.")

    @denuvowatch.command(name="role", with_app_command=False)
    @owner_only()
    async def denuvowatch_role(self, ctx: commands.Context, role: Optional[discord.Role] = None):
        """Set (or clear, if omitted) the role pinged on build changes."""
        await self.config.notify_role_id.set(role.id if role else None)
        await ctx.send(f"✅ Notify role set to {role.mention}." if role else "✅ Notify role cleared.")

    @denuvowatch.command(name="user", with_app_command=False)
    @owner_only()
    async def denuvowatch_user(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """Set (or clear, if omitted) the user pinged on build changes."""
        await self.config.notify_user_id.set(user.id if user else None)
        await ctx.send(f"✅ Notify user set to {user.mention}." if user else "✅ Notify user cleared.")