"""
BL4Helper - Borderlands 4 save bruteforce/resign Cog for Red-DiscordBot

Only the save-signing surface of pubhelper (savebrute/savesign/cancelbrute
and their queue/progress/fallback machinery) was used as a template here —
the config-combiner, anadius, and saveinst pieces of pubhelper are not
relevant to BL4 and are intentionally left out.

BL4 has no game profiles to pick from (it's a single game), so unlike
SAVE_PROFILES-based commands there's no `game` choice param.
"""

import asyncio
import contextlib
import io
import logging
import time
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red

from .bl4_signer import BL4Signer

log = logging.getLogger("red.sablinova.pubhelper")

# Mirrors SAVE_PLACEMENT_MSG's shape, adapted for BL4's save location
# (confirmed in the tool's README).
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
        self.bl4_signer = BL4Signer(self.data_path)
        self._cli_lock = asyncio.Lock()
        self.active_brutes: dict[int, asyncio.Task] = {}
        self.bruteforce_queue: list[dict] = []
        self.queued_brutes: dict[int, dict] = {}
        self.bruteforce_worker: asyncio.Task | None = None
        self.current_bruteforce_user_id: int | None = None

    async def cog_load(self) -> None:
        self.data_path.mkdir(parents=True, exist_ok=True)

    async def cog_unload(self) -> None:
        if self.bruteforce_worker and not self.bruteforce_worker.done():
            self.bruteforce_worker.cancel()
        for task in list(self.active_brutes.values()):
            if not task.done():
                task.cancel()

    # ── admin group ──────────────────────────────────────────────────────

    @commands.group(name="bl4helper")
    @commands.admin_or_permissions(manage_guild=True)
    async def bl4helper(self, ctx: commands.Context) -> None:
        """Borderlands 4 save bruteforce/resign tool admin commands."""
        pass
     
    @bl4helper.command(name="setup")
    @commands.is_owner()
    async def setuptool(self, ctx: commands.Context) -> None:
        """Download and install the BL4 save resigner CLI from GitHub.

        NOTE: I don't know the exact release asset URL/filename for
        mi5hmash/Borderlands4SaveDataResigner (pubhelper's setuptool
        hardcodes a specific MandarinJuice release asset — this needs the
        equivalent BL4 release URL filled in before this command works).
        """
        await ctx.send(
            "⏳ Downloading BL4 save resigner CLI from GitHub..."
        )

        tools_dir = self.data_path / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)

        # TODO: replace with the actual release asset URL once known, e.g.
        # https://github.com/mi5hmash/Borderlands4SaveDataResigner/releases/download/<tag>/<asset>.zip
        cli_release_url = "https://github.com/mi5hmash/Borderlands4SaveDataResigner/releases/download/v2.0.1/linux-x64_v2.0.1.zip"

        if not cli_release_url:
            await ctx.send(
                "❌ No release URL configured for the BL4 CLI yet. "
                "Check https://github.com/mi5hmash/Borderlands4SaveDataResigner/releases "
                "for the correct asset and update `cli_release_url` in bl4_cog.py."
            )
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    cli_release_url, timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status != 200:
                        await ctx.send(f"❌ Failed to download CLI: HTTP {resp.status}")
                        return
                    cli_data = await resp.read()

            target_cli = tools_dir / "bl4-savedata-resigner-cli.exe"
            target_cli.write_bytes(cli_data)
            with contextlib.suppress(Exception):
                target_cli.chmod(0o755)

            await ctx.send(
                f"✅ **BL4 save resigner CLI installed!**\n\nCLI: `{target_cli}`"
            )
        except Exception as e:
            log.error(f"bl4 setuptool error: {e}", exc_info=True)
            await ctx.send(f"❌ Installation failed: {e}")

    @bl4helper.command(name="status")
    async def toolstatus(self, ctx: commands.Context) -> None:
        """Check BL4 save resigner CLI installation status."""
        tool_path = self.bl4_signer.get_tool_path()
        if not tool_path:
            await ctx.send(
                "❌ **BL4 save resigner CLI not installed**\n\n"
                "Run `[p]bl4helper setup` to install."
            )
            return
        await ctx.send(f"✅ **BL4 save resigner CLI installed**\nPath: `{tool_path}`")

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

    # ── /bl4brute (mirrors /savebrute) ─────────────────────────────────────

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

        if not self.bl4_signer.is_tool_installed():
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
            await self._send_bruteforce_queue_log(
                interaction,
                f"📥 **BL4 bruteforce queued for {interaction.user.name}**",
                position=queue_position,
            )

        self._ensure_bruteforce_worker()

    async def _bl4brute_task(
        self,
        interaction: discord.Interaction,
        new_id: str,
        save_archive: bytes,
        notify: discord.Member = None,
    ):
        """Background task for bl4brute, mirroring pubhelper's _savebrute_task."""
        start_time = asyncio.get_event_loop().time()

        log_channel_id = await self.config.log_channel()
        fallback_channel = (
            self.bot.get_channel(log_channel_id) if log_channel_id else None
        )
        cli_log_channel_id = await self.config.cli_log_channel()
        cli_log_channel = (
            self.bot.get_channel(cli_log_channel_id) if cli_log_channel_id else None
        )

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

            # Interaction expired — fall back to DM
            try:
                if file and hasattr(file, "fp"):
                    file.fp.seek(0)
                await interaction.user.send(
                    content=f"{content}\n\n{inst}" if inst else content, file=file
                )
                return
            except (discord.Forbidden, discord.HTTPException) as e:
                log.warning(f"Failed to send BL4 DM to {interaction.user}: {e}")

            fallback_channels = []
            if interaction.channel and hasattr(interaction.channel, "send"):
                fallback_channels.append(interaction.channel)
            if fallback_channel:
                fallback_channels.append(fallback_channel)
            if cli_log_channel:
                fallback_channels.append(cli_log_channel)

            for ch in fallback_channels:
                mention_content = f"{interaction.user.mention} {content}"
                if file and hasattr(file, "fp"):
                    file.fp.seek(0)
                try:
                    await ch.send(
                        content=f"{mention_content}\n\n{inst}" if inst else mention_content,
                        file=file,
                    )
                    return
                except Exception as e2:
                    log.error(f"Failed to send BL4 result to fallback channel {ch.id}: {e2}")

            log.error(
                f"Failed to deliver BL4 savebrute results to {interaction.user} via any fallback channel."
            )

        log_queue = asyncio.Queue()

        async def progress_callback(line: str):
            await log_queue.put(line)

        log_message = None
        cancelled = False
        success = False
        finalizing = {"done": False}
        log_buffer = []
        task_start = time.monotonic()

        def _fmt_duration(seconds: float) -> str:
            seconds = max(0, int(seconds))
            m, s = divmod(seconds, 60)
            if m >= 60:
                h, m = divmod(m, 60)
                return f"{h:d}:{m:02d}:{s:02d}"
            return f"{m:d}:{s:02d}"

        async def _drain_log_queue():
            try:
                while True:
                    log_buffer.append(log_queue.get_nowait())
            except asyncio.QueueEmpty:
                pass

        def _build_final_log_text() -> str:
            if not log_buffer:
                return "No logs produced."
            final_text = "\n".join(log_buffer[-25:])
            if len(final_text) <= 1800:
                return final_text
            return f"...{final_text[-1797:]}"

        if cli_log_channel:
            try:
                log_message = await cli_log_channel.send(
                    f"🟢 **BL4 bruteforce — running**\n"
                    f"User: {interaction.user.display_name} UserID: {interaction.user.id}   "
                    f"Channel: {interaction.channel.mention}\n"
                    f"Lines: 0   Elapsed: 0:00\n```\nStarting…\n```"
                )
            except Exception as e:
                log.error(f"Failed to send initial BL4 log message: {e}")
                cli_log_channel = None

        async def log_updater():
            while True:
                try:
                    line = await asyncio.wait_for(log_queue.get(), timeout=15.0)
                    log_buffer.append(line)
                    while not log_queue.empty():
                        log_buffer.append(log_queue.get_nowait())
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    await _drain_log_queue()
                    break

                if log_buffer:
                    latest = log_buffer[-1]
                    if cli_log_channel and log_message and not finalizing["done"]:
                        try:
                            await log_message.edit(
                                content=(
                                    f"🟢 **BL4 bruteforce — running**\n"
                                    f"User: {interaction.user.display_name} UserID: {interaction.user.id}   "
                                    f"Channel: {interaction.channel.mention}\n"
                                    f"Lines: {len(log_buffer)}   Elapsed: "
                                    f"{_fmt_duration(time.monotonic() - task_start)}\n"
                                    f"```\n{latest[-1800:]}\n```"
                                )
                            )
                        except Exception as e:
                            log.warning(f"Failed to update BL4 log message: {e}")

                    try:
                        await interaction.edit_original_response(
                            content=(
                                "⏳ Bruteforcing User ID for **Borderlands 4**...\n"
                                f"**Progress:** `{latest}`"
                            )
                        )
                    except Exception:
                        pass

                    await asyncio.sleep(2.5)

        progress_task = asyncio.create_task(log_updater())

        try:
            brute_task = asyncio.create_task(
                self.bl4_signer.run_bruteforce(
                    save_archive, progress_callback=progress_callback
                )
            )

            try:
                brute_result = await asyncio.wait_for(
                    brute_task, timeout=BRUTEFORCE_INLINE_TIMEOUT
                )
            except asyncio.TimeoutError:
                try:
                    await interaction.edit_original_response(
                        content=(
                            "⏳ Still bruteforcing **Borderlands 4**...\n"
                            "_This is taking longer than usual! I will DM you the files when it's completely finished._"
                        )
                    )
                except Exception:
                    pass
                remaining = BRUTEFORCE_MAX_TIMEOUT - BRUTEFORCE_INLINE_TIMEOUT
                try:
                    brute_result = await asyncio.wait_for(brute_task, timeout=remaining)
                except asyncio.TimeoutError:
                    brute_task.cancel()
                    await send_final_message(
                        "❌ **BL4 Bruteforce Timed Out**\n\n"
                        "Exceeded 120 minutes. Find your SteamID64 manually and use "
                        "`/bl4sign` instead."
                    )
                    return

            if brute_result is None:
                await send_final_message(
                    "❌ **Bruteforce Failed**\n\n"
                    "Could not find a User ID. Make sure the archive contains a valid BL4 `.sav` file."
                )
                return

            found_id = brute_result["user_id"]

            known_ids = await self.config.known_save_ids()
            updated = False
            if found_id not in known_ids:
                known_ids.append(found_id)
                updated = True
            if new_id not in known_ids:
                known_ids.append(new_id)
                updated = True
            if updated:
                await self.config.known_save_ids.set(known_ids)

            if not progress_task.done():
                finalizing["done"] = True
                progress_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await progress_task
                await _drain_log_queue()

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
            success = True

        except asyncio.CancelledError:
            cancelled = True
            await send_final_message("🛑 **BL4 bruteforce cancelled manually by user.**")
        except Exception as e:
            log.error(f"BL4 bruteforce error: {e}", exc_info=True)
            await send_final_message(f"❌ **Error**: {e}")
        finally:
            if self.active_brutes.get(interaction.user.id) == asyncio.current_task():
                self.active_brutes.pop(interaction.user.id, None)

            if not progress_task.done():
                finalizing["done"] = True
                progress_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await progress_task
                await _drain_log_queue()

            if cli_log_channel and log_message:
                try:
                    if cancelled:
                        icon, status_text = "🛑", "cancelled"
                    elif success:
                        icon, status_text = "✅", "complete"
                        log_buffer.append("BL4 bruteforce completed successfully.")
                    else:
                        icon, status_text = "❌", "failed"

                    await log_message.edit(
                        content=(
                            f"{icon} **BL4 bruteforce — {status_text}**\n"
                            f"User: {interaction.user.display_name} UserID: {interaction.user.id}   "
                            f"Channel: {interaction.channel.mention}\n"
                            f"Lines: {len(log_buffer)}   Duration: "
                            f"{_fmt_duration(time.monotonic() - task_start)}\n"
                            f"```\n{_build_final_log_text()}\n```"
                        )
                    )
                except Exception:
                    pass

    # ── /bl4sign (mirrors /savesign — direct resign, no queue) ────────────

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

        if not self.bl4_signer.is_tool_installed():
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

        known_ids = await self.config.known_save_ids()
        updated = False
        if old_id not in known_ids:
            known_ids.append(old_id)
            updated = True
        if new_id not in known_ids:
            known_ids.append(new_id)
            updated = True
        if updated:
            await self.config.known_save_ids.set(known_ids)

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

    # ── /bl4cancelbrute (mirrors /cancelbrute) ─────────────────────────────

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
            await self._send_bruteforce_queue_log(
                queued_item["interaction"],
                f"🛑 **Queued BL4 bruteforce removed for {interaction.user.name}**",
            )
            await interaction.response.send_message(
                "🛑 Successfully removed your BL4 bruteforce from the queue.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ You don't have any active or queued BL4 bruteforce tasks.",
                ephemeral=True,
            )


async def setup(bot: Red):
    await bot.add_cog(BL4Helper(bot))
