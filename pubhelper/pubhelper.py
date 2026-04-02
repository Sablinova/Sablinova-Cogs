"""
PubHelper - Config Combiner Cog for Red-DiscordBot

Provides slash commands to combine user configs with basefiles for different games.
"""

import asyncio
import io
import logging
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

import aiohttp
import discord
import py7zr
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path

log = logging.getLogger("red.sablinova.pubhelper")

INVALID_LINK_MSG = "Invalid or expired link. Please provide a valid token link."

# Game profiles configuration
GAME_PROFILES = {
    "re9": {
        "name": "RE9",
        "basefiles_key": "re9_basefiles_set",
        "basefiles_name": "basefiles_re9.7z",
        "config_target": "pub_re9/steam_settings/configs.user.ini",
        "output_name": "RE9_Combined.zip",
    },
    "cd": {
        "name": "CD",
        "basefiles_key": "cd_basefiles_set",
        "basefiles_name": "basefiles_cd.7z",
        "config_target": "steam_settings/configs.user.ini",
        "output_name": "CD_Combined.zip",
    },
}


class SabPubHelper(commands.Cog):
    """Config combiner - extracts configs.user.ini and combines with basefiles."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=9832017465, force_registration=True
        )
        self.config.register_global(
            re9_basefiles_set=False,
            cd_basefiles_set=False,
        )
        self.data_path = cog_data_path(self)

    def _get_basefiles_path(self, game: str) -> Path:
        """Get the basefiles path for a game profile."""
        return self.data_path / GAME_PROFILES[game]["basefiles_name"]

    async def cog_load(self) -> None:
        """Called when the cog is loaded."""
        self.data_path.mkdir(parents=True, exist_ok=True)

    @commands.group(name="pubhelper")
    @commands.is_owner()
    async def pubhelper(self, ctx: commands.Context) -> None:
        """PubHelper configuration commands."""
        pass

    @pubhelper.command(name="setbasefiles")
    async def set_basefiles(self, ctx: commands.Context, game: str, url: str) -> None:
        """Set the basefiles 7z template for a game.

        **Games:** re9, cd

        **Usage:**
        `[p]pubhelper setbasefiles re9 <url>`
        `[p]pubhelper setbasefiles cd <url>`
        """
        game = game.lower()
        if game not in GAME_PROFILES:
            await ctx.send(
                f"Unknown game `{game}`. Available: {', '.join(GAME_PROFILES.keys())}"
            )
            return

        profile = GAME_PROFILES[game]
        basefiles_path = self._get_basefiles_path(game)

        async with ctx.typing():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=120)
                    ) as resp:
                        if resp.status != 200:
                            await ctx.send(f"Failed to download: HTTP {resp.status}")
                            return

                        content = await resp.read()

                        # Verify it's a valid 7z file
                        if not content.startswith(b"7z"):
                            await ctx.send(
                                "The downloaded file is not a valid 7z archive."
                            )
                            return

                        # Save to data path
                        with open(basefiles_path, "wb") as f:
                            f.write(content)

                        # Verify structure - check if target path exists
                        with py7zr.SevenZipFile(basefiles_path, "r") as z:
                            names = z.getnames()
                            target_dir = str(Path(profile["config_target"]).parent)
                            if not any(target_dir in n for n in names):
                                await ctx.send(
                                    f"Warning: basefiles may not have the expected structure. "
                                    f"Expected path containing `{target_dir}`."
                                )

                        await self.config.set_raw(profile["basefiles_key"], value=True)
                        size_mb = len(content) / (1024 * 1024)
                        await ctx.send(
                            f"{profile['name']} basefiles saved successfully ({size_mb:.2f} MB). "
                            f"The `/{game}cc` command is now ready to use."
                        )

            except asyncio.TimeoutError:
                await ctx.send("Download timed out. Try a faster host.")
            except py7zr.Bad7zFile:
                await ctx.send("The downloaded file is not a valid 7z archive.")
            except Exception as e:
                log.exception("Failed to set basefiles")
                await ctx.send(f"Error: {e}")

    @pubhelper.command(name="status")
    async def status(self, ctx: commands.Context) -> None:
        """Check status of all basefiles."""
        lines = []
        for game, profile in GAME_PROFILES.items():
            basefiles_path = self._get_basefiles_path(game)
            is_set = await self.config.get_raw(profile["basefiles_key"], default=False)
            if is_set and basefiles_path.exists():
                size_mb = basefiles_path.stat().st_size / (1024 * 1024)
                lines.append(f"**{profile['name']}:** Configured ({size_mb:.2f} MB)")
            else:
                lines.append(f"**{profile['name']}:** Not configured")

        await ctx.send("\n".join(lines))

    async def _process_command(
        self, interaction: discord.Interaction, url: str, game: str
    ) -> None:
        """Common processing logic for all game commands."""
        profile = GAME_PROFILES[game]
        basefiles_path = self._get_basefiles_path(game)

        # Check if basefiles are configured
        is_set = await self.config.get_raw(profile["basefiles_key"], default=False)
        if not is_set or not basefiles_path.exists():
            await interaction.response.send_message(
                f"{profile['name']} basefiles not configured. Ask the bot owner to run "
                f"`[p]pubhelper setbasefiles {game} <url>` first.",
                ephemeral=True,
            )
            return

        # Send initial status message
        await interaction.response.send_message(
            embed=discord.Embed(
                description="Downloading your file...",
                color=discord.Color.blurple(),
            )
        )

        try:
            # Step 1: Download user's zip
            download_result = await self._download_file(url)
            if isinstance(download_result, str):
                await interaction.edit_original_response(
                    embed=discord.Embed(
                        description=f"Download failed: {download_result}\n\n{INVALID_LINK_MSG}",
                        color=discord.Color.red(),
                    )
                )
                return

            user_zip_data = download_result

            # Step 2: Update status - processing
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description="Processing and combining files...",
                    color=discord.Color.blurple(),
                )
            )

            # Step 3: Process files
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self._combine_files, user_zip_data, game
            )

            if isinstance(result, str):
                await interaction.edit_original_response(
                    embed=discord.Embed(
                        description=f"{result}\n\n{INVALID_LINK_MSG}",
                        color=discord.Color.red(),
                    )
                )
                return

            # Step 4: Upload result
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description="Uploading combined package...",
                    color=discord.Color.blurple(),
                )
            )

            filename, data = result
            file = discord.File(io.BytesIO(data), filename=filename)
            size_mb = len(data) / (1024 * 1024)

            await interaction.edit_original_response(
                embed=discord.Embed(
                    description=f"Your combined {profile['name']} package is ready! ({size_mb:.2f} MB)",
                    color=discord.Color.green(),
                ),
                attachments=[file],
            )

        except Exception as e:
            log.exception("Error processing config")
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description=f"Error: {e}\n\n{INVALID_LINK_MSG}",
                    color=discord.Color.red(),
                )
            )

    @app_commands.command(
        name="re9cc", description="Combine your config with RE9 basefiles"
    )
    @app_commands.describe(url="URL to your token zip file")
    async def re9cc(self, interaction: discord.Interaction, url: str) -> None:
        """Combine user config with RE9 basefiles."""
        await self._process_command(interaction, url, "re9")

    @app_commands.command(
        name="cdcc", description="Combine your config with CD basefiles"
    )
    @app_commands.describe(url="URL to your token zip file")
    async def cdcc(self, interaction: discord.Interaction, url: str) -> None:
        """Combine user config with CD basefiles."""
        await self._process_command(interaction, url, "cd")

    async def _download_file(self, url: str) -> bytes | str:
        """Download file from URL. Returns bytes on success, error string on failure."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 403:
                        return "Access denied (403)"
                    elif resp.status == 404:
                        return "File not found (404)"
                    elif resp.status != 200:
                        return f"HTTP {resp.status}"

                    content = await resp.read()

                    # Check if it's actually a file or an error page
                    if content.startswith(b"<!DOCTYPE") or content.startswith(b"<html"):
                        return "Link returned a webpage, not a file"

                    if b"This content is no longer available" in content:
                        return "Link expired"

                    return content

        except asyncio.TimeoutError:
            return "Download timed out"
        except aiohttp.ClientError as e:
            return f"Connection error: {e}"
        except Exception as e:
            return str(e)

    def _combine_files(
        self, user_zip_data: bytes, game: str
    ) -> tuple[str, bytes] | str:
        """Combine user config with basefiles. Runs in executor."""
        profile = GAME_PROFILES[game]
        basefiles_path = self._get_basefiles_path(game)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            user_zip_path = tmpdir / "user.zip"
            extract_dir = tmpdir / "extracted"

            # Save user zip
            with open(user_zip_path, "wb") as f:
                f.write(user_zip_data)

            # Extract user zip and find configs.user.ini
            try:
                with zipfile.ZipFile(user_zip_path, "r") as z:
                    config_path = None
                    for name in z.namelist():
                        if name.endswith("configs.user.ini"):
                            config_path = name
                            break

                    if not config_path:
                        return "Could not find `configs.user.ini` in your zip file."

                    config_content = z.read(config_path)
            except zipfile.BadZipFile:
                return "The provided file is not a valid zip archive."

            # Extract basefiles
            extract_dir.mkdir(parents=True, exist_ok=True)
            try:
                with py7zr.SevenZipFile(basefiles_path, "r") as z:
                    z.extractall(extract_dir)
            except Exception as e:
                return f"Failed to extract basefiles: {e}"

            # Find or create target config path
            target_config = extract_dir / profile["config_target"]

            # Create parent dirs if needed and write config
            target_config.parent.mkdir(parents=True, exist_ok=True)
            with open(target_config, "wb") as f:
                f.write(config_content)

            # Create output zip
            output_zip = tmpdir / profile["output_name"]
            with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files in os.walk(extract_dir):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(extract_dir)
                        z.write(file_path, arcname)

            # Read output zip
            with open(output_zip, "rb") as f:
                output_data = f.read()

            return (profile["output_name"], output_data)
