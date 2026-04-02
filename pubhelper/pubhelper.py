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

# Default game profiles
DEFAULT_PROFILES = {
    "re9": {
        "name": "RE9",
        "config_target": "pub_re9/steam_settings/configs.user.ini",
        "output_name": "RE9_Combined.zip",
        "description": "Resident Evil 9",
        "basefiles_set": False,
    },
    "cd": {
        "name": "CD",
        "config_target": "steam_settings/configs.user.ini",
        "output_name": "CD_Combined.zip",
        "description": "CD Game",
        "basefiles_set": False,
    },
}


class GameSelectView(discord.ui.View):
    """View for selecting a game profile."""

    def __init__(
        self, cog: "SabPubHelper", author: discord.User, action: str = "setup"
    ):
        super().__init__(timeout=60)
        self.cog = cog
        self.author = author
        self.action = action  # "setup" or "configpath"
        self.selected_game = None
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "This isn't for you.", ephemeral=True
            )
            return False
        return True

    async def update_dropdown(self) -> None:
        """Update dropdown with current games."""
        profiles = await self.cog.config.profiles()
        options = [
            discord.SelectOption(
                label=profile["name"],
                description=profile["description"],
                value=game_key,
            )
            for game_key, profile in profiles.items()
        ]
        if not options:
            options = [discord.SelectOption(label="No games configured", value="none")]

        self.game_select.options = options

    @discord.ui.select(
        placeholder="Select a game...",
        options=[discord.SelectOption(label="Loading...", value="loading")],
    )
    async def game_select(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        if select.values[0] in ("loading", "none"):
            await interaction.response.defer()
            return

        self.selected_game = select.values[0]
        profiles = await self.cog.config.profiles()
        profile = profiles.get(self.selected_game)

        if not profile:
            await interaction.response.send_message("Game not found.", ephemeral=True)
            return

        if self.action == "setup":
            await self._handle_setup(interaction, profile)
        elif self.action == "configpath":
            await self._handle_configpath(interaction, profile)

    async def _handle_setup(
        self, interaction: discord.Interaction, profile: dict
    ) -> None:
        """Handle basefiles setup."""
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

        def check(m):
            return (
                m.author.id == self.author.id and m.channel.id == interaction.channel.id
            )

        try:
            msg = await self.cog.bot.wait_for("message", check=check, timeout=120)

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

            await self._process_basefiles(interaction, self.selected_game, url)

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Setup timed out. Run the command again to restart."
            )

    async def _handle_configpath(
        self, interaction: discord.Interaction, profile: dict
    ) -> None:
        """Handle config path change."""
        embed = discord.Embed(
            title=f"Change Config Path for {profile['name']}",
            description=(
                f"**Current path:** `{profile['config_target']}`\n\n"
                f"Send the new path where `configs.user.ini` should be placed.\n"
                f"Example: `steam_settings/configs.user.ini`\n\n"
                f"Waiting for your response..."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=None)

        def check(m):
            return (
                m.author.id == self.author.id and m.channel.id == interaction.channel.id
            )

        try:
            msg = await self.cog.bot.wait_for("message", check=check, timeout=60)
            new_path = msg.content.strip()

            if not new_path.endswith("configs.user.ini"):
                if not new_path.endswith("/"):
                    new_path += "/"
                new_path += "configs.user.ini"

            # Update config
            async with self.cog.config.profiles() as profiles:
                profiles[self.selected_game]["config_target"] = new_path

            await interaction.followup.send(
                embed=discord.Embed(
                    title="Config Path Updated",
                    description=(
                        f"**Game:** {profile['name']}\n**New path:** `{new_path}`"
                    ),
                    color=discord.Color.green(),
                )
            )

        except asyncio.TimeoutError:
            await interaction.followup.send("Timed out. Run the command again.")

    async def _process_basefiles(
        self, interaction: discord.Interaction, game: str, url: str
    ) -> None:
        """Download and save basefiles."""
        profiles = await self.cog.config.profiles()
        profile = profiles[game]
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

                    with open(basefiles_path, "wb") as f:
                        f.write(content)

                    async with self.cog.config.profiles() as profiles:
                        profiles[game]["basefiles_set"] = True

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
            profiles=DEFAULT_PROFILES,
        )
        self.data_path = cog_data_path(self)

    def _get_basefiles_path(self, game: str) -> Path:
        """Get the basefiles path for a game profile."""
        return self.data_path / f"basefiles_{game}.7z"

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
        view = GameSelectView(self, ctx.author, action="setup")
        await view.update_dropdown()
        view.message = await ctx.send(embed=embed, view=view)

    @pubhelper.command(name="setpath")
    async def set_config_path_interactive(self, ctx: commands.Context) -> None:
        """Interactive command to change config.user.ini target path.

        Use this to change where configs.user.ini gets placed in the basefiles.
        """
        embed = discord.Embed(
            title="Change Config Path",
            description="Select a game to change its config target path:",
            color=discord.Color.blurple(),
        )
        view = GameSelectView(self, ctx.author, action="configpath")
        await view.update_dropdown()
        view.message = await ctx.send(embed=embed, view=view)

    @pubhelper.command(name="configpath")
    async def set_config_path(
        self, ctx: commands.Context, game: str, *, path: str
    ) -> None:
        """Set the config.user.ini target path for a game.

        **Usage:**
        `[p]pubhelper configpath re9 pub_re9/steam_settings/configs.user.ini`
        `[p]pubhelper configpath cd steam_settings/configs.user.ini`
        """
        game = game.lower()
        profiles = await self.config.profiles()

        if game not in profiles:
            await ctx.send(
                f"Unknown game `{game}`. Available: {', '.join(profiles.keys())}"
            )
            return

        if not path.endswith("configs.user.ini"):
            if not path.endswith("/"):
                path += "/"
            path += "configs.user.ini"

        async with self.config.profiles() as profiles:
            old_path = profiles[game]["config_target"]
            profiles[game]["config_target"] = path

        await ctx.send(
            f"**{profiles[game]['name']}** config path updated:\n"
            f"Old: `{old_path}`\n"
            f"New: `{path}`"
        )

    @pubhelper.command(name="setbasefiles")
    async def set_basefiles(self, ctx: commands.Context, game: str, url: str) -> None:
        """Set the basefiles 7z template for a game.

        **Games:** re9, cd

        **Usage:**
        `[p]pubhelper setbasefiles re9 <url>`
        `[p]pubhelper setbasefiles cd <url>`
        """
        game = game.lower()
        profiles = await self.config.profiles()

        if game not in profiles:
            await ctx.send(
                f"Unknown game `{game}`. Available: {', '.join(profiles.keys())}"
            )
            return

        profile = profiles[game]
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
                                    f"Expected path containing `{target_dir}`.\n"
                                    f"Use `[p]pubhelper setpath {game}` to change the config path."
                                )

                        async with self.config.profiles() as profiles:
                            profiles[game]["basefiles_set"] = True

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
        """Check status of all games and their config paths."""
        profiles = await self.config.profiles()
        lines = []

        for game, profile in profiles.items():
            basefiles_path = self._get_basefiles_path(game)
            is_set = profile.get("basefiles_set", False)

            if is_set and basefiles_path.exists():
                size_mb = basefiles_path.stat().st_size / (1024 * 1024)
                status = f"Configured ({size_mb:.2f} MB)"
            else:
                status = "Not configured"

            lines.append(
                f"**{profile['name']}** (`/{game}cc`)\n"
                f"  Status: {status}\n"
                f"  Config path: `{profile['config_target']}`"
            )

        await ctx.send("\n\n".join(lines))

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
            name="Step 2: Configure Path (Optional)",
            value=(
                "Change where `configs.user.ini` is placed:\n"
                "**Interactive:**\n"
                "```\n[p]pubhelper setpath\n```\n"
                "**Direct:**\n"
                "```\n[p]pubhelper configpath re9 pub_re9/steam_settings/\n```"
            ),
            inline=False,
        )

        embed.add_field(
            name="Step 3: Enable Slash Commands",
            value=(
                "```\n[p]slash enable re9cc\n[p]slash enable cdcc\n[p]slash sync\n```"
            ),
            inline=False,
        )

        embed.add_field(
            name="Step 4: Users Can Now Use",
            value=(
                "`/re9cc url:<token zip link>` - RE9 package\n"
                "`/cdcc url:<token zip link>` - CD package"
            ),
            inline=False,
        )

        embed.add_field(
            name="Other Commands",
            value=(
                "`[p]pubhelper status` - Check all games status & paths\n"
                "`[p]pubhelper setup` - Interactive basefiles setup\n"
                "`[p]pubhelper setpath` - Interactive path change\n"
                "`[p]pubhelper help` - This guide"
            ),
            inline=False,
        )

        await ctx.send(embed=embed)

    async def _process_command(
        self, interaction: discord.Interaction, url: str, game: str
    ) -> None:
        """Common processing logic for all game commands."""
        profiles = await self.config.profiles()
        profile = profiles.get(game)

        if not profile:
            await interaction.response.send_message(
                f"Game `{game}` not configured.",
                ephemeral=True,
            )
            return

        basefiles_path = self._get_basefiles_path(game)
        is_set = profile.get("basefiles_set", False)

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
                None, self._combine_files, user_zip_data, game, profile
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
        self, user_zip_data: bytes, game: str, profile: dict
    ) -> tuple[str, bytes] | str:
        """Combine user config with basefiles. Runs in executor."""
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
