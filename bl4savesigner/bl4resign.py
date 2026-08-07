import asyncio
import contextlib
import io
import logging
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

from .bl4_signer import BL4Signer

log = logging.getLogger("red.sablinova.pubhelper")

COG_DIR = Path(__file__).parent


# Tool discovery – matches idsaveresign layout
def _find_cli() -> Optional[Path]:
    candidates = [
        COG_DIR / "tools" / "bl4-savedata-resigner-cli",
        COG_DIR / "bin" / "bl4-savedata-resigner-cli",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


BL4_PLACEMENT_MSG = (
    "### 📂 Installation Instructions\n"
    "**1.** Press `Win + R`, paste the path below, and hit **Enter**:\n"
    "```cmd\n"
    "%USERPROFILE%\\Documents\\My Games\\Borderlands 4\\Saved\\SaveGames\\\n"
    "```\n"
    "**2.** Extract the attached `.zip` and copy the `.sav` file(s) into that folder, replacing the old ones.\n"
    "**3.** Launch the game normally!\n"
)

# Bruteforce timeouts — same thresholds as pubhelper's savebrute
BRUTEFORCE_INLINE_TIMEOUT = 840  # 14 minutes — switch to DM mode
BRUTEFORCE_MAX_TIMEOUT = 7200  # 120 minutes — give up


def _sanitize_cdn_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


class BL4Helper(commands.Cog):
    """Borderlands 4 save bruteforce/resign commands."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=48671590213, force_registration=True
        )
        self.config.register_global(
            known_save_ids=[],
        )
        self.cli_path = _find_cli()
        if not self.cli_path:
            log.error(
                "bl4-savedata-resigner-cli not found in %s/tools/ or %s/bin/",
                COG_DIR,
                COG_DIR,
            )
        elif not (self.cli_path.stat().st_mode & 0o111):
            with contextlib.suppress(Exception):
                self.cli_path.chmod(0o755)

        self.bl4_signer = BL4Signer(COG_DIR)
        self._cli_lock = asyncio.Lock()
        self.active_brutes: dict[int, asyncio.Task] = {}
        self.bruteforce_queue: list[dict] = []
        self.queued_brutes: dict[int, dict] = {}
        self.bruteforce_worker: asyncio.Task | None = None
        self.current_bruteforce_user_id: int | None = None

    async def cog_unload(self) -> None:
        if self.bruteforce_worker and not self.bruteforce_worker.done():
            self.bruteforce_worker.cancel()
        for task in list(self.active_brutes.values()):
            if not task.done():
                task.cancel()

    def get_cli_path(self) -> Path:
        """Find the resigner CLI, re-checking in case it was installed after cog load."""
        cli = _find_cli()
        if cli:
            return cli
        raise RuntimeError(
            "The BL4 resigner binary is missing. Put `bl4-savedata-resigner-cli` in:\n"
            f"`{COG_DIR}/tools/`"
        )

    # ── admin group ──────────────────────────────────────────────────────

    @commands.group(name="bl4helper")
    @commands.admin_or_permissions(manage_guild=True)
    async def bl4helper(self, ctx: commands.Context) -> None:
        """Borderlands 4 save bruteforce/resign tool admin commands."""
        pass

    @bl4helper.command(name="setup")
    @commands.is_owner()
    async def setuptool(self, ctx: commands.Context) -> None:
        """Download and install bl4-savedata-resigner-cli to tools/"""
        CLI_RELEASE_URL = "https://github.com/mi5hmash/Borderlands4SaveDataResigner/releases/download/v2.0.1/linux-x64_v2.0.1.zip"

        tools_dir = COG_DIR / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        target_cli = tools_dir / "bl4-savedata-resigner-cli"

        msg = await ctx.send("⏳ Downloading BL4 save resigner CLI from GitHub...")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    CLI_RELEASE_URL, timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status != 200:
                        return await msg.edit(
                            content=f"❌ Download failed: HTTP {resp.status}\n{CLI_RELEASE_URL}"
                        )
                    cli_data = await resp.read()

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)
                zip_path = tmpdir_path / "cli.zip"
                zip_path.write_bytes(cli_data)

                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(tmpdir_path / "cli")

                # Grab recursively to locate binary regardless of subfolder layout
                all_files = list((tmpdir_path / "cli").rglob("*"))

                # Filter out folders, .exe, and macOS files
                candidates = [
                    p
                    for p in all_files
                    if p.is_file()
                    and p.suffix != ".exe"
                    and "osx" not in p.name.lower()
                    and "mac" not in p.name.lower()
                    and not p.name.endswith((".zip", ".txt", ".md", ".json"))
                ]

                cli_binary = candidates[0] if candidates else None

                if not cli_binary:
                    return await msg.edit(
                        content="❌ CLI binary not found in archive."
                    )

                shutil.copy(cli_binary, target_cli)

            target_cli.chmod(0o755)
            self.cli_path = target_cli

            await msg.edit(
                content=(
                    f"✅ **Installed to** `{target_cli}`\n"
                    f"`{target_cli.stat().st_size / 1024 / 1024:.2f} MB`"
                )
            )

        except Exception as e:
            log.exception("bl4 setuptool failed")
            await msg.edit(content=f"❌ Installation failed: {e}")

    @bl4helper.command(name="status")
    async def toolstatus(self, ctx: commands.Context) -> None:
        """Check BL4 save resigner CLI installation status."""
        tool_path = _find_cli()
        if not tool_path:
            await ctx.send(
                "❌ **BL4 save resigner CLI not installed**\n\n"
                "Run `[p]bl4helper setup` to install."
            )
            return
        await ctx.send(f"✅ **BL4 save resigner CLI installed**\nPath: `{tool_path}`")

    @bl4helper.command(name="remove", aliases=["uninstall", "clean"])
    @commands.is_owner()
    async def removetool(self, ctx: commands.Context) -> None:
        """Remove the installed BL4 save resigner CLI binary."""
        tools_dir = COG_DIR / "tools"
        bin_dir = COG_DIR / "bin"

        removed = False
        for folder in (tools_dir, bin_dir):
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
                removed = True

        if removed:
            self.cli_path = None
            await ctx.send("✅ **BL4 save resigner CLI removed successfully.**")
        else:
            await ctx.send("ℹ️ No installed CLI binaries found to remove.")

    # ── queue helpers (mirrors pubhelper's savebrute queue) ────────────────

    def _get_bruteforce_queue_position(self, user_id: int) -> int | None:
        if self.current_bruteforce_user_id == user_id:
            return 1
        active_offset = 1 if self.current_bruteforce_user_id is not None else 0
        for index, item in enumerate(self.bruteforce_queue, start=1):
            if item["user_id"] == user_id:
                return index + active_offset
        return None

    async def _update_queued_bruteforce_messages(self) -> None:
        active_offset = 1 if self.current_bruteforce_user_id is not None else 0
        for index, item in enumerate(self.bruteforce_queue, start=1):
            interaction = item["interaction"]
            position = index + active_offset
            try:
                await interaction.edit_original_response(
                    content=(
                        f"⏳ **BL4 bruteforce queued**\n"
                        f"Queue position: `#{position}`\n"
                        f"_Will start automatically when it reaches the front._"
                    )
                )
            except Exception:
                pass

    async def _run_bruteforce_queue(self) -> None:
        try:
            while self.bruteforce_queue:
                item = self.bruteforce_queue.pop(0)
                user_id = item["user_id"]
                self.queued_brutes.pop(user_id, None)
                self.current_bruteforce_user_id = user_id
                await self._update_queued_bruteforce_messages()

                task = asyncio.create_task(
                    self._bl4brute_task(
                        item["interaction"],
                        item["new_id"],
                        item["save_archive"],
                        item.get("notify"),
                    )
                )
                self.active_brutes[user_id] = task

                try:
                    await item["interaction"].edit_original_response(
                        content=(
                            "⏳ Bruteforcing User ID for **Borderlands 4**...\n"
                            "Queue position: `#1`\n"
                            "_Your job reached the front of the queue. I'll update you when done._"
                        )
                    )
                except Exception:
                    pass

                try:
                    await task
                finally:
                    if self.active_brutes.get(user_id) == task:
                        self.active_brutes.pop(user_id, None)
                    self.current_bruteforce_user_id = None
                    await self._update_queued_bruteforce_messages()
        finally:
            self.bruteforce_worker = None

    def _ensure_bruteforce_worker(self) -> None:
        if self.bruteforce_worker and not self.bruteforce_worker.done():
            return
        self.bruteforce_worker = asyncio.create_task(self._run_bruteforce_queue())

    # ── download helper (mirrors pubhelper's _download_file) ──────────────

    async def _download_file(
        self,
        url: str,
        progress_callback=None,
        total_timeout: int | None = None,
    ) -> bytes | str:
        """Download file from URL. Returns bytes on success, error string on failure."""
        sanitized_url = _sanitize_cdn_url(url)
        timeout = aiohttp.ClientTimeout(
            total=total_timeout, connect=15, sock_connect=15, sock_read=120
        )
        started = time.monotonic()

        async def emit(line: str) -> None:
            if progress_callback is None:
                return
            result = progress_callback(line)
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                await result

        try:
            await emit("Starting archive download...")
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status == 403:
                        return "Access denied (403)"
                    elif resp.status == 404:
                        return "File not found (404)"
                    elif resp.status != 200:
                        return f"HTTP {resp.status}"

                    content_length_header = resp.headers.get("Content-Length")
                    advertised = None
                    if content_length_header and content_length_header.isdigit():
                        advertised = int(content_length_header)
                        if advertised > 500 * 1024 * 1024:
                            return (
                                f"File too large ({advertised // (1024 * 1024)} MB). "
                                f"Maximum supported is 500 MB."
                            )

                    chunks: list[bytes] = []
                    downloaded = 0
                    last_progress = started
                    async for chunk in resp.content.iter_chunked(65536):
                        chunks.append(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        if downloaded > 500 * 1024 * 1024:
                            await emit("Archive exceeds 500MB limit")
                            return "Archive exceeds 500MB limit"
                        if now - last_progress >= 1.0:
                            downloaded_mb = downloaded / (1024 * 1024)
                            if advertised:
                                percent = (downloaded / advertised) * 100
                                total_mb = advertised / (1024 * 1024)
                                await emit(
                                    f"Downloaded {downloaded_mb:.1f} / {total_mb:.1f} MB ({percent:.1f}%)"
                                )
                            else:
                                await emit(f"Downloaded {downloaded_mb:.1f} MB")
                            last_progress = now

                    content = b"".join(chunks)

                    if content.startswith(b"<!DOCTYPE") or content.startswith(b"<html"):
                        return "Link returned a webpage, not a file"
                    if b"This content is no longer available" in content:
                        return "Link expired"

                    return content

        except asyncio.TimeoutError:
            log.exception("[bl4] download timed out: url=%s", sanitized_url)
            return "Download timed out"
        except aiohttp.ClientError as e:
            log.exception("[bl4] download client error: url=%s", sanitized_url)
            return f"Connection error: {e}"
        except Exception as e:
            log.exception("[bl4] download unexpected error: url=%s", sanitized_url)
            return str(e)

    # ── /bl4brute ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="bl4brute",
        description="Bruteforce a BL4 save's Steam ID and re-sign it to yours",
    )
    @app_commands.describe(
        new_id="Your Steam64 ID to sign saves to",
        link="URL to save archive (zip/7z/rar)",
        notify="Mention the user to ping when done (optional)",
    )
    async def bl4brute(
        self,
        interaction: discord.Interaction,
        new_id: str,
        link: str,
        notify: discord.Member = None,
    ) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)

        if not _find_cli():
            await interaction.followup.send(
                "❌ The BL4 save resigner CLI is not installed. "
                "Ask an admin to run `[p]bl4helper setup`",
                ephemeral=True,
            )
            return

        result = await self._download_file(link)
        if isinstance(result, str):
            await interaction.followup.send(f"❌ Download failed: {result}")
            return
        save_archive = result

        if interaction.user.id in self.active_brutes:
            task = self.active_brutes[interaction.user.id]
            if not task.done():
                await interaction.followup.send(
                    "❌ You already have a BL4 bruteforce running. "
                    "Use `/bl4cancelbrute` to stop it first.",
                    ephemeral=True,
                )
                return

        existing_position = self._get_bruteforce_queue_position(interaction.user.id)
        if existing_position is not None:
            await interaction.followup.send(
                f"❌ You already have a BL4 bruteforce queued at position "
                f"`#{existing_position}`. Use `/bl4cancelbrute` to remove it.",
                ephemeral=True,
            )
            return

        queue_item = {
            "user_id": interaction.user.id,
            "interaction": interaction,
            "new_id": new_id,
            "save_archive": save_archive,
            "notify": notify,
        }
        self.bruteforce_queue.append(queue_item)
        self.queued_brutes[interaction.user.id] = queue_item

        queue_position = self._get_bruteforce_queue_position(interaction.user.id)
        if queue_position == 1:
            await interaction.followup.send(
                "⏳ BL4 bruteforce queued.\nQueue position: `#1`\n"
                "_Starting now. I'll update you when done._"
            )
        else:
            await interaction.followup.send(
                f"⏳ **BL4 bruteforce queued**\nQueue position: `#{queue_position}`\n"
                f"_Will start automatically when earlier jobs finish._"
            )

        self._ensure_bruteforce_worker()

    async def _bl4brute_task(
        self,
        interaction: discord.Interaction,
        new_id: str,
        save_archive: bytes,
        notify: discord.Member = None,
    ):
        start_time = asyncio.get_event_loop().time()

        async def send_final_message(content, inst=None, file=None):
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed < BRUTEFORCE_INLINE_TIMEOUT:
                try:
                    await interaction.edit_original_response(content=content)
                    if file:
                        await interaction.followup.send(
                            content=inst if inst else None, file=file
                        )
                    elif inst:
                        await interaction.followup.send(inst)
                    return
                except Exception as e:
                    log.warning(f"Failed to edit original BL4 response: {e}")

            try:
                if file and hasattr(file, "fp"):
                    file.fp.seek(0)
                await interaction.user.send(
                    content=f"{content}\n\n{inst}" if inst else content, file=file
                )
            except Exception as e:
                log.warning(f"Failed to send BL4 DM: {e}")

        log_queue = asyncio.Queue()

        async def progress_callback(line: str):
            await log_queue.put(line)

        try:
            brute_task = asyncio.create_task(
                self.bl4_signer.run_bruteforce(
                    save_archive, progress_callback=progress_callback
                )
            )

            brute_result = await asyncio.wait_for(
                brute_task, timeout=BRUTEFORCE_INLINE_TIMEOUT
            )

            if brute_result is None:
                await send_final_message(
                    "❌ **Bruteforce Failed**\n\n"
                    "Could not find a User ID. Make sure the archive contains a valid BL4 `.sav` file."
                )
                return

            found_id = brute_result["user_id"]

            await send_final_message(
                f"✅ **Found User ID: `{found_id}`**\n\nRe-signing to `{new_id}`..."
            )

            resign_result = await self.bl4_signer.run_resign(
                save_archive, found_id, new_id
            )

            if resign_result is None:
                await send_final_message(
                    f"❌ **Re-sign Failed**\n\nFound ID: `{found_id}`\nCould not re-sign the save."
                )
                return

            zip_filename = "bl4_resigned.zip"
            ping = f"{notify.mention}\n" if notify else ""
            zip_file = discord.File(io.BytesIO(resign_result), filename=zip_filename)

            await send_final_message(
                f"{ping}\n✅ **BL4 Bruteforce + Resign Complete!**\n\n"
                f"Original ID: `{found_id}` → New ID: `{new_id}`",
                inst=BL4_PLACEMENT_MSG,
                file=zip_file,
            )

        except asyncio.CancelledError:
            await send_final_message("🛑 **BL4 bruteforce cancelled manually by user.**")
        except Exception as e:
            log.error(f"BL4 bruteforce error: {e}", exc_info=True)
            await send_final_message(f"❌ **Error**: {e}")
        finally:
            if self.active_brutes.get(interaction.user.id) == asyncio.current_task():
                self.active_brutes.pop(interaction.user.id, None)

    # ── /bl4sign ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="bl4sign",
        description="Re-sign a BL4 save to a different Steam ID (when you already know the old ID)",
    )
    @app_commands.describe(
        old_id="Original Steam64 ID the save is signed to",
        new_id="Your Steam64 ID to sign saves to",
        link="URL to save archive (zip/7z/rar)",
        notify="Mention the user to ping when done (optional)",
    )
    async def bl4sign(
        self,
        interaction: discord.Interaction,
        old_id: str,
        new_id: str,
        link: str,
        notify: discord.Member = None,
    ) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)

        if not _find_cli():
            await interaction.followup.send(
                "❌ The BL4 save resigner CLI is not installed. "
                "Ask an admin to run `[p]bl4helper setup`",
                ephemeral=True,
            )
            return

        result = await self._download_file(link)
        if isinstance(result, str):
            await interaction.followup.send(f"❌ Download failed: {result}")
            return
        save_archive = result

        await interaction.followup.send("⏳ Re-signing your BL4 save...")

        resign_result = await self.bl4_signer.run_resign(save_archive, old_id, new_id)

        if resign_result is None:
            await interaction.edit_original_response(
                content=(
                    "❌ **Re-sign failed!**\n\n"
                    f"This usually means the **Original ID** (`{old_id}`) is incorrect, "
                    "or the archive doesn't contain a valid BL4 `.sav` file.\n\n"
                    "💡 *If you don't know the exact original Steam ID, use `/bl4brute` instead!*"
                )
            )
            return

        ping = f"{notify.mention}\n" if notify else ""
        success_msg = (
            f"{ping}\n✅ **Re-sign Complete!**\n\nOriginal ID: `{old_id}` → New ID: `{new_id}`"
        )
        zip_filename = "bl4_resigned.zip"

        await interaction.edit_original_response(content=success_msg)
        try:
            await interaction.followup.send(
                file=discord.File(io.BytesIO(resign_result), filename=zip_filename)
            )
            await interaction.followup.send(BL4_PLACEMENT_MSG)
        except discord.HTTPException as e:
            log.warning(f"BL4 resign file upload failed ({e.status}): {e}")
            await interaction.followup.send(
                "❌ File was too large to upload to Discord."
            )

    # ── /bl4cancelbrute ───────────────────────────────────────────────────

    @app_commands.command(
        name="bl4cancelbrute",
        description="Cancel your currently running or queued BL4 bruteforce task",
    )
    async def bl4cancelbrute(self, interaction: discord.Interaction) -> None:
        task = self.active_brutes.get(interaction.user.id)
        if task and not task.done():
            task.cancel()
            self.active_brutes.pop(interaction.user.id, None)
            await interaction.response.send_message(
                "🛑 Successfully sent cancellation signal to your BL4 bruteforce task. "
                "It will stop shortly.",
                ephemeral=True,
            )
            return

        queued_item = self.queued_brutes.pop(interaction.user.id, None)
        if queued_item:
            with contextlib.suppress(ValueError):
                self.bruteforce_queue.remove(queued_item)
            await self._update_queued_bruteforce_messages()
            await interaction.response.send_message(
                "🛑 Successfully removed your BL4 bruteforce from the queue.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ You don't have any active or queued BL4 bruteforce tasks.",
                ephemeral=True,
            )