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
        "description": "Resident Evil 9",
    },
    "cd": {
        "name": "CD",
        "basefiles_key": "cd_basefiles_set",
        "basefiles_name": "basefiles_cd.7z",
        "config_target": "steam_settings/configs.user.ini",
        "output_name": "CD_Combined.zip",
        "description": "CD Game",
    },
}


class GameSelectView(discord.ui.View):
    """View for selecting a game profile."""

    def __init__(self, cog: "SabPubHelper", author: discord.User):
        super().__init__(timeout=60)
        self.cog = cog
        self.author = author
        self.selected_game = None
        self.message = None

        # Add game select dropdown
        options = [
            discord.SelectOption(
                label=profile["name"],
                description=profile["description"],
                value=game_key,
            )
            for game_key, profile in GAME_PROFILES.items()
        ]
        self.game_select = discord.ui.Select(
            placeholder="Select a game...",
            options=options,
        )
        self.game_select.callback = self.game_selected
        self.add_item(self.game_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "This isn't for you.", ephemeral=True
            )
            return False
        return True

    async def game_selected(self, interaction: discord.Interaction) -> None:
        self.selected_game = self.game_select.values[0]
        profile = GAME_PROFILES[self.selected_game]

        # Update to show upload prompt
        embed = discord.Embed(
            title=f"Setup {profile['name']} Basefiles",
            description=(
                f"**Option 1:** Upload the basefiles `.7z` file as an attachment to this channel.\n\n"
                f"**Option 2:** Send a URL to the basefiles `.7z` file.\n\n"
                f"Waiting for your response..."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=None)

        # Wait for user's next message
        def check(m):
            return (
                m.author.id == self.author.id and m.channel.id == interaction.channel.id
            )

        try:
            msg = await self.cog.bot.wait_for("message", check=check, timeout=120)

            # Check for attachment
            if msg.attachments:
                attachment = msg.attachments[0]
                if not attachment.filename.endswith(".7z"):
                    await interaction.followup.send("Please upload a `.7z` file.")
                    return
                url = attachment.url
            elif msg.content.startswith("http"):
                url = msg.content.strip()
            else:
                await interaction.followup.send(
                    "Please provide a valid URL or upload a `.7z` file."
                )
                return

            # Process the basefiles
            await self._process_basefiles(interaction, self.selected_game, url)

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Setup timed out. Run the command again to restart."
            )

    async def _process_basefiles(
        self, interaction: discord.Interaction, game: str, url: str
    ) -> None:
        """Download and save basefiles."""
        profile = GAME_PROFILES[game]
        basefiles_path = self.cog._get_basefiles_path(game)

        status_msg = await interaction.followup.send(
            embed=discord.Embed(
                description="Downloading basefiles...",
                color=discord.Color.blurple(),
            )
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status != 200:
                        await status_msg.edit(
                            embed=discord.Embed(
                                description=f"Download failed: HTTP {resp.status}",
                                color=discord.Color.red(),
                            )
                        )
                        return

                    content = await resp.read()

                    if not content.startswith(b"7z"):
                        await status_msg.edit(
                            embed=discord.Embed(
                                description="The file is not a valid 7z archive.",
                                color=discord.Color.red(),
                            )
                        )
                        return

                    # Save to data path
                    with open(basefiles_path, "wb") as f:
                        f.write(content)

                    await self.cog.config.set_raw(profile["basefiles_key"], value=True)
                    size_mb = len(content) / (1024 * 1024)

                    await status_msg.edit(
                        embed=discord.Embed(
                            title="Basefiles Saved",
                            description=(
                                f"**Game:** {profile['name']}\n"
                                f"**Size:** {size_mb:.2f} MB\n\n"
                                f"The `/{game}cc` command is now ready.\n"
                                f"Don't forget to run:\n"
                                f"```\n[p]slash enable {game}cc\n[p]slash sync\n```"
                            ),
                            color=discord.Color.green(),
                        )
                    )

        except asyncio.TimeoutError:
            await status_msg.edit(
                embed=discord.Embed(
                    description="Download timed out.",
                    color=discord.Color.red(),
                )
            )
        except Exception as e:
            log.exception("Failed to set basefiles")
            await status_msg.edit(
                embed=discord.Embed(
                    description=f"Error: {e}",
                    color=discord.Color.red(),
                )
            )


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

    @pubhelper.command(name="setup")
    async def setup_interactive(self, ctx: commands.Context) -> None:
        """Interactive setup wizard for basefiles.

        Guides you through setting up basefiles with a dropdown menu.
        You can upload files directly or provide a URL.
        """
        embed = discord.Embed(
            title="PubHelper Setup",
            description="Select a game to configure its basefiles:",
            color=discord.Color.blurple(),
        )
        view = GameSelectView(self, ctx.author)
        view.message = await ctx.send(embed=embed, view=view)

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

                        if not content.startswith(b"7z"):
                            await ctx.send(
                                "The downloaded file is not a valid 7z archive."
                            )
                            return

                        with open(basefiles_path, "wb") as f:
                            f.write(content)

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

    @pubhelper.command(name="help")
    async def help_command(self, ctx: commands.Context) -> None:
        """Show detailed setup and usage guide."""
        embed = discord.Embed(
            title="PubHelper Setup Guide",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="What is PubHelper?",
            value=(
                "PubHelper combines user token configs with game basefiles. "
                "Users provide their token zip, and the bot creates a ready-to-use package."
            ),
            inline=False,
        )

        embed.add_field(
            name="Step 1: Set Basefiles",
            value=(
                "**Interactive:**\n"
                "```\n[p]pubhelper setup\n```\n"
                "**Direct:**\n"
                "```\n[p]pubhelper setbasefiles re9 <url>\n"
                "[p]pubhelper setbasefiles cd <url>\n```"
            ),
            inline=False,
        )

        embed.add_field(
            name="Step 2: Enable Slash Commands",
            value=(
                "```\n[p]slash enable re9cc\n[p]slash enable cdcc\n[p]slash sync\n```"
            ),
            inline=False,
        )

        embed.add_field(
            name="Step 3: Users Can Now Use",
            value=(
                "`/re9cc url:<token zip link>` - RE9 package\n"
                "`/cdcc url:<token zip link>` - CD package"
            ),
            inline=False,
        )

        embed.add_field(
            name="How It Works",
            value=(
                "1. User provides token zip URL\n"
                "2. Bot downloads and extracts `configs.user.ini`\n"
                "3. Bot injects it into your basefiles\n"
                "4. Bot uploads combined package to user"
            ),
            inline=False,
        )

        embed.add_field(
            name="Other Commands",
            value=(
                "`[p]pubhelper status` - Check basefiles status\n"
                "`[p]pubhelper setup` - Interactive setup wizard\n"
                "`[p]pubhelper help` - This guide"
            ),
            inline=False,
        )

        await ctx.send(embed=embed)

    async def _process_command(
        self, interaction: discord.Interaction, url: str, game: str
    ) -> None:
        """Common processing logic for all game commands."""
        profile = GAME_PROFILES[game]
        basefiles_path = self._get_basefiles_path(game)

        is_set = await self.config.get_raw(profile["basefiles_key"], default=False)
        if not is_set or not basefiles_path.exists():
            await interaction.response.send_message(
                f"{profile['name']} basefiles not configured. Ask the bot owner to run "
                f"`[p]pubhelper setbasefiles {game} <url>` first.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                description="Downloading your file...",
                color=discord.Color.blurple(),
            )
        )

        try:
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

            await interaction.edit_original_response(
                embed=discord.Embed(
                    description="Processing and combining files...",
                    color=discord.Color.blurple(),
                )
            )

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

            with open(user_zip_path, "wb") as f:
                f.write(user_zip_data)

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

            extract_dir.mkdir(parents=True, exist_ok=True)
            try:
                with py7zr.SevenZipFile(basefiles_path, "r") as z:
                    z.extractall(extract_dir)
            except Exception as e:
                return f"Failed to extract basefiles: {e}"

            target_config = extract_dir / profile["config_target"]
            target_config.parent.mkdir(parents=True, exist_ok=True)
            with open(target_config, "wb") as f:
                f.write(config_content)

            output_zip = tmpdir / profile["output_name"]
            with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as z:
                for root, dirs, files in os.walk(extract_dir):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(extract_dir)
                        z.write(file_path, arcname)

            with open(output_zip, "rb") as f:
                output_data = f.read()

            return (profile["output_name"], output_data)
