"""
idsaveresign - Red-DiscordBot cog

Wraps mi5hmash's id-savedata-resigner-cli (idTech 7/8 SaveData resigner) behind
a Discord slash command so users can re-sign a zipped save folder from one
SteamID64 to another without touching a shell.

Requires on the host running the bot:
    - .NET 10 runtime installed (the bundled CLI is framework-dependent, not
      self-contained):  sudo apt install dotnet-runtime-10.0
    - pip install rarfile py7zr  (for .rar / .7z upload support)
    - unrar / p7zip-full on PATH recommended as fallback extractors

CLI binary location (checked in order):
    1. <cog_folder>/tools/id-savedata-resigner-cli
    2. <cog_folder>/bin/id-savedata-resigner-cli
"""

import asyncio
import io
import logging
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from redbot.core import commands, Config
from redbot.core.bot import Red

# Optional archive backends – same as SaveSigner cog
try:
    import rarfile  # pip install rarfile  | apt install unrar
except ImportError:
    rarfile = None

try:
    import py7zr  # pip install py7zr
except ImportError:
    py7zr = None

log = logging.getLogger("red.idsaveresign")

COG_DIR = Path(__file__).parent

# Tool discovery – matches SaveSigner layout
def _find_cli() -> Optional[Path]:
    candidates = [
        COG_DIR / "tools" / "id-savedata-resigner-cli",
        COG_DIR / "tools" / "id-savedata-resigner",
        COG_DIR / "bin" / "id-savedata-resigner-cli",
        COG_DIR / "bin" / "id-savedata-resigner",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None

CLI_PATH = _find_cli()

STEAMID64_RE = re.compile(r"^\d{17}$")
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100 MB cap on the incoming archive
CLI_TIMEOUT_SECONDS = 120

SAVE_PLACEMENT_MSG = (
    "### 📂 Installation Instructions\n"
    "**1.** Press `Win + R`, paste the path below, and hit **Enter**:\n"
    "```cmd\n"
    "%AppData%\\GSE Saves\\3017860\\remote\\\n"
    "```\n"
    "**2.** Extract the attached `.zip` — copy all folder inside into above folder, replacing old folders.\n"
    "**3.** Launch the game normally!\n\n"
    "-# 🐧 **Linux / Steam Deck:** `~/.local/share/crucible-launcher/Prefix/doom_the_dark_agesprefix/drive_c/users/steamuser/AppData/Roaming/GSE Saves/3017860/remote/`\n"
)


@dataclass(frozen=True)
class GameProfile:
    key: str
    label: str
    game_code: str
    appid: int
    save_folder: str  # folder name under "Saved Games/id Software/"


GAME_PROFILES: dict[str, GameProfile] = {
    "darkages": GameProfile(
        key="darkages",
        label="DOOM: The Dark Ages",
        game_code="MANCUBUS",
        appid=3017860,
        save_folder="DOOMTheDarkAges",
    ),
}

# Hardcoded profile – only one game
DEFAULT_PROFILE = GAME_PROFILES["darkages"]


class IdSaveResignError(Exception):
    """Raised for any expected/handled failure in the resign pipeline."""


class IdSaveResign(commands.Cog):
    """Re-sign idTech 7/8 SaveData files between SteamID64s (currently: DOOM: The Dark Ages)."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x1D5A7E51, force_registration=True)
        self.config.register_guild(dump_channel_id=None)
        self._session: Optional[aiohttp.ClientSession] = None
        self._tmp_root = COG_DIR / "tmp"
        self._tmp_root.mkdir(exist_ok=True)

        self.cli_path = _find_cli()
        if not self.cli_path:
            log.error(
                "id-savedata-resigner-cli not found in %s/tools/ or %s/bin/",
                COG_DIR, COG_DIR
            )
        elif not (self.cli_path.stat().st_mode & 0o111):
            try:
                self.cli_path.chmod(0o755)
            except Exception:
                pass

    async def cog_load(self):
        self._session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self._session:
            await self._session.close()
        shutil.rmtree(self._tmp_root, ignore_errors=True)

    # ---------------------------------------------------------------- utils

    def get_cli_path(self) -> Path:
        """Find the resigner CLI, re-checking in case it was installed after cog load."""
        cli = _find_cli()
        if cli:
            return cli
        raise IdSaveResignError(
            "The resigner binary is missing. Put `id-savedata-resigner-cli` in:\n"
            f"`{COG_DIR}/tools/`"
        )

    @staticmethod
    def _validate_steamid(value: str, label: str) -> str:
        value = value.strip()
        if not STEAMID64_RE.match(value):
            raise IdSaveResignError(
                f"`{value}` doesn't look like a SteamID64 for **{label}** "
                "(needs to be exactly 17 digits, e.g. `76561197960265729`)."
            )
        return value

    async def _download_archive(self, url: str, dest: Path) -> Path:
        if not url.lower().startswith(("http://", "https://")):
            raise IdSaveResignError("Link has to be a direct http(s) URL to the zip/rar/7z.")

        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    raise IdSaveResignError(f"Download failed with HTTP {resp.status}.")

                content_length = resp.content_length
                if content_length and content_length > MAX_DOWNLOAD_BYTES:
                    raise IdSaveResignError(
                        f"That file is {content_length / 1024 / 1024:.1f} MB, "
                        f"which is over the {MAX_DOWNLOAD_BYTES // 1024 // 1024} MB limit."
                    )

                url_l = url.lower()
                if ".rar" in url_l:
                    ext = ".rar"
                elif ".7z" in url_l:
                    ext = ".7z"
                else:
                    ext = ".zip"

                out_path = dest / f"upload{ext}"
                written = 0
                with open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(65536):
                        written += len(chunk)
                        if written > MAX_DOWNLOAD_BYTES:
                            raise IdSaveResignError(
                                f"Download exceeded the {MAX_DOWNLOAD_BYTES // 1024 // 1024} MB limit."
                            )
                        f.write(chunk)
                return out_path
        except asyncio.TimeoutError as e:
            raise IdSaveResignError("Download timed out.") from e
        except aiohttp.ClientError as e:
            raise IdSaveResignError(f"Download failed: {e}") from e

    @staticmethod
    def _extract_archive(archive_path: Path, dest: Path) -> None:
        """Extract zip/rar/7z – same logic as the SaveSigner cog."""
        dest.mkdir(parents=True, exist_ok=True)

        with open(archive_path, "rb") as f:
            magic = f.read(10)

        # RAR
        if magic.startswith(b"Rar!\x1a\x07"):
            if rarfile:
                try:
                    with rarfile.RarFile(archive_path) as archive:
                        archive.extractall(dest)
                    return
                except Exception as e:
                    log.warning("rarfile extraction failed: %s – falling back to 7z", e)
            # fall through to 7z subprocess

        # 7z
        elif magic.startswith(b"7z\xbc\xaf\x27\x1c"):
            if py7zr:
                try:
                    with py7zr.SevenZipFile(archive_path, "r") as archive:
                        archive.extractall(dest)
                    return
                except Exception as e:
                    log.warning("py7zr extraction failed: %s – falling back to 7z", e)
            # fall through to 7z subprocess

        # ZIP
        elif magic.startswith(b"PK\x03\x04") or magic.startswith(b"PK\x05\x06") or magic.startswith(b"PK\x07\x08"):
            try:
                with zipfile.ZipFile(archive_path) as zf:
                    zf.extractall(dest)
                return
            except zipfile.BadZipFile:
                pass  # fall through to 7z

        # Fallback: 7z CLI – handles zip/rar/7z and mislabeled archives
        if shutil.which("7z") is None:
            missing = []
            if rarfile is None:
                missing.append("pip install rarfile")
            if py7zr is None:
                missing.append("pip install py7zr")
            missing_str = " / ".join(missing) + " or " if missing else ""
            raise IdSaveResignError(
                "Couldn't extract that archive. "
                f"Install {missing_str}apt install p7zip-full unrar "
                "on the bot host for .rar/.7z support."
            )

        result = subprocess.run(
            ["7z", "x", str(archive_path), f"-o{dest}", "-y"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise IdSaveResignError(
                f"Couldn't extract the archive with 7z:\n```\n{result.stdout[-500:]}\n{result.stderr[-500:]}\n```"
            )

    @staticmethod
    def _find_save_root(extracted_dir: Path) -> Path:
        entries = list(extracted_dir.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0]
        return extracted_dir

    async def _run_resign(
        self, profile: GameProfile, save_root: Path, old_id: str, new_id: str, work_dir: Path
    ) -> Path:
        cli_path = self.get_cli_path()

        cmd = [
            str(cli_path),
            "-m", "r",
            "-g", profile.game_code,
            "-p", str(save_root),
            "-uI", old_id,
            "-uO", new_id,
            "-q",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=CLI_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise IdSaveResignError("The resigner process timed out.")

        output_text = stdout.decode(errors="replace")
        error_lines = [ln for ln in output_text.splitlines() if "[Error]" in ln]

        if error_lines:
            reason = error_lines[0].split(": ", 2)[-1]
            if "AuthenticationTagMismatch" in reason or "AuthenticationTag" in output_text:
                raise IdSaveResignError(
                    "Re-sign failed: the **old SteamID64 doesn't match these save files** "
                    "(authentication tag mismatch). Double check `old_id`."
                )
            raise IdSaveResignError(f"Re-sign failed:\n```\n{reason[:500]}\n```")

        output_root = work_dir / "_OUTPUT"
        if not output_root.exists():
            raise IdSaveResignError(
                "The resigner reported no errors but produced no output."
            )
        candidates = sorted(output_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise IdSaveResignError("Resigner produced an empty output folder.")
        return candidates[0]

    @staticmethod
    def _zip_dir(src_dir: Path, out_path: Path) -> Path:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in src_dir.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(src_dir))
        return out_path

    async def _deliver(
        self, interaction: discord.Interaction, zip_path: Path, filename: str
    ) -> str:
        size = zip_path.stat().st_size
        limit = interaction.guild.filesize_limit if interaction.guild else 8 * 1024 * 1024

        if size <= limit:
            await interaction.followup.send(
                content="Here's your resigned save:",
                file=discord.File(zip_path, filename=filename),
            )
            return "attached directly"

        dump_channel_id = await self.config.guild(interaction.guild).dump_channel_id()
        dump_channel = interaction.guild.get_channel(dump_channel_id) if dump_channel_id else None

        if dump_channel is None:
            raise IdSaveResignError(
                f"Resigned save is {size / 1024 / 1024:.1f} MB, over this server's "
                f"{limit / 1024 / 1024:.1f} MB upload limit, and no dump channel is configured. "
                f"Ask an admin to run `/idsaveresign_setdumpchannel`."
            )

        dump_msg = await dump_channel.send(
            content=f"Resigned save for {interaction.user.mention} ({filename})",
            file=discord.File(zip_path, filename=filename),
        )
        if not dump_msg.attachments:
            raise IdSaveResignError("Upload to the dump channel didn't produce a file link.")
        url = dump_msg.attachments[0].url
        await interaction.followup.send(
            content=f"That file was {size / 1024 / 1024:.1f} MB (too big to attach here). "
            f"Here's a link instead: {url}"
        )
        return "linked via dump channel"

    # ------------------------------------------------------------- commands

    @app_commands.command(
        name="idsaveresign",
        description="Re-sign a DOOM: The Dark Ages save from one SteamID64 to another.",
    )
    @app_commands.describe(
        link="Direct link to a .zip/.rar/.7z of the save folder",
        old_id="Original SteamID64 the save is currently signed to",
        new_id="Target SteamID64 to re-sign the save to",
    )
    async def idsaveresign(
        self,
        interaction: discord.Interaction,
        link: str,
        old_id: str,
        new_id: str,
    ):
        await interaction.response.defer(thinking=True)

        job_dir = self._tmp_root / f"{interaction.id}-{uuid.uuid4().hex[:8]}"
        job_dir.mkdir(parents=True, exist_ok=True)
        dl_dir = job_dir / "download"
        extract_dir = job_dir / "extracted"
        work_dir = job_dir / "work"
        for d in (dl_dir, extract_dir, work_dir):
            d.mkdir(parents=True, exist_ok=True)

        try:
            profile = DEFAULT_PROFILE
            old_id = self._validate_steamid(old_id, "old_id")
            new_id = self._validate_steamid(new_id, "new_id")

            archive_path = await self._download_archive(link, dl_dir)
            self._extract_archive(archive_path, extract_dir)
            save_root = self._find_save_root(extract_dir)

            output_folder = await self._run_resign(profile, save_root, old_id, new_id, work_dir)

            out_zip = self._zip_dir(output_folder, job_dir / f"{profile.key}_{new_id}.zip")
            await self._deliver(interaction, out_zip, out_zip.name)

            placement = SAVE_PLACEMENT_MSG.format(
                new_id=new_id, appid=profile.appid, save_folder=profile.save_folder
            )
            await interaction.followup.send(content=placement)

        except IdSaveResignError as e:
            await interaction.followup.send(content=f"⚠️ {e}")
        except Exception:
            log.exception("Unhandled error in /idsaveresign")
            await interaction.followup.send(
                content="⚠️ Something went wrong unexpectedly — check the bot logs."
            )
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    @commands.command(name="idsaveresign_setup")
    @commands.is_owner()
    async def idsaveresign_setup(self, ctx: commands.Context):
        """Download and install id-savedata-resigner-cli to tools/"""
        # Update this when Mi5hmasH releases a new version
        CLI_URL = "https://github.com/mi5hmash/idSaveDataResigner/releases/download/v2.0.1/linux-x64_v2.0.1.zip"
        IS_ZIPPED = True   # set True if the release asset is a .zip

        tools_dir = COG_DIR / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        target_cli = tools_dir / "id-savedata-resigner-cli"

        msg = await ctx.send("⏳ Downloading idSaveDataResigner CLI…")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(CLI_URL, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status != 200:
                        return await msg.edit(content=f"❌ Download failed: HTTP {resp.status}\n{CLI_URL}")
                    cli_data = await resp.read()

            if IS_ZIPPED:
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    zip_path = tmpdir_path / "cli.zip"
                    zip_path.write_bytes(cli_data)
                    with zipfile.ZipFile(zip_path) as zf:
                        zf.extractall(tmpdir_path / "cli")
                    cli_binary = next((tmpdir_path / "cli").rglob("id-savedata-resigner-cli*"), None)
                    if not cli_binary or not cli_binary.is_file():
                        return await msg.edit(content="❌ CLI binary not found in archive")
                    shutil.copy(cli_binary, target_cli)
            else:
                with open(target_cli, "wb") as f:
                    f.write(cli_data)

            target_cli.chmod(0o755)
            self.cli_path = target_cli

            await msg.edit(content=f"✅ Installed to `{target_cli}`\n`{target_cli.stat().st_size / 1024 / 1024:.2f} MB`\n.NET 10 runtime still required on the host: `sudo apt install dotnet-runtime-10.0`")
        except Exception as e:
            log.exception("idsaveresign_setup failed")
            await msg.edit(content=f"❌ Install failed: {e}")

    @app_commands.command(
        name="idsaveresign_setdumpchannel",
        description="Set the channel used to host resigned saves too large to attach directly.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def idsaveresign_setdumpchannel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        await self.config.guild(interaction.guild).dump_channel_id.set(channel.id)
        await interaction.response.send_message(
            f"Large resigned saves will now be uploaded to {channel.mention} and linked.",
            ephemeral=True,
        )