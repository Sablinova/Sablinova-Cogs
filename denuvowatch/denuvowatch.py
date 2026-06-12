import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Union

import aiohttp
import discord
from discord import app_commands
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

# HubCapManifest — authenticated API; serves decrypted manifest bundles.
HUBCAP_BASE = "https://hubcapmanifest.com/api/v1"

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


def parse_manifest_records(blob: bytes, depot_key: Optional[bytes] = None):
    """Parse a raw Steam depot manifest blob into file records.

    Returns a list of dicts: {"path": str, "size": int, "sha": str}.
    Directories are skipped. FileMapping layout: filename=field 1, size=field 2,
    flags=field 3, sha_content=field 4.

    Some manifests store filenames AES-encrypted (base64 text). The encrypted
    flag isn't reliably present in the mirror, so when a `depot_key` is
    available and a name doesn't look like a real path, this attempts to
    decrypt it and uses the result if it looks valid.
    """
    import struct

    if len(blob) < 8:
        return []
    magic, length = struct.unpack_from("<II", blob, 0)
    if magic != MANIFEST_PAYLOAD_MAGIC:
        return []
    payload = blob[8:8 + length]

    records = []
    for fn, wt, v in _iter_protobuf_fields(payload):
        if fn != 1 or wt != 2:  # field 1 = repeated FileMapping
            continue
        raw_name = None
        size = 0
        flags = 0
        sha = b""
        for ffn, fwt, fv in _iter_protobuf_fields(v):
            if ffn == 1 and fwt == 2:
                raw_name = fv
            elif ffn == 2 and fwt == 0:
                size = fv
            elif ffn == 3 and fwt == 0:
                flags = fv
            elif ffn == 4 and fwt == 2:
                sha = fv
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
        records.append(
            {"path": name.replace("\\", "/"), "size": size, "sha": sha.hex()}
        )
    return records


def parse_manifest_files(blob: bytes, depot_key: Optional[bytes] = None):
    """Back-compat: list of (path, size) for exe extraction."""
    return [(r["path"], r["size"]) for r in parse_manifest_records(blob, depot_key)]

DEFAULT_GLOBALS = {
    "games": {},          # appid_str -> {name, denuvo, build_id, build_time, header}
    "notify_channel": None,
    "notify_user": None,   # legacy: pinged on build updates only
    "mention": None,       # {"type": "user"|"role", "id": int} pinged on ALL updates
    "interval_minutes": 10,
    "admins": [],          # user IDs allowed to use owner-gated commands
    "hubcap_key": None,    # legacy single key (migrated into hubcap_keys)
    "hubcap_keys": [],     # list of HubCapManifest API keys (round-robined)
    # appid_str -> {"exes": [...], "build_id": str|None, "source": str, "cached_at": int}
    "exe_cache": {},
    # appid_str -> {"build_id": str|None, "files": {path: sha}} for build diffs
    "file_snapshots": {},
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

    async def search_steam(self, query: str) -> list:
        try:
            async with self.session.get(
                "https://store.steampowered.com/api/storesearch/",
                params={"term": query, "cc": "us", "l": "en"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                payload = await r.json()
            return [
                {"appid": i["id"], "name": i["name"]}
                for i in payload.get("items", [])
            ]
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

    async def _exe_paths_manifesthub(self, appid: int):
        """Return (name, exe_paths) for an app via ManifestHub2, or (None, None)."""
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

    # ─── HubCapManifest (authenticated API) ──────────────────────────────────

    async def _get_hubcap_keys(self) -> list:
        """Return the configured HubCap keys, migrating the legacy single key."""
        keys = list(await self.config.hubcap_keys() or [])
        legacy = await self.config.hubcap_key()
        if legacy:
            if legacy not in keys:
                keys.append(legacy)
                await self.config.hubcap_keys.set(keys)
            await self.config.hubcap_key.set(None)
        return keys

    async def _pick_hubcap_key(self) -> Optional[str]:
        """Pick the key with the most daily quota left; skip exhausted ones.

        Prefers keys with a known positive remaining count (most first). Keys
        whose remaining is unknown (network error) are used only as a fallback
        when no key has known remaining. Returns None when every key is known
        to be exhausted.
        """
        keys = await self._get_hubcap_keys()
        if not keys:
            return None
        best_known = None       # (remaining, key) with remaining > 0
        unknown_fallback = None
        all_zero = True
        for k in keys:
            rem = await self._hubcap_remaining(k)
            if rem is None:
                if unknown_fallback is None:
                    unknown_fallback = k
                all_zero = False  # unknown isn't a confirmed zero
                continue
            if rem > 0:
                all_zero = False
                if best_known is None or rem > best_known[0]:
                    best_known = (rem, k)
        if best_known is not None:
            return best_known[1]
        if unknown_fallback is not None:
            return unknown_fallback
        # Every key returned a known 0 (all exhausted).
        return None

    async def _any_hubcap_key(self) -> Optional[str]:
        """First configured key (for free endpoints like search/status)."""
        keys = await self._get_hubcap_keys()
        return keys[0] if keys else None

    async def hubcap_status(self, appid: int, key: str) -> Optional[dict]:
        """Free status check — does HubCap have a manifest for this app?"""
        try:
            async with self.session.get(
                f"{HUBCAP_BASE}/status/{appid}",
                headers={"Authorization": f"Bearer {key}"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status != 200:
                    return None
                return await r.json(content_type=None)
        except Exception:
            log.exception("HubCap status failed for %s", appid)
            return None

    async def hubcap_search(self, query: str, key: str, limit: int = 20):
        """Free library search. Returns list of {'appid': int, 'name': str}."""
        try:
            async with self.session.get(
                f"{HUBCAP_BASE}/search",
                params={"q": query, "limit": limit},
                headers={"Authorization": f"Bearer {key}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return []
                data = await r.json(content_type=None)
            out = []
            for item in data.get("results", []):
                gid = item.get("game_id")
                name = item.get("game_name")
                if gid and name:
                    out.append({"appid": int(gid), "name": name})
            return out
        except Exception:
            return []

    async def _exe_paths_hubcap(self, appid: int, key: str):
        """Return (name, exe_paths) via HubCap manifest ZIP, or (None, None).

        Costs one daily download. Returns (None, None) when HubCap has no
        manifest or the request fails, so the caller can fall back.
        """
        import io
        import zipfile

        status = await self.hubcap_status(appid, key)
        if not status or status.get("status") != "available":
            return None, None
        name = status.get("game_name") or f"AppID {appid}"

        try:
            async with self.session.get(
                f"{HUBCAP_BASE}/manifest/{appid}",
                headers={"Authorization": f"Bearer {key}"},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                if r.status != 200:
                    log.warning("HubCap manifest %s -> HTTP %s", appid, r.status)
                    return None, None
                data = await r.read()
        except Exception:
            log.exception("HubCap manifest download failed for %s", appid)
            return None, None

        exes = []
        seen = set()
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if not info.filename.lower().endswith(".manifest"):
                        continue
                    blob = zf.read(info)
                    # HubCap serves decrypted manifests; no depot key needed.
                    for path, _size in parse_manifest_files(blob):
                        if path.lower().endswith(".exe") and path not in seen:
                            seen.add(path)
                            exes.append(path)
        except Exception:
            log.exception("HubCap manifest ZIP parse failed for %s", appid)
            return None, None

        exes.sort(key=str.lower)
        return name, exes

    async def _records_manifesthub(self, appid: int):
        """Return (name, [records]) of all files via ManifestHub2, or (None, None)."""
        metadata = await self.mh_fetch_metadata(appid)
        if metadata is None:
            return None, None
        name = metadata.get("name") or f"AppID {appid}"
        records = []
        for depot_id, gid, depot_key in self._public_depots(metadata):
            blob = await self.mh_fetch_manifest_blob(appid, depot_id, gid)
            if not blob:
                continue
            records.extend(parse_manifest_records(blob, depot_key))
        return name, records

    async def _records_hubcap(self, appid: int, key: str, force_update: bool = False):
        """Return (name, [records]) of all files via HubCap, or (None, None).

        When force_update is True, HubCap regenerates the manifest from Steam
        before serving (needed to get a freshly-pushed build's file list).
        """
        import io
        import zipfile

        status = await self.hubcap_status(appid, key)
        if not status or status.get("status") != "available":
            return None, None
        name = status.get("game_name") or f"AppID {appid}"
        # If HubCap knows it's behind, force a refresh regardless.
        if status.get("needs_update"):
            force_update = True
        params = {"force_update": "true"} if force_update else None
        try:
            async with self.session.get(
                f"{HUBCAP_BASE}/manifest/{appid}",
                params=params,
                headers={"Authorization": f"Bearer {key}"},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as r:
                if r.status != 200:
                    return None, None
                data = await r.read()
        except Exception:
            log.exception("HubCap manifest download failed for %s", appid)
            return None, None

        records = []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if not info.filename.lower().endswith(".manifest"):
                        continue
                    records.extend(parse_manifest_records(zf.read(info)))
        except Exception:
            log.exception("HubCap manifest ZIP parse failed for %s", appid)
            return None, None
        return name, records

    async def get_file_map(self, appid: int, allow_hubcap: bool = True):
        """Return (name, {path: {sha, size}}) of all files, free source first.

        Returns (name, None) when no source has data.
        """
        name, records = await self._records_manifesthub(appid)
        if not records and allow_hubcap:
            key = await self._pick_hubcap_key()
            if key:
                hc_name, hc_records = await self._records_hubcap(appid, key)
                if hc_records is not None:
                    name, records = hc_name, hc_records
        if records is None:
            return name, None
        return name, {r["path"]: {"sha": r["sha"], "size": r["size"]} for r in records}

    async def get_file_map_fresh(self, appid: int):
        """Like get_file_map but prioritises an up-to-date source.

        Used right after a build change: ManifestHub2 lags badly, so prefer
        HubCap with force_update. Falls back to ManifestHub2 only when no key.
        Returns (name, {path:{sha,size}}) or (name, None).
        """
        key = await self._pick_hubcap_key()
        if key:
            hc_name, hc_records = await self._records_hubcap(appid, key, force_update=True)
            if hc_records is not None:
                return hc_name, {
                    r["path"]: {"sha": r["sha"], "size": r["size"]} for r in hc_records
                }
        # No key (or HubCap failed): fall back to the free mirror.
        return await self.get_file_map(appid)

    @staticmethod
    def _entry_sha(entry):
        """Read sha from a snapshot entry (supports legacy sha-string format)."""
        if isinstance(entry, dict):
            return entry.get("sha")
        return entry  # legacy: value was the sha string itself

    @staticmethod
    def _entry_size(entry):
        """Read size from a snapshot entry, or None for legacy entries."""
        if isinstance(entry, dict):
            return entry.get("size")
        return None

    @classmethod
    def _diff_file_maps(cls, old: dict, new: dict):
        """Diff two file maps.

        Returns (added, removed, modified, size_delta) where:
          - added/removed: list of (path, size)
          - modified: list of (path, old_size, new_size)
          - size_delta: total byte change (new total - old total over changed files)
        Handles both new {path:{sha,size}} and legacy {path: sha} formats.
        """
        old_paths = set(old)
        new_paths = set(new)

        added = sorted(
            ((p, cls._entry_size(new[p])) for p in (new_paths - old_paths)),
            key=lambda t: t[0].lower(),
        )
        removed = sorted(
            ((p, cls._entry_size(old[p])) for p in (old_paths - new_paths)),
            key=lambda t: t[0].lower(),
        )
        modified = sorted(
            (
                (p, cls._entry_size(old[p]), cls._entry_size(new[p]))
                for p in (old_paths & new_paths)
                if cls._entry_sha(old[p]) != cls._entry_sha(new[p])
            ),
            key=lambda t: t[0].lower(),
        )

        size_delta = 0
        for _p, s in added:
            size_delta += s or 0
        for _p, s in removed:
            size_delta -= s or 0
        for _p, os, ns in modified:
            size_delta += (ns or 0) - (os or 0)

        return added, removed, modified, size_delta

    async def _fetch_exes_fresh(self, appid: int, allow_hubcap: bool = True):
        """Fetch exe paths live: ManifestHub2 (free) first, HubCap if empty.

        Returns (name, exe_paths, source). exe_paths is None when no source
        had data. Honours the free-first order you asked for.
        """
        # 1) Free source first.
        name, exes = await self._exe_paths_manifesthub(appid)
        if exes:
            return name, exes, "ManifestHub2"

        # 2) HubCap only if the free source had nothing useful.
        if allow_hubcap:
            key = await self._pick_hubcap_key()
            if key:
                hc_name, hc_exes = await self._exe_paths_hubcap(appid, key)
                if hc_exes is not None:
                    return hc_name, hc_exes, "HubCapManifest"

        # Free source existed but had zero exes -> report that (empty list).
        if exes is not None:
            return name, exes, "ManifestHub2"
        return name, None, None

    async def get_exe_paths(self, appid: int, current_build: Optional[str] = None):
        """Cache-first exe resolver.

        Serves from the cache when present and the build is unchanged. Otherwise
        fetches fresh (free source, then HubCap) and updates the cache, keyed by
        build_id so a new build forces a refresh.

        Returns (name, exe_paths, source). source may be 'cache',
        'HubCapManifest', 'ManifestHub2', or None.
        """
        appid_str = str(appid)
        cache = await self.config.exe_cache()
        entry = cache.get(appid_str)

        if entry is not None:
            # Use cache unless we know the build moved on.
            if current_build is None or entry.get("build_id") == current_build:
                return entry.get("name"), list(entry.get("exes", [])), "cache"

        name, exes, source = await self._fetch_exes_fresh(appid)
        if exes is not None:
            cache[appid_str] = {
                "name": name,
                "exes": exes,
                "build_id": current_build,
                "source": source,
                "cached_at": int(datetime.now(timezone.utc).timestamp()),
            }
            await self.config.exe_cache.set(cache)
            return name, exes, source

        # Fetch failed; fall back to a stale cache entry if we have one.
        if entry is not None:
            return entry.get("name"), list(entry.get("exes", [])), "cache (stale)"
        return name, None, None

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
    def _human_size(num, signed: bool = False) -> str:
        """Format a byte count like '349.29 MiB'. None -> '?'."""
        if num is None:
            return "?"
        sign = ""
        if signed:
            sign = "+" if num >= 0 else "-"
        n = abs(num)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if n < 1024 or unit == "TiB":
                if unit == "B":
                    return f"{sign}{int(n)} {unit}"
                return f"{sign}{n:.2f} {unit}"
            n /= 1024

    @classmethod
    def _format_diff_lines(cls, lines: list, max_items: int = 10, max_chars: int = 1000) -> str:
        """Pack pre-rendered lines into a code block, trimmed to fit."""
        if not lines:
            return "—"
        shown = []
        used = 0
        for line in lines[:max_items]:
            if used + len(line) + 1 > max_chars:
                break
            shown.append(line)
            used += len(line) + 1
        body = "\n".join(shown)
        remaining = len(lines) - len(shown)
        text = f"```\n{body}\n```"
        if remaining > 0:
            text += f"…and {remaining} more"
        return text

    @staticmethod
    def build_depot_embed(appid: int, old_build: str, new_build: str, new: dict, diff=None) -> discord.Embed:
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
            embed.add_field(name="Build Pushed", value=f"<t:{new['build_time']}:f>", inline=True)

        if diff is not None:
            added, removed, modified, size_delta = diff
            embed.add_field(
                name="Changes",
                value=(
                    f"🟢 {len(added)} added   "
                    f"🔴 {len(removed)} removed   "
                    f"🟡 {len(modified)} modified\n"
                    f"📦 Total size change: **{DenuvoWatch._human_size(size_delta, signed=True)}**"
                ),
                inline=False,
            )
            if modified:
                lines = [
                    f"{p}  ({DenuvoWatch._human_size(os)} → {DenuvoWatch._human_size(ns)}, "
                    f"{DenuvoWatch._human_size((ns or 0) - (os or 0), signed=True)})"
                    for p, os, ns in modified
                ]
                embed.add_field(
                    name=f"🟡 Modified ({len(modified)})",
                    value=DenuvoWatch._format_diff_lines(lines),
                    inline=False,
                )
            if added:
                lines = [f"{p}  ({DenuvoWatch._human_size(s)})" for p, s in added]
                embed.add_field(
                    name=f"🟢 Added ({len(added)})",
                    value=DenuvoWatch._format_diff_lines(lines),
                    inline=False,
                )
            if removed:
                lines = [f"{p}  ({DenuvoWatch._human_size(s)})" for p, s in removed]
                embed.add_field(
                    name=f"🔴 Removed ({len(removed)})",
                    value=DenuvoWatch._format_diff_lines(lines),
                    inline=False,
                )

        if new.get("header"):
            embed.set_thumbnail(url=new["header"])
        embed.set_footer(text=f"AppID {appid} • DenuvoWatch")
        embed.timestamp = datetime.now(timezone.utc)
        return embed

    # ─── Background check ────────────────────────────────────────────────────

    @staticmethod
    def _format_mention(mention) -> Optional[str]:
        """Turn a stored mention into a pingable string, or None.

        Accepts a {'type','id'} dict or a bare user-ID int (legacy notify_user).
        """
        if not mention:
            return None
        if isinstance(mention, int):  # legacy: bare user id
            return f"<@{mention}>"
        if not mention.get("id"):
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

                notify_user = self._format_mention(await self.config.notify_user())
                mention = self._format_mention(await self.config.mention())
                allowed = discord.AllowedMentions(users=True, roles=True)
                snapshots = await self.config.file_snapshots()
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
                        # Compute a depot file diff vs. the stored snapshot.
                        diff = None
                        new_map = None
                        try:
                            _name, new_map = await self.get_file_map_fresh(appid)
                        except Exception:
                            log.exception("file map fetch failed for %s", appid_str)
                        snap = snapshots.get(appid_str)
                        if new_map is not None and snap and snap.get("files"):
                            diff = self._diff_file_maps(snap["files"], new_map)

                        pings = [p for p in (mention, notify_user) if p]
                        content = " ".join(pings) if pings else None
                        await channel.send(
                            content=content,
                            embed=self.build_depot_embed(appid, old_build, new_build, new, diff),
                            allowed_mentions=allowed,
                        )
                        changes = True

                        # Save the new snapshot for the next diff.
                        if new_map is not None:
                            snapshots[appid_str] = {"build_id": new_build, "files": new_map}

                    games[appid_str] = {
                        "name": new["name"],
                        "denuvo": new["denuvo"],
                        "build_id": new["build_id"],
                        "build_time": new.get("build_time"),
                        "header": new.get("header", old.get("header", "")),
                    }

                await self.config.games.set(games)
                await self.config.file_snapshots.set(snapshots)
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
            raw_candidates = (await self.search_steam(query))[:10]
            if not raw_candidates:
                await ctx.send("❌ No results found on Steam.")
                return

            if len(raw_candidates) == 1:
                candidates = raw_candidates
            else:
                candidates = []
                for c in raw_candidates:
                    details = await self.fetch_app_details(c["appid"])
                    if details.get("type") == "game":
                        candidates.append(c)
                    if len(candidates) >= 5:
                        break
                if not candidates:
                    await ctx.send("❌ No games found (all results were DLC/other).")
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
            embed.add_field(name="Build Pushed", value=f"<t:{snapshot['build_time']}:f>", inline=True)
        if snapshot.get("header"):
            embed.set_thumbnail(url=snapshot["header"])
        embed.set_footer(text=f"AppID {appid} • Checked now")
        embed.timestamp = datetime.now(timezone.utc)
        await ctx.send(embed=embed)

    async def _exeloc_autocomplete(self, interaction: discord.Interaction, current: str):
        """Suggest games: watchlist + cached first, then HubCap library search."""
        current = (current or "").strip()
        cur_low = current.lower()
        choices = []
        seen = set()

        def add(appid_str, name, tag):
            if appid_str in seen:
                return
            seen.add(appid_str)
            label = f"{name} [{tag}]"[:100]
            choices.append(app_commands.Choice(name=label, value=str(appid_str)))

        try:
            games = await self.config.games()
            cache = await self.config.exe_cache()
        except Exception:
            games, cache = {}, {}

        # 1) Watchlist matches first.
        for appid_str, info in games.items():
            if len(choices) >= 25:
                break
            name = info.get("name", appid_str)
            if not cur_low or cur_low in name.lower() or cur_low in appid_str:
                add(appid_str, name, "watchlist")

        # 2) Cached (not already shown).
        for appid_str, entry in cache.items():
            if len(choices) >= 25:
                break
            name = entry.get("name", appid_str)
            if not cur_low or cur_low in str(name).lower() or cur_low in appid_str:
                add(appid_str, name, "cached")

        # 3) HubCap library search for broader matches (free; needs 3+ chars).
        if len(choices) < 25 and len(current) >= 3:
            try:
                key = await self._any_hubcap_key()
                if key:
                    for item in await self.hubcap_search(current, key, limit=25):
                        if len(choices) >= 25:
                            break
                        add(str(item["appid"]), item["name"], "library")
            except Exception:
                pass

        return choices[:25]

    @commands.hybrid_command(name="exeloc", description="List all .exe paths in the latest depot for a game")
    @app_commands.describe(query="Pick a watched/cached game, or type a name or AppID")
    @app_commands.autocomplete(query=_exeloc_autocomplete)
    async def exeloc(self, ctx: commands.Context, *, query: str):
        """List every .exe path in a game's depot.

        Pass a Steam AppID or a game name. Served from cache when the build is
        unchanged; otherwise fetched (free source first, then HubCap).
        """
        await ctx.defer()
        games = await self.config.games()
        appid = await self._resolve_appid(query, games)
        if appid is None:
            await ctx.send(f"❌ Couldn't resolve `{query}` to a Steam game.")
            return

        # Determine current build for cache freshness: use stored value for a
        # watched game, else fetch it live.
        if str(appid) in games:
            current_build = games[str(appid)].get("build_id")
        else:
            current_build, _ = await self.fetch_build_id(appid)

        name, exes, _source = await self.get_exe_paths(appid, current_build)
        if exes is None:
            await ctx.send(
                f"❌ No depot data found for `{name or query}` (AppID `{appid}`)."
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
        embed.set_footer(text=f"AppID {appid} • {len(exes)} exe(s)")
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
    async def dw_pinguser(
        self,
        ctx: commands.Context,
        target: Optional[Union[discord.Member, discord.Role]] = None,
    ):
        """Set (or clear) the user/role pinged on build updates only.

        Run without an argument to clear it.
        """
        if target is None:
            await self.config.notify_user.set(None)
            await ctx.send("✅ Build-update ping cleared.")
            return
        mtype = "role" if isinstance(target, discord.Role) else "user"
        await self.config.notify_user.set({"type": mtype, "id": target.id})
        await ctx.send(
            f"✅ Build updates will ping {target.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

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
        pinguser = self._format_mention(await self.config.notify_user())
        mention = self._format_mention(await self.config.mention())
        interval = await self.config.interval_minutes()
        games = await self.config.games()
        embed = discord.Embed(title="⚙️ DenuvoWatch Config", color=discord.Color.blurple())
        embed.add_field(name="Alert channel", value=f"<#{channel_id}>" if channel_id else "❌ not set", inline=False)
        embed.add_field(name="Update mention", value=mention or "none", inline=False)
        embed.add_field(name="Build-ping (build only)", value=pinguser or "none", inline=False)
        embed.add_field(name="Interval", value=f"{interval} minutes", inline=False)
        embed.add_field(name="Watchlist size", value=f"{len(games)}/{MAX_GAMES}", inline=False)
        embed.add_field(name="Admins", value=str(len(await self.config.admins())), inline=False)
        await ctx.send(embed=embed)

    @denuvowatch.command(name="clear")
    async def dw_clear(self, ctx: commands.Context):
        """Clear the entire watchlist."""
        await self.config.games.set({})
        await ctx.send("🗑️ Watchlist cleared.")

    async def _hubcap_remaining(self, key: str) -> Optional[int]:
        """Return remaining HubCap daily downloads.

        0 when the daily limit is reached (HTTP 429), None when truly unknown
        (network/other error).
        """
        try:
            async with self.session.get(
                f"{HUBCAP_BASE}/user/stats",
                headers={"Authorization": f"Bearer {key}"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status == 429:
                    return 0  # daily limit reached
                if r.status != 200:
                    return None
                info = await r.json(content_type=None)
            used = info.get("daily_usage")
            limit = info.get("daily_limit")
            if used is None or limit is None:
                return None
            return max(0, int(limit) - int(used))
        except Exception:
            return None

    @denuvowatch.command(name="cacheall")
    async def dw_cacheall(self, ctx: commands.Context, mode: str = ""):
        """Cache .exe paths and file snapshots for the whole watchlist.

        Modes:
          (none)  — free source first, HubCap only if free has nothing; skips
                    games whose build hasn't changed since last cache.
          force   — re-cache every game (ignore the unchanged-build skip).
          fresh   — pull every game fresh from HubCap with force_update so the
                    baseline matches the current build exactly. Most accurate
                    for future build diffs, but uses one HubCap download per
                    game (watch your daily quota).

        Usage: `[p]denuvowatch cacheall`, `... cacheall force`, `... cacheall fresh`
        """
        mode = (mode or "").strip().lower()
        force = mode in ("force", "fresh", "true")
        fresh = mode == "fresh"
        games = await self.config.games()
        if not games:
            await ctx.send("📭 Watchlist is empty.")
            return

        keys = await self._get_hubcap_keys()
        cache = await self.config.exe_cache()

        cached_fresh = 0
        skipped_unchanged = 0
        used_hubcap = 0
        hubcap_skipped_quota = 0
        no_data = 0

        snapshots = await self.config.file_snapshots()
        status = await ctx.send(f"🔄 Caching {len(games)} game(s)…")

        for i, (appid_str, info) in enumerate(games.items(), 1):
            appid = int(appid_str)
            current_build = info.get("build_id")
            entry = cache.get(appid_str)

            # Skip if already cached at this build and not forced.
            if (
                not force
                and entry is not None
                and entry.get("build_id") == current_build
                and entry.get("exes") is not None
                and snapshots.get(appid_str, {}).get("build_id") == current_build
            ):
                skipped_unchanged += 1
                continue

            name = None
            records = None
            source = None

            if fresh and keys:
                # Always pull fresh from HubCap (accurate baseline).
                key = await self._pick_hubcap_key()
                if key:
                    hc_name, hc_records = await self._records_hubcap(
                        appid, key, force_update=True
                    )
                    if hc_records is not None:
                        name, records, source = hc_name, hc_records, "HubCapManifest"
                        used_hubcap += 1
                else:
                    hubcap_skipped_quota += 1
            else:
                # Free source first.
                name, records = await self._records_manifesthub(appid)
                source = "ManifestHub2" if records else None

                # HubCap fallback only when free had nothing, key present, quota left.
                if not records and keys:
                    key = await self._pick_hubcap_key()
                    if key:
                        hc_name, hc_records = await self._records_hubcap(appid, key)
                        if hc_records is not None:
                            name, records, source = hc_name, hc_records, "HubCapManifest"
                            used_hubcap += 1
                    else:
                        hubcap_skipped_quota += 1

            if records is not None and source:
                exes = sorted(
                    {r["path"] for r in records if r["path"].lower().endswith(".exe")},
                    key=str.lower,
                )
                cache[appid_str] = {
                    "name": name or info.get("name"),
                    "exes": exes,
                    "build_id": current_build,
                    "source": source,
                    "cached_at": int(datetime.now(timezone.utc).timestamp()),
                }
                # Seed/update the file snapshot used for future build diffs.
                snapshots[appid_str] = {
                    "build_id": current_build,
                    "files": {r["path"]: {"sha": r["sha"], "size": r["size"]} for r in records},
                }
                cached_fresh += 1
            elif entry is None:
                no_data += 1

            if i % 5 == 0:
                try:
                    await status.edit(content=f"🔄 Caching… {i}/{len(games)} processed")
                except Exception:
                    pass

        await self.config.exe_cache.set(cache)
        await self.config.file_snapshots.set(snapshots)

        lines = [
            "✅ **Cache complete.**",
            f"• Cached/updated: {cached_fresh}",
            f"• Skipped (build unchanged): {skipped_unchanged}",
            f"• HubCap downloads used: {used_hubcap}",
        ]
        if hubcap_skipped_quota:
            lines.append(f"• ⚠️ Skipped (all HubCap keys exhausted): {hubcap_skipped_quota} — kept existing cache")
        if no_data:
            lines.append(f"• No depot data anywhere: {no_data}")
        # Total remaining across all keys.
        total_remaining = 0
        unknown = False
        for k in keys:
            rem = await self._hubcap_remaining(k)
            if rem is None:
                unknown = True
            else:
                total_remaining += rem
        if keys:
            suffix = "+" if unknown else ""
            lines.append(f"• HubCap remaining today: {total_remaining}{suffix} across {len(keys)} key(s)")
        await status.edit(content="\n".join(lines))

    @denuvowatch.command(name="difftest")
    async def dw_difftest(self, ctx: commands.Context, *, query: str):
        """Preview a Build Updated embed with a SIMULATED depot diff.

        Fetches a game's current files, fakes a "previous" snapshot by changing
        a few of them, and posts the real Build Updated embed so you can see how
        the file diff (added/removed/modified + sizes) renders. Does not change
        any stored data.
        """
        async with ctx.typing():
            games = await self.config.games()
            appid = await self._resolve_appid(query, games)
            if appid is None:
                await ctx.send(f"❌ Couldn't resolve `{query}` to a Steam game.")
                return
            name, new_map = await self.get_file_map(appid)
        if not new_map:
            await ctx.send(f"❌ No depot data found for `{name or query}` (AppID `{appid}`).")
            return

        # Build a fake "old" snapshot from the current map:
        #  - drop a couple files  -> they appear ADDED in new
        #  - keep some only in old -> appear REMOVED
        #  - change a hash+size   -> appear MODIFIED
        paths = list(new_map.keys())
        old_map = dict(new_map)

        added_preview = paths[:2]              # present in new, remove from old
        for p in added_preview:
            old_map.pop(p, None)

        # Fake removed: invent old-only entries.
        for i in range(2):
            old_map[f"_removed_sample_{i}/old_file_{i}.bin"] = {
                "sha": "f" * 40,
                "size": 5_000_000 * (i + 1),
            }

        # Fake modified: change sha + size of a couple existing files.
        modified_preview = [p for p in paths[2:4]]
        for p in modified_preview:
            cur = new_map[p]
            cur_size = cur.get("size", 0) if isinstance(cur, dict) else 0
            old_map[p] = {"sha": "0" * 40, "size": max(0, cur_size - 7_000_000)}

        diff = self._diff_file_maps(old_map, new_map)
        new_info = {
            "name": name,
            "build_time": int(datetime.now(timezone.utc).timestamp()),
            "header": "",
        }
        embed = self.build_depot_embed(appid, "OLD_TEST", "NEW_TEST", new_info, diff)
        await ctx.send(content="🧪 **Simulated diff preview** (no data changed):", embed=embed)

    @denuvowatch.command(name="cacheclear")
    async def dw_cacheclear(self, ctx: commands.Context):
        """Clear the cached exe-path data and file snapshots for all games."""
        await self.config.exe_cache.set({})
        await self.config.file_snapshots.set({})
        await ctx.send("🗑️ Exe-path cache and file snapshots cleared.")

    @denuvowatch.command(name="cachestatus")
    async def dw_cachestatus(self, ctx: commands.Context):
        """Show how many games have cached exe data."""
        cache = await self.config.exe_cache()
        games = await self.config.games()
        if not cache:
            await ctx.send("Cache is empty. Run `[p]denuvowatch cacheall`.")
            return
        stale = sum(
            1
            for a, e in cache.items()
            if a in games and e.get("build_id") != games[a].get("build_id")
        )
        embed = discord.Embed(title="🗂️ Exe Cache", color=discord.Color.blurple())
        embed.add_field(name="Cached games", value=str(len(cache)), inline=True)
        embed.add_field(name="Watchlist", value=str(len(games)), inline=True)
        embed.add_field(name="Stale (build moved)", value=str(stale), inline=True)
        await ctx.send(embed=embed)

    async def _validate_hubcap_key(self, key: str):
        """Validate a key against the free stats endpoint. Returns info or None."""
        try:
            async with self.session.get(
                f"{HUBCAP_BASE}/user/stats",
                headers={"Authorization": f"Bearer {key}"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                if r.status != 200:
                    return None
                return await r.json(content_type=None)
        except Exception:
            return None

    @staticmethod
    def _mask_key(key: str) -> str:
        if len(key) <= 12:
            return key[:4] + "…"
        return f"{key[:8]}…{key[-4:]}"

    @denuvowatch.group(name="hubcapkey", invoke_without_command=True)
    @commands.is_owner()
    async def dw_hubcapkey(self, ctx: commands.Context):
        """(Owner) Manage HubCapManifest API keys (round-robined for quota).

        Subcommands: add, remove, list, clear.
        """
        await ctx.send_help(ctx.command)

    @dw_hubcapkey.command(name="add")
    async def dw_hubcapkey_add(self, ctx: commands.Context, key: str):
        """Add a HubCap API key. DM the bot to avoid exposing it."""
        if ctx.guild is not None:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        info = await self._validate_hubcap_key(key)
        if info is None:
            await ctx.send("❌ That key was rejected by HubCapManifest. Not saved.")
            return
        keys = await self._get_hubcap_keys()
        if key in keys:
            await ctx.send("ℹ️ That key is already saved.")
            return
        keys.append(key)
        await self.config.hubcap_keys.set(keys)
        used = info.get("daily_usage")
        limit = info.get("daily_limit")
        extra = f" (usage {used}/{limit})" if limit is not None else ""
        await ctx.send(
            f"✅ Key added{extra}. Now using **{len(keys)}** key(s) for `/exeloc`."
        )

    @dw_hubcapkey.command(name="remove", aliases=["rm", "del"])
    async def dw_hubcapkey_remove(self, ctx: commands.Context, key_or_index: str):
        """Remove a key by its list number (see `list`) or full value."""
        if ctx.guild is not None:
            try:
                await ctx.message.delete()
            except Exception:
                pass
        keys = await self._get_hubcap_keys()
        if not keys:
            await ctx.send("No keys configured.")
            return
        removed = None
        if key_or_index.isdigit():
            idx = int(key_or_index) - 1
            if 0 <= idx < len(keys):
                removed = keys.pop(idx)
        elif key_or_index in keys:
            keys.remove(key_or_index)
            removed = key_or_index
        if removed is None:
            await ctx.send("❌ No matching key (use the number from `hubcapkey list`).")
            return
        await self.config.hubcap_keys.set(keys)
        await ctx.send(f"🗑️ Removed key `{self._mask_key(removed)}`. {len(keys)} left.")

    @dw_hubcapkey.command(name="list", aliases=["ls"])
    async def dw_hubcapkey_list(self, ctx: commands.Context):
        """List configured keys (masked) with remaining quota."""
        keys = await self._get_hubcap_keys()
        if not keys:
            await ctx.send("No HubCap keys configured. Add one with `hubcapkey add <key>`.")
            return
        lines = []
        total = 0
        unknown = False
        for i, k in enumerate(keys, 1):
            info = await self._validate_hubcap_key(k)
            if info is None:
                lines.append(f"{i}. `{self._mask_key(k)}` — ❌ invalid/unreachable")
                continue
            used = info.get("daily_usage", 0)
            limit = info.get("daily_limit", 0)
            rem = max(0, (limit or 0) - (used or 0))
            total += rem
            lines.append(f"{i}. `{self._mask_key(k)}` — {rem} left ({used}/{limit})")
        embed = discord.Embed(
            title="🔑 HubCap Keys",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Total remaining today: {total} across {len(keys)} key(s)")
        await ctx.send(embed=embed)

    @dw_hubcapkey.command(name="clear")
    async def dw_hubcapkey_clear(self, ctx: commands.Context):
        """Remove all HubCap keys."""
        await self.config.hubcap_keys.set([])
        await self.config.hubcap_key.set(None)
        await ctx.send("✅ All HubCap keys cleared. `/exeloc` will use ManifestHub2 only.")

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
