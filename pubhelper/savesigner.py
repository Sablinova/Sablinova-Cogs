"""
SaveSigner - MandarinJuice integration for save file signing.

Handles bruteforce and re-signing operations for RE Engine saves.
"""

import asyncio
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from discord import app_commands
import discord
import aiohttp
import py7zr

log = logging.getLogger("red.sablinova.pubhelper")

_ANONDROP_CHUNK_SIZE = 9 * 1024 * 1024  # 9 MB

LIN_PATH = "/home/user/.local/share/crucible-launcher/Prefix/pragmataprefix/drive_c/users/steamuser/AppData/Roaming/GSE Saves/{steam_id}/remote/win64_save/"

# Instructions for the save
SAVE_INSTRUCTIONS = """```\nSave File Instructions\n\n1. Press Win + R, paste the path below and hit Enter:\n%USERPROFILE%\\AppData\\Roaming\\GSE Saves\\{steam_id}\\remote\\win64_save\\\n\n1.1 Linux Path: {LIN_PATH}\n\n2. Send a .zip / .7z of the win64_save folder\n\n3. Send configs.user.ini — it can be found inside {config_folder}steam_settings\n```"""

# MandarinJuice save signing profiles
SAVE_PROFILES = {
    "resident evil 9 requiem": {
        "name": "RE9",
        "profile": "Resident Evil 9 Requiem v1.bin",
        "steam_id": "3764200",
        "config_folder": "pub_re9/",
    },
    "dragon's dogma 2": {
        "name": "DD2",
        "profile": "Dragon's Dogma 2 v1.bin",
        "steam_id": "2054970",
        "config_folder": "",
    },
    "monster hunter wilds": {
        "name": "MHWilds",
        "profile": "Monster Hunter Wilds v1.bin",
        "steam_id": "2246340",
        "config_folder": "",
    },
    "kunitsu-gami path of the goddess": {
        "name": "Kunitsu",
        "profile": "Kunitsu-Gami Path of the Goddess v1.bin",
        "steam_id": "2510720",
        "config_folder": "",
    },
    "dead rising deluxe remaster": {
        "name": "DeadRising",
        "profile": "Dead Rising Deluxe Remaster v1.bin",
        "steam_id": "2531360",
        "config_folder": "",
    },
    "monster hunter stories 3 twisted reflection": {
        "name": "MHStories3",
        "profile": "Monster Hunter Stories 3 Twisted Reflection v1.bin",
        "steam_id": "2498260",  # Fallback value, real ID wasn't originally included
        "config_folder": "pub_mhs3/",
    },
    "mega man star force legacy collection": {
        "name": "MegaMan",
        "profile": "Mega Man Star Force Legacy Collection v1.bin",
        "steam_id": "2816910",  # Fallback value, real ID wasn't originally included
        "config_folder": "",
    },
    "pragmata": {
        "name": "Pragmata",
        "profile": "PRAGMATA_v1.bin",
        "steam_id": "3357650",
        "config_folder": "pub_pragmata/",
    },
}


SAVE_INSTRUCTIONS_SEGA = """```
{game_name} – Save Transfer Guide

Save folder:
\%AppData%\Roaming\SEGA\{game_folder}\Steam\

• Go to the folder above
• Find your old SteamID folder (e.g., 76561198012345678)
• Copy everything inside it
• Paste into the new SteamID folder (created after launching and saving the game once)

Done – launch the game and your save should load
```"""

SEGA_PROFILES = {
    "persona 3 reload": {
        "name": "P3R",
        "game_folder": "P3R",
    },
    "persona 3 portable": {
        "name": "P3P",
        "game_folder": "P3P",
    },
    "persona 5 strikers": {
        "name": "P5S",
        "game_folder": "Persona 5 Strikers",
    },
    "persona 5 tactica": {
        "name": "P5T",
        "game_folder": "P5T",
    },
    "persona 4 arena ultimax": {
        "name": "P4AU",
        "game_folder": "P4AU",
    },
    "like a dragon: infinite wealth": {
        "name": "IW",
        "game_folder": "YakuzaLikeADragon8",
    },
    "like a dragon gaiden: the man who erased his name": {
        "name": "Gaiden",
        "game_folder": "LikeADragonGaiden",
    },
    "yakuza kiwami 3 & dark ties": {
        "name": "K3",
        "game_folder": "YakuzaKiwami3",
    },
    "like a dragon: pirate yakuza in hawaii": {
        "name": "Pirate",
        "game_folder": "LikeADragonPirateYakuza",
    },
    "sonic x shadow generations": {
        "name": "SSG",
        "game_folder": "SonicXShadowGenerations",
    },
    "metaphor: refantazio": {
        "name": "Metaphor",
        "game_folder": "Metaphor"
    },
}

class SaveSigner:
    """Handles MandarinJuice CLI interactions for save signing."""

    def __init__(self, data_path: Path):
        """Initialize SaveSigner with cog data path."""
        self.data_path = data_path
        self.tools_path = data_path / "tools"
        # Support both the old name and the new ProMax name
        self.cli_path_old = self.tools_path / "mandarin-juice-cli"
        self.cli_path_new = self.tools_path / "mandarin-juice-promax"
        self.profiles_path = self.tools_path / "profiles"

    def get_tool_path(self) -> Path | None:
        """Get path to MandarinJuice CLI. Returns None if not installed."""
        if self.cli_path_new.exists() and self.cli_path_new.is_file():
            return self.cli_path_new
        if self.cli_path_old.exists() and self.cli_path_old.is_file():
            return self.cli_path_old
        return None

    def get_profile_path(self, game: str) -> Path | None:
        """Get path to game profile file. Returns None if not found."""
        if game not in SAVE_PROFILES:
            return None

        profile_filename = SAVE_PROFILES[game]["profile"]
        profile_path = self.profiles_path / profile_filename

        if profile_path.exists():
            return profile_path
        return None

    def is_tool_installed(self) -> bool:
        """Check if MandarinJuice CLI is installed."""
        return self.get_tool_path() is not None

    def get_available_profiles(self) -> list[str]:
        """Get list of installed game profiles."""
        available = []
        for game_id in SAVE_PROFILES:
            if self.get_profile_path(game_id):
                available.append(game_id)
        return available

    async def run_bruteforce(
        self,
        game: str,
        save_archive: bytes,
        known_ids: list[str] | None = None,
        progress_callback=None,
    ) -> dict | None:
        """
        Run bruteforce to find User ID.

        Args:
            game: Game profile ID (e.g., "re9")
            save_archive: Archive file contents (zip or 7z)
            known_ids: Optional list of known save IDs to test first
            progress_callback: Optional async function to call with stdout lines

        Returns:
            dict with "user_id" (str) and "time" (float), or None if failed
        """
        tool_path = self.get_tool_path()
        profile_path = self.get_profile_path(game)

        if not tool_path or not profile_path:
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            extract_dir = tmpdir_path / "extracted"
            input_dir = tmpdir_path / "input"
            extract_dir.mkdir()
            input_dir.mkdir()

            # Extract archive
            archive_path = tmpdir_path / "archive"
            archive_path.write_bytes(save_archive)

            try:
                # Try 7z first
                with py7zr.SevenZipFile(archive_path, "r") as archive:
                    archive.extractall(extract_dir)
            except Exception:
                # Fall back to zip
                try:
                    with zipfile.ZipFile(archive_path, "r") as archive:
                        archive.extractall(extract_dir)
                except Exception:
                    raise ValueError("Unsupported format")

            # Find the best .bin file to bruteforce:
            # Priority 1: slot files (e.g. 001Slot.bin, SaveSlot.bin)
            # Priority 2: known data files (data000.bin, data001.bin, data00-1.bin)
            # Priority 3: any other .bin file
            data_path = None
            data_fallback = None  # data000/001/00-1.bin
            any_fallback = None  # any other .bin

            _DATA_NAMES = {"data000.bin", "data001.bin", "data00-1.bin"}

            # Sort files by size (smallest first)
            bin_files = sorted(
                extract_dir.rglob("*.bin"), key=lambda p: p.stat().st_size
            )

            for file_path in bin_files:
                name_lower = file_path.name.lower()

                # Priority 1: slot files
                if name_lower.endswith("slot.bin"):
                    data_path = file_path
                    break

                # Priority 2: known data files
                if name_lower in _DATA_NAMES and data_fallback is None:
                    data_fallback = file_path
                    continue

                # Priority 3: any other .bin
                if any_fallback is None:
                    any_fallback = file_path

            if not data_path:
                data_path = data_fallback or any_fallback

            if not data_path:
                return None

            # Copy the first save file to input directory
            shutil.copy(data_path, input_dir / data_path.name)

            # Run bruteforce
            cmd = [
                str(tool_path),
                "-m",
                "b",
                "-g",
                str(profile_path),
                "-p",
                str(input_dir),
            ]

            if known_ids:
                cmd.extend(["-u", ",".join(known_ids)])

            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

                user_id = None
                buf = b""

                # Read output in chunks to handle \r progress bars correctly
                if proc.stdout is None:
                    return None

                while True:
                    chunk = await proc.stdout.read(1024)
                    if not chunk:
                        break
                    buf += chunk

                    while True:
                        n_idx = buf.find(b"\n")
                        r_idx = buf.find(b"\r")

                        if n_idx != -1 and r_idx != -1:
                            idx = min(n_idx, r_idx)
                        else:
                            idx = max(n_idx, r_idx)

                        if idx == -1:
                            break

                        line_str = buf[:idx].decode("utf-8", errors="ignore").strip()
                        buf = buf[idx + 1 :]

                        if line_str and progress_callback:
                            await progress_callback(line_str)

                        # Parse output for "Found UserID: XXXXX"
                        match = re.search(r"Found UserID:\s*(\d+)", line_str)
                        if match:
                            user_id = match.group(1)

                if buf:
                    line_str = buf.decode("utf-8", errors="ignore").strip()
                    if line_str and progress_callback:
                        await progress_callback(line_str)
                    match = re.search(r"Found UserID:\s*(\d+)", line_str)
                    if match:
                        user_id = match.group(1)

                await proc.wait()

                if user_id:
                    return {"user_id": user_id}

            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                raise
            except Exception as e:
                if progress_callback:
                    await progress_callback(f"Exception running tool: {e}")
            finally:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()

        return None

    async def run_resign(
        self, game: str, save_archive: bytes, old_id: str, new_id: str
    ) -> bytes | None:
        """
        Run re-sign operation.

        Args:
            game: Game profile ID (e.g., "re9")
            save_archive: Archive file contents (zip or 7z)
            old_id: Original User ID
            new_id: New User ID to sign to

        Returns:
            Zip file bytes containing re-signed saves, or None if failed
        """
        tool_path = self.get_tool_path()
        profile_path = self.get_profile_path(game)

        if not tool_path or not profile_path:
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            extract_dir = tmpdir_path / "extracted"
            input_dir = tmpdir_path / "input"
            extract_dir.mkdir()
            input_dir.mkdir()

            # Extract archive
            archive_path = tmpdir_path / "archive"
            archive_path.write_bytes(save_archive)

            try:
                # Try 7z first
                with py7zr.SevenZipFile(archive_path, "r") as archive:
                    archive.extractall(extract_dir)
            except Exception:
                # Fall back to zip
                try:
                    with zipfile.ZipFile(archive_path, "r") as archive:
                        archive.extractall(extract_dir)
                except Exception:
                    raise ValueError("Unsupported format")

            # Copy all .bin save files to input directory
            for file_path in extract_dir.rglob("*.bin"):
                shutil.copy(file_path, input_dir / file_path.name)

            # Check if we have any save files
            save_files = list(input_dir.glob("*.bin"))
            if not save_files:
                return None

            # Run re-sign
            cmd = [
                str(tool_path),
                "-m",
                "r",
                "-g",
                str(profile_path),
                "-p",
                str(input_dir),
                "-uI",
                old_id,
                "-uO",
                new_id,
            ]

            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

                stdout, _ = await proc.communicate()
            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                raise
            except Exception as e:
                return None
            finally:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()

            # The output goes to the current working directory's _OUTPUT folder
            # Since MandarinJuice puts it relative to its own binary, we need to locate it
            output_base = tool_path.parent / "_OUTPUT"
            if not output_base.exists():
                return None

            # Find the most recent resigned directory
            resigned_dirs = sorted(output_base.glob("*_resigned"), reverse=True)
            if not resigned_dirs:
                return None

            output_dir = resigned_dirs[0] / new_id
            if not output_dir.exists():
                return None

            # Collect all output files and create zip
            output_files = list(output_dir.glob("*.bin"))
            if not output_files:
                return None

            # Create zip in memory
            zip_buffer = Path(tmpdir_path / "output.zip")
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
                for file_path in output_files:
                    zipf.write(file_path, file_path.name)

            # Clean up _OUTPUT directory
            shutil.rmtree(output_base, ignore_errors=True)

            return zip_buffer.read_bytes()

        return None

    # ------------------------------------------------------------------
    # AnonDrop upload helpers (fallback when zip is too large for Discord)
    # ------------------------------------------------------------------

    async def upload_to_anondrop(
        self,
        data: bytes,
        filename: str,
        progress_callback=None,
    ) -> str | None:
        """
        Upload bytes to AnonDrop.net and return the download URL, or None on failure.

        Uses simple POST for files ≤ 8 MB, chunked upload otherwise.
        progress_callback: optional async callable(percent: int) called during chunked upload.
        """
        log.info("AnonDrop: uploading %s (%d bytes)", filename, len(data))
        try:
            async with aiohttp.ClientSession() as session:
                if len(data) <= 8 * 1024 * 1024:
                    return await self._anondrop_simple_upload(session, data, filename)
                else:
                    return await self._anondrop_chunked_upload(
                        session, data, filename, progress_callback
                    )
        except Exception as e:
            log.warning(
                "AnonDrop: unexpected error in upload_to_anondrop: %s", e, exc_info=True
            )
            return None

    @staticmethod
    async def _anondrop_register(session: aiohttp.ClientSession) -> str | None:
        """Register on AnonDrop to get a userkey for chunked uploads."""
        try:
            async with session.get(
                "https://anondrop.net/register",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                log.debug("AnonDrop register status: %d", resp.status)
                if resp.status != 200:
                    log.warning("AnonDrop register HTTP %d", resp.status)
                    return None
                html = await resp.text()
                match = re.search(
                    r"localStorage\.setItem\(['\"]userkey['\"],\s*['\"](\d+)['\"]",
                    html,
                )
                if match:
                    return match.group(1)
                log.warning("AnonDrop register: no userkey in response: %s", html[:200])
        except Exception as e:
            log.warning("AnonDrop register error: %s", e)
        return None

    @staticmethod
    def _parse_anondrop_link(html: str) -> str | None:
        """Extract the AnonDrop download URL from response HTML."""
        match = re.search(r"(https?://anondrop\.net/\d+/[^\s\"'<>]+)", html)
        if match:
            return match.group(1)
        match = re.search(r"(https?://anondrop\.net/[a-zA-Z0-9]+)", html)
        if match:
            link = match.group(1)
            path = urlparse(link).path.strip("/")
            if path and path not in (
                "upload",
                "register",
                "initiateupload",
                "uploadchunk",
                "endupload",
                "embed",
            ):
                return link
        match = re.search(r"anondrop\.net/([a-zA-Z0-9]{6,})", html)
        if match:
            return f"https://anondrop.net/{match.group(1)}"
        log.warning("AnonDrop: could not parse link from response: %s", html[:300])
        return None

    async def _anondrop_simple_upload(
        self,
        session: aiohttp.ClientSession,
        data: bytes,
        filename: str,
    ) -> str | None:
        """Simple multipart POST for files ≤ 8 MB."""
        try:
            form = aiohttp.FormData()
            form.add_field("file", data, filename=filename)
            async with session.post(
                "https://anondrop.net/upload",
                data=form,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                log.debug("AnonDrop simple upload status: %d", resp.status)
                if resp.status != 200:
                    log.warning("AnonDrop simple upload HTTP %d", resp.status)
                    return None
                return self._parse_anondrop_link(await resp.text())
        except Exception as e:
            log.warning("AnonDrop simple upload error: %s", e)
            return None

    async def _anondrop_chunked_upload(
        self,
        session: aiohttp.ClientSession,
        data: bytes,
        filename: str,
        progress_callback=None,
    ) -> str | None:
        """Chunked upload for files > 8 MB."""
        try:
            userkey = await self._anondrop_register(session)
            if not userkey:
                log.warning("AnonDrop chunked: could not get userkey")
                return None

            # Step 1: Initiate
            async with session.get(
                "https://anondrop.net/initiateupload",
                params={"filename": filename, "key": userkey},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                log.debug("AnonDrop initiate status: %d", resp.status)
                if resp.status != 200:
                    log.warning("AnonDrop initiate HTTP %d", resp.status)
                    return None
                session_hash = (await resp.text()).strip()

            if not session_hash:
                log.warning("AnonDrop: empty session hash after initiate")
                return None

            log.debug("AnonDrop session_hash: %s", session_hash)

            # Step 2: Upload chunks
            total = len(data)
            total_chunks = (total + _ANONDROP_CHUNK_SIZE - 1) // _ANONDROP_CHUNK_SIZE
            offset = 0
            chunk_num = 0
            while offset < total:
                chunk = data[offset : offset + _ANONDROP_CHUNK_SIZE]
                offset += _ANONDROP_CHUNK_SIZE
                chunk_num += 1
                log.debug("AnonDrop uploading chunk %d/%d", chunk_num, total_chunks)
                chunk_form = aiohttp.FormData()
                chunk_form.add_field(
                    "file",
                    chunk,
                    filename=f"chunk_{chunk_num}",
                    content_type="application/octet-stream",
                )
                async with session.post(
                    "https://anondrop.net/uploadchunk",
                    params={"session_hash": session_hash},
                    data=chunk_form,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        log.warning(
                            "AnonDrop chunk %d/%d HTTP %d",
                            chunk_num,
                            total_chunks,
                            resp.status,
                        )
                        return None

                if progress_callback:
                    percent = int((chunk_num / total_chunks) * 100)
                    await progress_callback(percent)

            # Step 3: Finalize
            async with session.get(
                "https://anondrop.net/endupload",
                params={"session_hash": session_hash},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                log.debug("AnonDrop endupload status: %d", resp.status)
                if resp.status != 200:
                    log.warning("AnonDrop endupload HTTP %d", resp.status)
                    return None
                html = await resp.text()
                link = self._parse_anondrop_link(html)
                if link:
                    log.info("AnonDrop upload complete: %s", link)
                return link

        except Exception as e:
            log.warning("AnonDrop chunked upload error: %s", e, exc_info=True)
            return None
