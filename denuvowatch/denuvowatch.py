import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Union

import aiohttp
import discord
from bs4 import BeautifulSoup
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.sabby.denuvowatch")

MAX_GAMES = 50
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# ManifestHub2 — static depot data mirror (one git branch per Steam AppID).
MANIFESTHUB_OWNER = "SSMGAlt"
MANIFESTHUB_REPO = "ManifestHub2"
MANIFESTHUB_RAW = f"https://raw.githubusercontent.com/{MANIFESTHUB_OWNER}/{MANIFESTHUB_REPO}"

# Steam depot manifest section magics (little-endian uint32).
MANIFEST_PAYLOAD_MAGIC = 0x71F617D0


def _read_varint(buf: bytes, i: int):
    shift = 0
    result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, i


def _iter_protobuf_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for a protobuf message."""
    i = 0
    n = len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        fn = key >> 3
        wt = key & 7
        if wt == 0:  # varint
            v, i = _read_varint(buf, i)
            yield fn, wt, v
        elif wt == 2:  # length-delimited
            ln, i = _read_varint(buf, i)
            v = buf[i:i + ln]
            i += ln
            yield fn, wt, v
        elif wt == 5:  # 32-bit
            v = buf[i:i + 4]
            i += 4
            yield fn, wt, v
        elif wt == 1:  # 64-bit
            v = buf[i:i + 8]
            i += 8
            yield fn, wt, v
        else:
            raise ValueError(f"unsupported protobuf wire type {wt}")


def _decrypt_filename(enc_b64: bytes, key: bytes) -> str:
    """Decrypt a Steam-encrypted manifest filename.

    Format: base64( AES-ECB(IV)[16] + AES-CBC(payload) ), PKCS7-padded, with
    the first 16 bytes being the ECB-encrypted IV. Requires the 32-byte AES
    depot key. Raises on failure.
    """
    import base64

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    data = base64.b64decode(enc_b64)
    iv = Cipher(algorithms.AES(key), modes.ECB()).decryptor().update(data[:16])
    body = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor().update(data[16:])
    pad = body[-1]
    if 1 <= pad <= 16:
        body = body[:-pad]
    return body.rstrip(b"\x00").decode("utf-8", "replace")


def _looks_like_path(name: str) -> bool:
    """Heuristic: does this look like a real (decrypted) file path?"""
    if not name:
        return False
    # Real paths are printable and almost always contain a separator or a dot.
    if not all(32 <= ord(c) < 127 or ord(c) > 160 for c in name):
        return False
    return ("/" in name) or ("\\" in name) or ("." in name)


def parse_manifest_files(blob: bytes, depot_key: Optional[bytes] = None):
    """Parse a raw Steam depot manifest blob into a list of (path, size).

    Self-contained: reads the ContentManifestPayload section and its repeated
    FileMapping messages (filename=field 1, size=field 2, flags=field 4).

    Some manifests store filenames AES-encrypted (base64 text). The encrypted
    flag isn't reliably present in the ManifestHub mirror, so when a
    `depot_key` is available and a name doesn't look like a real path, this
    attempts to decrypt it and uses the result if it looks valid.
    """
    import struct

    if len(blob) < 8:
        return []
    magic, length = struct.unpack_from("<II", blob, 0)
    if magic != MANIFEST_PAYLOAD_MAGIC:
        return []
    payload = blob[8:8 + length]

    files = []
    for fn, wt, v in _iter_protobuf_fields(payload):
        if fn != 1 or wt != 2:  # field 1 = repeated FileMapping
            continue
        raw_name = None
        size = 0
        flags = 0
        for ffn, fwt, fv in _iter_protobuf_fields(v):
            if ffn == 1 and fwt == 2:
                raw_name = fv
            elif ffn == 2 and fwt == 0:
                size = fv
            elif ffn == 4 and fwt == 0:
                flags = fv
        if raw_name is None:
            continue

        name = raw_name.decode("utf-8", "replace").rstrip("\x00")
        # If it doesn't look like a path and we have a key, try to decrypt.
        if not _looks_like_path(name) and depot_key:
            try:
                decrypted = _decrypt_filename(raw_name.decode("ascii"), depot_key)
                if _looks_like_path(decrypted):
                    name = decrypted
            except Exception:
                pass

        # flag 0x40 = directory; skip those.
        if flags & 0x40:
            continue
        files.append((name.replace("\\", "/"), size))
    return files

DEFAULT_GLOBALS = {
    "games": {},          # appid_str -> {name, denuvo, build_id, build_time, header}
    "notify_channel": None,
    "notify_user": None,   # legacy: pinged on build updates only
    "mention": None,       # {"type": "user"|"role", "id": int} pinged on ALL updates
    "interval_minutes": 10,
    "admins": [],          # user IDs allowed to use owner-gated commands
}


def is_owner_or_admin():
    """Allow the bot owner, or a user manually added via `addadmin`."""

    async def predicate(ctx: commands.Context) -> bool:
        if await ctx.bot.is_owner(ctx.author):
            return True
        cog = ctx.cog
        if cog is None:
            return False
        admins = await cog.config.admins()
        return ctx.author.id in admins

    return commands.check(predicate)


class DenuvoWatch(commands.Cog):
    """Watch Steam games for Denuvo and build changes.

    Monitors a single global watchlist and alerts a configured channel when a
    game's Denuvo anti-tamper status flips or when its public build is updated.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=0xDE0040A7C8, force_registration=True
        )
        self.config.register_global(**DEFAULT_GLOBALS)
        self.session = aiohttp.ClientSession(headers=HEADERS)
        self._check_lock = asyncio.Lock()
        self.check_games.start()

    def cog_unload(self):
        self.check_games.cancel()
        asyncio.create_task(self.session.close())

    # ─── Steam helpers ──────────────────────────────────────────────────────

    async def fetch_app_details(self, appid: int) -> dict:
        try:
            async with self.session.get(
                "https://store.steampowered.com/api/appdetails",
                params={"appids": appid, "cc": "us", "l": "en"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                r.raise_for_status()
                payload = await r.json()
            result = payload.get(str(appid), {})
            return result.get("data", {}) if result.get("success") else {}
        except Exception:
            return {}

    async def fetch_build_id(self, appid: int):
        """Return (build_id, build_time) for the public branch via SteamCMD API."""
        try:
            async with self.session.get(
                f"https://api.steamcmd.net/v1/info/{appid}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
            depots = (
                data.get("data", {}).get(str(appid), {}).get("depots", {})
            )
            public_branch = depots.get("branches", {}).get("public", {})
            build_id = public_branch.get("buildid")
            timeupdated = public_branch.get("timeupdated")
            return (
                str(build_id) if build_id else None,
                int(timeupdated) if timeupdated else None,
            )
        except Exception:
            return None, None

    @staticmethod
    def _check_denuvo_api(data: dict) -> bool:
        return "denuvo" in data.get("drm_notice", "").lower()

    async def _check_denuvo_scrape(self, appid: int) -> bool:
        try:
            async with self.session.get(
                f"https://store.steampowered.com/app/{appid}/",
                cookies={"birthtime": "0", "mature_content": "1"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                text = await r.text()
            soup = BeautifulSoup(text, "html.parser")
            return "denuvo" in soup.get_text().lower()
        except Exception:
            return False

    async def has_denuvo(self, appid: int, data: dict) -> bool:
        if self._check_denuvo_api(data):
            return True
        return await self._check_denuvo_scrape(appid)

    async def search_steam(self, query: str, games_only: bool = False) -> list[dict]:
        try:
            async with self.session.get(
                "https://store.steampowered.com/api/storesearch/",
                params={"term": query, "cc": "us", "l": "en"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                payload = await r.json()
            if games_only:
                # Filter out obvious DLC/soundtrack/edition entries
                dlc_keywords = ["dlc", "soundtrack", "ost", "pack", "bundle", "edition", "content", "season pass"]
                items = [
                    i for i in items
                    if not any(kw in i.get("name", "").lower() for kw in dlc_keywords)
                ]
            return [{"appid": i["id"], "name": i["name"]} for i in payload.get("items", [])]
        except Exception:
            return []

    async def get_game_snapshot(self, appid: int) -> Optional[dict]:
        data = await self.fetch_app_details(appid)
        if not data:
            return None
        denuvo = await self.has_denuvo(appid, data)
        build_id, build_time = await self.fetch_build_id(appid)
        return {
            "name": data.get("name", f"AppID {appid}"),
            "denuvo": denuvo,
            "header": data.get("header_image", ""),
            "build_id": build_id,
            "build_time": build_time,
        }

    # ─── ManifestHub2 (depot file listings) ──────────────────────────────────

    async def mh_fetch_metadata(self, appid: int) -> Optional[dict]:
        """Fetch {appid}.json from ManifestHub2. Returns dict or None (404)."""
        url = f"{MANIFESTHUB_RAW}/{appid}/{appid}.json"
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                if r.status == 404:
                    return None
                r.raise_for_status()
                return await r.json(content_type=None)
        except Exception:
            log.exception("ManifestHub metadata fetch failed for %s", appid)
            return None

    async def mh_fetch_manifest_blob(self, appid: int, depot_id: str, gid: str) -> Optional[bytes]:
        """Fetch a raw {depot}_{gid}.manifest blob from ManifestHub2."""
        url = f"{MANIFESTHUB_RAW}/{appid}/{depot_id}_{gid}.manifest"
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                if r.status == 404:
                    return None
                r.raise_for_status()
                return await r.read()
        except Exception:
            log.exception("ManifestHub blob fetch failed for %s/%s_%s", appid, depot_id, gid)
            return None

    @staticmethod
    def _public_depots(metadata: dict):
        """Yield (depot_id_str, gid, depot_key_bytes_or_None) for public depots."""
        depot_root = metadata.get("depot", {}) or {}
        for key, val in depot_root.items():
            if not str(key).isdigit() or not isinstance(val, dict):
                continue
            gid = (val.get("manifests", {}) or {}).get("public", {}).get("gid")
            if not gid:
                continue
            keyhex = val.get("decryptionkey")
            depot_key = None
            if isinstance(keyhex, str) and len(keyhex) == 64:
                try:
                    depot_key = bytes.fromhex(keyhex)
                except ValueError:
                    depot_key = None
            yield str(key), str(gid), depot_key

    async def get_exe_paths(self, appid: int):
        """Return (name, exe_paths) for an app via ManifestHub2, or (name, None)."""
        metadata = await self.mh_fetch_metadata(appid)
        if metadata is None:
            return None, None
        name = metadata.get("name") or f"AppID {appid}"
        exes = []
        seen = set()
        for depot_id, gid, depot_key in self._public_depots(metadata):
            blob = await self.mh_fetch_manifest_blob(appid, depot_id, gid)
            if not blob:
                continue
            for path, _size in parse_manifest_files(blob, depot_key):
                if path.lower().endswith(".exe") and path not in seen:
                    seen.add(path)
                    exes.append(path)
        exes.sort(key=str.lower)
        return name, exes

    # ─── Embed builders ─────────────────────────────────────────────────────

    @staticmethod
    def build_denuvo_embed(appid: int, change_type: str, old: dict, new: dict) -> discord.Embed:
        name = new.get("name", old.get("name", f"AppID {appid}"))
        url = f"https://store.steampowered.com/app/{appid}/"
        if change_type == "denuvo_removed":
            embed = discord.Embed(
                title="🎉 Denuvo Removed!",
                description=f"**[{name}]({url})** no longer has Denuvo anti-tamper.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Before", value="⚠️ Had Denuvo", inline=True)
            embed.add_field(name="After", value="✅ Denuvo-free", inline=True)
        else:
            embed = discord.Embed(
                title="⚠️ Denuvo Added",
                description=f"**[{name}]({url})** now has Denuvo anti-tamper.",
                color=discord.Color.red(),
            )
            embed.add_field(name="Before", value="✅ Denuvo-free", inline=True)
            embed.add_field(name="After", value="⚠️ Has Denuvo", inline=True)
        if new.get("header"):
            embed.set_thumbnail(url=new["header"])
        embed.set_footer(text=f"AppID {appid} • DenuvoWatch")
        embed.timestamp = datetime.now(timezone.utc)
        return embed

    @staticmethod
    def build_depot_embed(appid: int, old_build: str, new_build: str, new: dict) -> discord.Embed:
        name = new.get("name", f"AppID {appid}")
        url = f"https://store.steampowered.com/app/{appid}/"
        embed = discord.Embed(
            title="🔧 Build Updated",
            description=f"**[{name}]({url})** received a new build.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Old Build ID", value=f"`{old_build}`", inline=True)
        embed.add_field(name="New Build ID", value=f"`{new_build}`", inline=True)
        if new.get("build_time"):
            embed.add_field(name="Build Pushed", value=f"<t:{new['build_time']}:R>", inline=True)
        if new.get("header"):
            embed.set_thumbnail(url=new["header"])
        embed.set_footer(text=f"AppID {appid} • DenuvoWatch")
        embed.timestamp = datetime.now(timezone.utc)
        return embed

    # ─── Background check ────────────────────────────────────────────────────

    @staticmethod
    def _format_mention(mention: Optional[dict]) -> Optional[str]:
        """Turn a stored mention dict into a pingable string, or None."""
        if not mention or not mention.get("id"):
            return None
        if mention.get("type") == "role":
            return f"<@&{mention['id']}>"
        return f"<@{mention['id']}>"

    async def check_games_internal(self) -> bool:
        """Scan the whole watchlist. Returns True if any change was detected."""
        if self._check_lock.locked():
            return False
        async with self._check_lock:
            changes = False
            try:
                channel_id = await self.config.notify_channel()
                channel = self.bot.get_channel(channel_id) if channel_id else None
                if channel is None:
                    log.warning("Notify channel not set or not found (%s).", channel_id)
                    return False

                games = await self.config.games()
                if not games:
                    return False

                notify_user = await self.config.notify_user()
                mention = self._format_mention(await self.config.mention())
                allowed = discord.AllowedMentions(users=True, roles=True)
                log.info("Checking %d games…", len(games))

                async def check_single(appid_str):
                    await asyncio.sleep(0.5)
                    snap = await self.get_game_snapshot(int(appid_str))
                    return appid_str, snap

                results = await asyncio.gather(
                    *[check_single(a) for a in games.keys()]
                )

                for appid_str, new in results:
                    if new is None:
                        continue
                    old = games.get(appid_str, {})
                    appid = int(appid_str)

                    if old.get("denuvo") and not new["denuvo"]:
                        await channel.send(
                            content=mention,
                            embed=self.build_denuvo_embed(appid, "denuvo_removed", old, new),
                            allowed_mentions=allowed,
                        )
                        changes = True
                    elif not old.get("denuvo") and new["denuvo"]:
                        await channel.send(
                            content=mention,
                            embed=self.build_denuvo_embed(appid, "denuvo_added", old, new),
                            allowed_mentions=allowed,
                        )
                        changes = True

                    old_build = old.get("build_id")
                    new_build = new.get("build_id")
                    if old_build and new_build and old_build != new_build:
                        pings = [p for p in (mention, f"<@{notify_user}>" if notify_user else None) if p]
                        content = " ".join(pings) if pings else None
                        await channel.send(
                            content=content,
                            embed=self.build_depot_embed(appid, old_build, new_build, new),
                            allowed_mentions=allowed,
                        )
                        changes = True

                    games[appid_str] = {
                        "name": new["name"],
                        "denuvo": new["denuvo"],
                        "build_id": new["build_id"],
                        "build_time": new.get("build_time"),
                        "header": new.get("header", old.get("header", "")),
                    }

                await self.config.games.set(games)
                log.info("Check complete.")
            except Exception:
                log.exception("check_games crashed")
            return changes

    @tasks.loop(minutes=10)
    async def check_games(self):
        await self.check_games_internal()

    @check_games.before_loop
    async def before_check_games(self):
        await self.bot.wait_until_red_ready()
        # Sync the loop interval with the stored config value.
        minutes = await self.config.interval_minutes()
        if minutes and minutes != self.check_games.minutes:
            self.check_games.change_interval(minutes=minutes)

    # ─── Watchlist commands (hybrid: slash + prefix) ─────────────────────────

    async def _resolve_appid(self, query: str, games: dict) -> Optional[int]:
        if query.isdigit():
            return int(query)
        for appid_str, info in games.items():
            if query.lower() in info.get("name", "").lower():
                return int(appid_str)
        results = await self.search_steam(query)
        return results[0]["appid"] if results else None

    @commands.hybrid_command(name="dadd", description="Add a Steam game to the watchlist by name or AppID")
    @is_owner_or_admin()
    async def dadd(self, ctx: commands.Context, *, query: str):
        """Add a game to the watchlist (searches Steam if you pass a name)."""
        await ctx.defer()
        games = await self.config.games()

        if query.isdigit():
            appid = int(query)
            snapshot = await self.get_game_snapshot(appid)
            if snapshot is None:
                await ctx.send(f"❌ Couldn't find a game with AppID `{appid}`.")
                return
            candidates = [{"appid": appid, "name": snapshot["name"]}]
        else:
            candidates = (await self.search_steam(query, games_only=True))[:5]
            if not candidates:
                await ctx.send("❌ No results found on Steam.")
                return

        if len(candidates) == 1:
            await self._add_appid(ctx, candidates[0]["appid"])
            return

        options = [
            discord.SelectOption(label=c["name"][:100], value=str(c["appid"]))
            for c in candidates
        ]
        select = discord.ui.Select(placeholder="Choose a game…", options=options)

        async def select_callback(inter: discord.Interaction):
            await inter.response.defer()
            await self._add_appid(ctx, int(select.values[0]), interaction=inter)

        select.callback = select_callback
        view = discord.ui.View(timeout=60)
        view.add_item(select)
        await ctx.send("Multiple results found — pick one:", view=view)

    async def _add_appid(self, ctx: commands.Context, appid: int, interaction: discord.Interaction = None):
        async def send(*args, **kwargs):
            if interaction is not None:
                return await interaction.followup.send(*args, **kwargs)
            return await ctx.send(*args, **kwargs)

        games = await self.config.games()

        if str(appid) in games:
            await send(f"ℹ️ **{games[str(appid)]['name']}** is already on the watchlist.")
            return

        if len(games) >= MAX_GAMES:
            await send(f"❌ Watchlist is full ({MAX_GAMES} games max).")
            return

        snapshot = await self.get_game_snapshot(appid)
        if snapshot is None:
            await send(f"❌ Couldn't fetch data for AppID `{appid}`.")
            return

        games[str(appid)] = {
            "name": snapshot["name"],
            "denuvo": snapshot["denuvo"],
            "build_id": snapshot["build_id"],
            "build_time": snapshot.get("build_time"),
            "header": snapshot.get("header", ""),
        }
        await self.config.games.set(games)

        embed = discord.Embed(
            title="✅ Added to Watchlist",
            description=f"**[{snapshot['name']}](https://store.steampowered.com/app/{appid}/)**",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Denuvo", value="⚠️ Yes" if snapshot["denuvo"] else "✅ No", inline=True)
        embed.add_field(name="Build ID", value=f"`{snapshot['build_id']}`" if snapshot["build_id"] else "Unknown", inline=True)
        embed.add_field(name="Watchlist", value=f"{len(games)}/{MAX_GAMES} games", inline=True)
        if snapshot.get("header"):
            embed.set_thumbnail(url=snapshot["header"])
        embed.set_footer(text=f"AppID {appid}")
        await send(embed=embed)

    @commands.hybrid_command(name="dremove", description="Remove a game from the watchlist")
    @is_owner_or_admin()
    async def dremove(self, ctx: commands.Context, *, query: str):
        """Remove a game from the watchlist."""
        await ctx.defer()
        games = await self.config.games()
        if not games:
            await ctx.send("📭 Watchlist is empty.")
            return

        matches = []
        for appid_str, info in games.items():
            if query.isdigit() and appid_str == query:
                matches = [(appid_str, info)]
                break
            elif query.lower() in info.get("name", "").lower():
                matches.append((appid_str, info))

        if not matches:
            await ctx.send(f"❌ No game matching `{query}` on the watchlist.")
            return

        if len(matches) == 1:
            appid_str, info = matches[0]
            del games[appid_str]
            await self.config.games.set(games)
            await ctx.send(f"🗑️ Removed **{info['name']}** from the watchlist.")
            return

        options = [
            discord.SelectOption(label=info["name"][:100], value=appid_str)
            for appid_str, info in matches[:25]
        ]
        select = discord.ui.Select(placeholder="Which game to remove?", options=options)

        async def cb(inter: discord.Interaction):
            chosen_id = select.values[0]
            current = await self.config.games()
            name = current.get(chosen_id, {}).get("name", chosen_id)
            current.pop(chosen_id, None)
            await self.config.games.set(current)
            await inter.response.send_message(f"🗑️ Removed **{name}** from the watchlist.")

        select.callback = cb
        view = discord.ui.View(timeout=60)
        view.add_item(select)
        await ctx.send("Multiple matches — choose one:", view=view)

    @commands.hybrid_command(name="dlist", description="Show all watched games and their status")
    async def dlist(self, ctx: commands.Context):
        """Show all watched games with their current Denuvo/build status."""
        await ctx.defer()
        games = await self.config.games()
        if not games:
            await ctx.send("📭 Watchlist is empty. Use `/dadd` to add games.")
            return

        ordered = sorted(games.items(), key=lambda x: x[1].get("name", "").lower())
        lines = []
        for appid_str, info in ordered:
            icon = "⚠️" if info.get("denuvo") else "✅"
            build = f" • build `{info['build_id']}`" if info.get("build_id") else ""
            lines.append(f"{icon} **{info.get('name', appid_str)}** `{appid_str}`{build}")

        embed = discord.Embed(
            title=f"🎮 Steam Watchlist ({len(games)}/{MAX_GAMES})",
            color=discord.Color.blurple(),
        )
        embed.description = "\n".join(lines)[:4000]
        embed.set_footer(text="⚠️ = has Denuvo   ✅ = no Denuvo")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="dcheck", description="Instantly check a game's current status")
    async def dcheck(self, ctx: commands.Context, *, query: str):
        """Instantly check any game's current Denuvo/build status."""
        await ctx.defer()
        games = await self.config.games()
        appid = await self._resolve_appid(query, games)
        if appid is None:
            await ctx.send(f"❌ Couldn't resolve `{query}` to a Steam game.")
            return

        snapshot = await self.get_game_snapshot(appid)
        if snapshot is None:
            await ctx.send(f"❌ Couldn't fetch data for AppID `{appid}`.")
            return

        in_watchlist = str(appid) in games
        embed = discord.Embed(
            title=f"🔍 {snapshot['name']}",
            url=f"https://store.steampowered.com/app/{appid}/",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Denuvo", value="⚠️ Yes" if snapshot["denuvo"] else "✅ No", inline=True)
        embed.add_field(name="Build ID", value=f"`{snapshot['build_id']}`" if snapshot["build_id"] else "Unknown", inline=True)
        embed.add_field(name="Watchlist", value="👁️ Watching" if in_watchlist else "➕ Use /dadd", inline=True)
        if snapshot.get("build_time"):
            embed.add_field(name="Build Pushed", value=f"<t:{snapshot['build_time']}:R>", inline=True)
        if snapshot.get("header"):
            embed.set_thumbnail(url=snapshot["header"])
        embed.set_footer(text=f"AppID {appid} • Checked now")
        embed.timestamp = datetime.now(timezone.utc)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="exeloc", description="List all .exe paths in the latest depot for a game")
    async def exeloc(self, ctx: commands.Context, *, query: str):
        """List every .exe path in the latest depot of a game.

        Pass a Steam AppID or a game name. Data comes from ManifestHub2.
        """
        await ctx.defer()
        games = await self.config.games()
        appid = await self._resolve_appid(query, games)
        if appid is None:
            await ctx.send(f"❌ Couldn't resolve `{query}` to a Steam game.")
            return

        name, exes = await self.get_exe_paths(appid)
        if exes is None:
            await ctx.send(
                f"❌ No depot data found for `{name or query}` (AppID `{appid}`) on ManifestHub2."
            )
            return
        if not exes:
            await ctx.send(
                f"⚠️ No `.exe` files found in the depot for **{name}** (AppID `{appid}`)."
            )
            return

        listing = "\n".join(exes)
        embed = discord.Embed(
            title=f"🗂️ Executables — {name}",
            url=f"https://store.steampowered.com/app/{appid}/",
            color=discord.Color.blurple(),
        )
        # Description cap is 4096; trim if necessary and note how many were cut.
        block = f"```\n{listing}\n```"
        if len(block) > 4096:
            shown = []
            running = len("```\n\n```")
            for line in exes:
                if running + len(line) + 1 > 3900:
                    break
                shown.append(line)
                running += len(line) + 1
            cut = len(exes) - len(shown)
            block = "```\n" + "\n".join(shown) + f"\n```\n…and {cut} more."
        embed.description = block
        embed.set_footer(text=f"AppID {appid} • {len(exes)} exe(s) • via ManifestHub2")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="dforcecheck", description="Manually trigger a full watchlist scan (admin only)")
    @is_owner_or_admin()
    async def dforcecheck(self, ctx: commands.Context):
        """Manually trigger a full watchlist scan now."""
        await ctx.send("🔄 Running full watchlist check now…")
        changes = await self.check_games_internal()
        if not changes:
            await ctx.send("✅ Check complete — no changes detected.")

    @commands.hybrid_command(name="dstatus", description="Show bot status and next scheduled check time")
    async def dstatus(self, ctx: commands.Context):
        """Show how many games are watched and when the next check runs."""
        games = await self.config.games()
        interval = await self.config.interval_minutes()
        channel_id = await self.config.notify_channel()
        mention = self._format_mention(await self.config.mention())
        next_iter = self.check_games.next_iteration
        next_str = f"<t:{int(next_iter.timestamp())}:R>" if next_iter else "Starting soon"

        embed = discord.Embed(title="🤖 DenuvoWatch", color=discord.Color.blurple())
        embed.add_field(name="Watching", value=f"{len(games)}/{MAX_GAMES} games", inline=True)
        embed.add_field(name="Check every", value=f"{interval} minutes", inline=True)
        embed.add_field(name="Next check", value=next_str, inline=True)
        embed.add_field(name="Mention", value=mention or "none", inline=True)
        embed.add_field(
            name="Alert channel",
            value=f"<#{channel_id}>" if channel_id else "❌ not set",
            inline=True,
        )
        await ctx.send(embed=embed)

    # ─── Config commands (owner only) ────────────────────────────────────────

    @commands.group(name="denuvowatch", aliases=["dwatch"])
    @is_owner_or_admin()
    async def denuvowatch(self, ctx: commands.Context):
        """DenuvoWatch configuration."""

    @denuvowatch.command(name="channel")
    async def dw_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where alerts are posted."""
        await self.config.notify_channel.set(channel.id)
        await ctx.send(f"✅ Alerts will be posted in {channel.mention}.")

    @denuvowatch.command(name="pinguser")
    async def dw_pinguser(self, ctx: commands.Context, user: discord.User = None):
        """Set (or clear) the user pinged on build updates. Omit to clear."""
        await self.config.notify_user.set(user.id if user else None)
        if user:
            await ctx.send(f"✅ Build updates will ping {user.mention}.")
        else:
            await ctx.send("✅ Build-update ping cleared.")

    @denuvowatch.command(name="mention")
    async def dw_mention(
        self,
        ctx: commands.Context,
        target: Optional[Union[discord.Member, discord.Role]] = None,
    ):
        """Set the user or role pinged on EVERY update (Denuvo + build).

        Run without an argument to clear the mention.
        """
        if target is None:
            await self.config.mention.set(None)
            await ctx.send("✅ Update mention cleared.")
            return
        mtype = "role" if isinstance(target, discord.Role) else "user"
        await self.config.mention.set({"type": mtype, "id": target.id})
        await ctx.send(
            f"✅ Updates will mention {target.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @denuvowatch.command(name="interval")
    async def dw_interval(self, ctx: commands.Context, minutes: int):
        """Set how often (in minutes) the watchlist is scanned."""
        if minutes < 5:
            await ctx.send("❌ Interval must be at least 5 minutes to respect Steam rate limits.")
            return
        await self.config.interval_minutes.set(minutes)
        self.check_games.change_interval(minutes=minutes)
        await ctx.send(f"✅ Check interval set to **{minutes}** minutes.")

    @denuvowatch.command(name="show")
    async def dw_show(self, ctx: commands.Context):
        """Show the current configuration."""
        channel_id = await self.config.notify_channel()
        user_id = await self.config.notify_user()
        mention = self._format_mention(await self.config.mention())
        interval = await self.config.interval_minutes()
        games = await self.config.games()
        embed = discord.Embed(title="⚙️ DenuvoWatch Config", color=discord.Color.blurple())
        embed.add_field(name="Alert channel", value=f"<#{channel_id}>" if channel_id else "❌ not set", inline=False)
        embed.add_field(name="Update mention", value=mention or "none", inline=False)
        embed.add_field(name="Build-ping user", value=f"<@{user_id}>" if user_id else "none", inline=False)
        embed.add_field(name="Interval", value=f"{interval} minutes", inline=False)
        embed.add_field(name="Watchlist size", value=f"{len(games)}/{MAX_GAMES}", inline=False)
        embed.add_field(name="Admins", value=str(len(await self.config.admins())), inline=False)
        await ctx.send(embed=embed)

    @denuvowatch.command(name="clear")
    async def dw_clear(self, ctx: commands.Context):
        """Clear the entire watchlist."""
        await self.config.games.set({})
        await ctx.send("🗑️ Watchlist cleared.")

    @denuvowatch.command(name="import")
    async def dw_import(self, ctx: commands.Context, url: str = None):
        """Import games into the watchlist from a JSON file or URL.

        Either attach a `steam_data.json`-style file, or pass a direct/raw
        JSON link, e.g. `[p]denuvowatch import https://.../steam_data.json`.
        Accepts `{"games": {appid: {...}}}` or a bare `{appid: {...}}` mapping.
        Existing entries are kept; new games are added up to the cap.
        """
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
                await ctx.send(
                    "❌ The source didn't return JSON (got HTML/other). "
                    "Use a **raw** JSON link, e.g. a Discord CDN attachment URL "
                    "or a `raw.githubusercontent.com` link — not a GitHub page link."
                )
                return
            payload = json.loads(text)
        except Exception as e:
            await ctx.send(f"❌ Couldn't parse the JSON: `{e}`")
            return

        incoming = payload.get("games", payload) if isinstance(payload, dict) else None
        if not isinstance(incoming, dict) or not incoming:
            await ctx.send("❌ No games found in the file.")
            return

        games = await self.config.games()
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
            games[appid_str] = {
                "name": info.get("name", f"AppID {appid_str}"),
                "denuvo": bool(info.get("denuvo", False)),
                "build_id": info.get("build_id"),
                "build_time": info.get("build_time"),
                "header": info.get("header", ""),
            }
            added += 1

        await self.config.games.set(games)

        lines = [f"✅ Imported **{added}** game(s). Watchlist now {len(games)}/{MAX_GAMES}."]
        if skipped_existing:
            lines.append(f"• Skipped {skipped_existing} already on the watchlist.")
        if skipped_full:
            lines.append(f"• Skipped {skipped_full} — watchlist full ({MAX_GAMES} cap).")
        if invalid:
            lines.append(f"• Ignored {invalid} invalid entr(y/ies).")
        await ctx.send("\n".join(lines))

    # ─── Admin management (bot owner only) ───────────────────────────────────

    @denuvowatch.command(name="addadmin")
    @commands.is_owner()
    async def dw_addadmin(self, ctx: commands.Context, user: discord.User):
        """(Owner) Grant a user access to all DenuvoWatch admin commands."""
        async with self.config.admins() as admins:
            if user.id in admins:
                await ctx.send(f"ℹ️ {user.mention} is already a DenuvoWatch admin.")
                return
            admins.append(user.id)
        await ctx.send(
            f"✅ {user.mention} can now use DenuvoWatch admin commands.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @denuvowatch.command(name="removeadmin", aliases=["deladmin"])
    @commands.is_owner()
    async def dw_removeadmin(self, ctx: commands.Context, user: discord.User):
        """(Owner) Revoke a user's DenuvoWatch admin access."""
        async with self.config.admins() as admins:
            if user.id not in admins:
                await ctx.send(f"ℹ️ {user.mention} is not a DenuvoWatch admin.")
                return
            admins.remove(user.id)
        await ctx.send(
            f"✅ Removed {user.mention} from DenuvoWatch admins.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @denuvowatch.command(name="admins", aliases=["listadmins"])
    @commands.is_owner()
    async def dw_admins(self, ctx: commands.Context):
        """(Owner) List users with DenuvoWatch admin access."""
        admins = await self.config.admins()
        if not admins:
            await ctx.send("No DenuvoWatch admins set. Only the bot owner has access.")
            return
        lines = []
        for uid in admins:
            user = self.bot.get_user(uid)
            lines.append(f"• {user} (`{uid}`)" if user else f"• `{uid}`")
        embed = discord.Embed(
            title="🛡️ DenuvoWatch Admins",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)
