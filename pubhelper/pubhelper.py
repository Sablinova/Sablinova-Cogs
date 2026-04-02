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
        "basefiles_format": "7z",  # "7z" or "zip"
    },
    "cd": {
        "name": "CD",
        "config_target": "steam_settings/configs.user.ini",
        "output_name": "CD_Combined.zip",
        "description": "CD Game",
        "basefiles_set": False,
        "basefiles_format": "7z",  # "7z" or "zip"
    },
}


def _detect_archive_format(content: bytes) -> str | None:
    """Detect archive format from magic bytes.

    Returns: "7z", "zip", or None if unknown.
    """
    if content.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    elif content.startswith(b"PK\x03\x04") or content.startswith(b"PK\x05\x06"):
        return "zip"
    return None


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
                f"**Option 1:** Upload the basefiles `.7z` or `.zip` file as an attachment.\n\n"
                f"**Option 2:** Send a URL to the basefiles archive.\n\n"
                f"**Option 3:** Type `cancel` to abort.\n\n"
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

            if msg.content.strip().lower() == "cancel":
                await interaction.followup.send("Setup cancelled.")
                return

            if msg.attachments:
                attachment = msg.attachments[0]
                if not (
                    attachment.filename.endswith(".7z")
                    or attachment.filename.endswith(".zip")
                ):
                    await interaction.followup.send(
                        "Please upload a `.7z` or `.zip` file."
                    )
                    return
                url = attachment.url
            elif msg.content.startswith("http"):
                url = msg.content.strip()
            else:
                await interaction.followup.send(
                    "Please provide a valid URL or upload a `.7z`/`.zip` file."
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

                    # Detect archive format
                    fmt = _detect_archive_format(content)
                    if not fmt:
                        await status_msg.edit(
                            embed=discord.Embed(
                                description="The file is not a valid archive. Supported formats: `.7z`, `.zip`",
                                color=discord.Color.red(),
                            )
                        )
                        return

                    # Remove old basefiles if format changed
                    for old_fmt in ("7z", "zip"):
                        old_path = self.cog._get_basefiles_path(game, old_fmt)
                        if old_path.exists() and old_fmt != fmt:
                            old_path.unlink()

                    basefiles_path = self.cog._get_basefiles_path(game, fmt)
                    with open(basefiles_path, "wb") as f:
                        f.write(content)

                    async with self.cog.config.profiles() as profiles:
                        profiles[game]["basefiles_set"] = True
                        profiles[game]["basefiles_format"] = fmt

                    size_mb = len(content) / (1024 * 1024)

                    await status_msg.edit(
                        embed=discord.Embed(
                            title="Basefiles Saved",
                            description=(
                                f"**Game:** {profile['name']}\n"
                                f"**Format:** {fmt}\n"
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

    def _get_basefiles_path(self, game: str, fmt: str = "7z") -> Path:
        """Get the basefiles path for a game profile."""
        return self.data_path / f"basefiles_{game}.{fmt}"

    def _find_basefiles_path(self, game: str) -> Path | None:
        """Find existing basefiles for a game (checks both formats)."""
        for fmt in ("7z", "zip"):
            path = self._get_basefiles_path(game, fmt)
            if path.exists():
                return path
        return None

    def _make_game_command(self, game_id: str):
        """Create a slash command callback for a game."""
        cog = self

        @app_commands.describe(url="URL to your token zip file")
        async def callback(interaction: discord.Interaction, url: str) -> None:
            await cog._process_command(interaction, url, game_id)

        return callback

    async def cog_load(self) -> None:
        """Called when the cog is loaded."""
        self.data_path.mkdir(parents=True, exist_ok=True)

        profiles = await self.config.profiles()

        # Register dynamic slash commands for non-builtin games
        builtin_games = {"re9", "cd"}
        for game_id, profile in profiles.items():
            if game_id not in builtin_games:
                # Create and register dynamic command
                callback = self._make_game_command(game_id)
                cmd = app_commands.Command(
                    name=f"{game_id}cc",
                    description=f"Combine your config with {profile['name']} basefiles",
                    callback=callback,
                )
                try:
                    self.bot.tree.add_command(cmd)
                    log.info(f"Registered dynamic slash command /{game_id}cc")
                except Exception as e:
                    log.warning(f"Could not register /{game_id}cc: {e}")

        # Auto-detect basefiles on disk and mark as configured
        async with self.config.profiles() as profiles:
            for game in profiles:
                basefiles_path = self._find_basefiles_path(game)
                if basefiles_path and not profiles[game].get("basefiles_set", False):
                    # Detect format from extension
                    fmt = basefiles_path.suffix.lstrip(".")
                    profiles[game]["basefiles_set"] = True
                    profiles[game]["basefiles_format"] = fmt
                    log.info(f"Auto-detected {game} basefiles at {basefiles_path}")

    async def cog_unload(self) -> None:
        """Called when the cog is unloaded."""
        profiles = await self.config.profiles()
        builtin_games = {"re9", "cd"}

        # Remove dynamic slash commands
        for game_id in profiles:
            if game_id not in builtin_games:
                try:
                    self.bot.tree.remove_command(f"{game_id}cc")
                    log.info(f"Removed dynamic slash command /{game_id}cc")
                except Exception:
                    pass

    @commands.group(name="pubhelper")
    @commands.admin_or_permissions(manage_guild=True)
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

    @pubhelper.command(name="addgame")
    async def add_game_interactive(self, ctx: commands.Context) -> None:
        """Interactive wizard to add a new game profile.

        Guides you through creating a new game with:
        - Game ID (used for slash command, e.g., 're9' creates /re9cc)
        - Display name
        - Description
        - Config target path
        - Basefiles upload
        """
        embed = discord.Embed(
            title="Add New Game",
            description=(
                "Let's add a new game profile!\n\n"
                "**Step 1/4:** Enter the game ID (lowercase, no spaces).\n"
                "This will be used for the slash command, e.g., `re9` creates `/re9cc`.\n\n"
                "Type the game ID below, or `cancel` to abort:"
            ),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

        def check(m):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

        try:
            # Step 1: Game ID
            msg = await self.bot.wait_for("message", check=check, timeout=60)

            if msg.content.strip().lower() == "cancel":
                await ctx.send("Game setup cancelled.")
                return

            game_id = msg.content.strip().lower().replace(" ", "_")

            # Validate game ID
            if not game_id.isalnum() and "_" not in game_id:
                await ctx.send(
                    "Invalid game ID. Use only letters, numbers, and underscores."
                )
                return

            profiles = await self.config.profiles()
            if game_id in profiles:
                await ctx.send(
                    f"Game `{game_id}` already exists. Use `[p]pubhelper setup` to configure it."
                )
                return

            # Step 2: Display Name
            embed = discord.Embed(
                title="Add New Game",
                description=(
                    f"**Game ID:** `{game_id}`\n\n"
                    "**Step 2/4:** Enter the display name for this game.\n"
                    "Example: `Resident Evil 9`\n\n"
                    "Type the display name below, or `cancel` to abort:"
                ),
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)

            msg = await self.bot.wait_for("message", check=check, timeout=60)

            if msg.content.strip().lower() == "cancel":
                await ctx.send("Game setup cancelled.")
                return

            display_name = msg.content.strip()

            # Step 3: Description
            embed = discord.Embed(
                title="Add New Game",
                description=(
                    f"**Game ID:** `{game_id}`\n"
                    f"**Name:** {display_name}\n\n"
                    "**Step 3/4:** Enter a short description (optional).\n"
                    "Type `skip` to use the display name, or `cancel` to abort.\n\n"
                    "Type the description below:"
                ),
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)

            msg = await self.bot.wait_for("message", check=check, timeout=60)

            if msg.content.strip().lower() == "cancel":
                await ctx.send("Game setup cancelled.")
                return

            description = msg.content.strip()
            if description.lower() == "skip":
                description = display_name

            # Step 4: Config Path
            embed = discord.Embed(
                title="Add New Game",
                description=(
                    f"**Game ID:** `{game_id}`\n"
                    f"**Name:** {display_name}\n"
                    f"**Description:** {description}\n\n"
                    "**Step 4/4:** Enter the config target path.\n"
                    "This is where `configs.user.ini` will be placed in the basefiles.\n\n"
                    "Examples:\n"
                    "- `steam_settings/configs.user.ini`\n"
                    "- `pub_re9/steam_settings/configs.user.ini`\n\n"
                    "Type the path below, or `cancel` to abort:"
                ),
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)

            msg = await self.bot.wait_for("message", check=check, timeout=60)

            if msg.content.strip().lower() == "cancel":
                await ctx.send("Game setup cancelled.")
                return

            config_path = msg.content.strip()

            if not config_path.endswith("configs.user.ini"):
                if not config_path.endswith("/"):
                    config_path += "/"
                config_path += "configs.user.ini"

            # Create the new profile
            new_profile = {
                "name": display_name,
                "config_target": config_path,
                "output_name": f"{display_name.replace(' ', '_')}_Combined.zip",
                "description": description,
                "basefiles_set": False,
                "basefiles_format": "7z",
            }

            async with self.config.profiles() as profiles:
                profiles[game_id] = new_profile

            # Success message
            embed = discord.Embed(
                title="Game Added Successfully!",
                description=(
                    f"**Game ID:** `{game_id}`\n"
                    f"**Name:** {display_name}\n"
                    f"**Description:** {description}\n"
                    f"**Config Path:** `{config_path}`\n\n"
                    "**Next steps:**\n"
                    f"1. Reload cog: `[p]reload pubhelper`\n"
                    f"2. Sync slash commands: `[p]pubhelper syncslash`\n"
                    f"3. Upload basefiles: `[p]pubhelper setup` and select {display_name}\n\n"
                    f"After sync, `/{game_id}cc` will be available."
                ),
                color=discord.Color.green(),
            )
            await ctx.send(embed=embed)

        except asyncio.TimeoutError:
            await ctx.send("Setup timed out. Run `[p]pubhelper addgame` to try again.")

    @pubhelper.command(name="syncslash")
    async def sync_slash(self, ctx: commands.Context) -> None:
        """Sync all pubhelper slash commands to Discord.

        Run this after adding new games to make their slash commands available.
        """
        async with ctx.typing():
            try:
                # Sync the command tree
                synced = await self.bot.tree.sync()
                await ctx.send(f"Synced {len(synced)} slash commands to Discord.")
            except Exception as e:
                log.exception("Failed to sync slash commands")
                await ctx.send(f"Failed to sync: {e}")

    @pubhelper.command(name="removegame")
    async def remove_game(self, ctx: commands.Context, game: str) -> None:
        """Remove a game profile.

        **Usage:**
        `[p]pubhelper removegame <game_id>`

        This will remove the game profile and delete its basefiles.
        Built-in games (re9, cd) cannot be removed.
        """
        game = game.lower()
        profiles = await self.config.profiles()

        if game not in profiles:
            await ctx.send(
                f"Unknown game `{game}`. Available: {', '.join(profiles.keys())}"
            )
            return

        if game in ("re9", "cd"):
            await ctx.send(
                "Cannot remove built-in games. You can reconfigure them instead."
            )
            return

        profile = profiles[game]

        # Confirm deletion
        embed = discord.Embed(
            title="Confirm Deletion",
            description=(
                f"Are you sure you want to remove **{profile['name']}** (`{game}`)?\n\n"
                f"This will:\n"
                f"- Remove the game profile\n"
                f"- Delete basefiles (if any)\n"
                f"- The `/{game}cc` command will no longer work\n\n"
                f"Type `yes` to confirm or `no` to cancel."
            ),
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)

        def check(m):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=30)
            if msg.content.strip().lower() != "yes":
                await ctx.send("Deletion cancelled.")
                return

            # Delete basefiles
            for fmt in ("7z", "zip"):
                path = self._get_basefiles_path(game, fmt)
                if path.exists():
                    path.unlink()

            # Remove profile
            async with self.config.profiles() as profiles:
                del profiles[game]

            await ctx.send(
                f"Game **{profile['name']}** removed successfully.\n"
                f"Note: The `/{game}cc` slash command may still appear until you run `[p]slash sync`."
            )

        except asyncio.TimeoutError:
            await ctx.send("Confirmation timed out. Deletion cancelled.")

    @pubhelper.command(name="updatedll")
    async def update_dll(self, ctx: commands.Context) -> None:
        """Update steamclient64.dll across all game basefiles.

        Upload or provide a URL to the new steamclient64.dll file.
        This will update the DLL in ALL configured game basefiles.
        """
        embed = discord.Embed(
            title="Update steamclient64.dll",
            description=(
                "This will update `steamclient64.dll` in **all** game basefiles.\n\n"
                "**Option 1:** Upload the `.dll` file as an attachment.\n"
                "**Option 2:** Send a URL to the `.dll` file.\n"
                "**Option 3:** Type `cancel` to abort.\n\n"
                "Waiting for your response..."
            ),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)

        def check(m):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=120)

            # Check for cancel
            if msg.content.strip().lower() == "cancel":
                await ctx.send("DLL update cancelled.")
                return

            if msg.attachments:
                attachment = msg.attachments[0]
                if not attachment.filename.lower().endswith(".dll"):
                    await ctx.send("Please upload a `.dll` file.")
                    return
                url = attachment.url
            elif msg.content.startswith("http"):
                url = msg.content.strip()
            else:
                await ctx.send(
                    "Please provide a valid URL or upload a `.dll` file. Or type `cancel` to abort."
                )
                return

            # Download the DLL
            status_msg = await ctx.send(
                embed=discord.Embed(
                    description="Downloading DLL...",
                    color=discord.Color.blurple(),
                )
            )

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=60)
                    ) as resp:
                        if resp.status != 200:
                            await status_msg.edit(
                                embed=discord.Embed(
                                    description=f"Download failed: HTTP {resp.status}",
                                    color=discord.Color.red(),
                                )
                            )
                            return

                        dll_content = await resp.read()

                        # Basic validation - check for MZ header (PE executable)
                        if not dll_content.startswith(b"MZ"):
                            await status_msg.edit(
                                embed=discord.Embed(
                                    description="The file doesn't appear to be a valid DLL.",
                                    color=discord.Color.red(),
                                )
                            )
                            return

                dll_size = len(dll_content) / (1024 * 1024)

                await status_msg.edit(
                    embed=discord.Embed(
                        description=f"DLL downloaded ({dll_size:.2f} MB). Processing basefiles...",
                        color=discord.Color.blurple(),
                    )
                )

                # Process each game's basefiles
                profiles = await self.config.profiles()
                results = []

                for game_id, profile in profiles.items():
                    basefiles_path = self._find_basefiles_path(game_id)
                    if not basefiles_path or not basefiles_path.exists():
                        results.append(f"**{profile['name']}**: Skipped (no basefiles)")
                        continue

                    try:
                        # Run in executor since this is blocking I/O
                        loop = asyncio.get_event_loop()
                        updated_count = await loop.run_in_executor(
                            None,
                            self._update_dll_in_archive,
                            basefiles_path,
                            dll_content,
                        )

                        if updated_count > 0:
                            results.append(
                                f"**{profile['name']}**: Updated {updated_count} file(s)"
                            )
                        else:
                            results.append(
                                f"**{profile['name']}**: No steamclient64.dll found"
                            )
                    except Exception as e:
                        log.exception(f"Failed to update DLL in {game_id}")
                        results.append(f"**{profile['name']}**: Error - {e}")

                # Show results
                embed = discord.Embed(
                    title="DLL Update Complete",
                    description="\n".join(results),
                    color=discord.Color.green(),
                )
                await status_msg.edit(embed=embed)

            except asyncio.TimeoutError:
                await status_msg.edit(
                    embed=discord.Embed(
                        description="Download timed out.",
                        color=discord.Color.red(),
                    )
                )
            except Exception as e:
                log.exception("Failed to update DLL")
                await status_msg.edit(
                    embed=discord.Embed(
                        description=f"Error: {e}",
                        color=discord.Color.red(),
                    )
                )

        except asyncio.TimeoutError:
            await ctx.send("Timed out. Run `[p]pubhelper updatedll` to try again.")

    def _update_dll_in_archive(self, archive_path: Path, dll_content: bytes) -> int:
        """Update steamclient64.dll in an archive. Returns count of updated files."""
        fmt = archive_path.suffix.lstrip(".")
        updated_count = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            extract_dir = tmpdir / "extracted"
            extract_dir.mkdir()

            # Extract archive
            if fmt == "7z":
                with py7zr.SevenZipFile(archive_path, "r") as z:
                    z.extractall(extract_dir)
            else:  # zip
                with zipfile.ZipFile(archive_path, "r") as z:
                    z.extractall(extract_dir)

            # Find and replace all steamclient64.dll files
            for dll_path in extract_dir.rglob("steamclient64.dll"):
                with open(dll_path, "wb") as f:
                    f.write(dll_content)
                updated_count += 1
                log.info(f"Updated {dll_path}")

            # Also check for case variations
            for dll_path in extract_dir.rglob("SteamClient64.dll"):
                with open(dll_path, "wb") as f:
                    f.write(dll_content)
                updated_count += 1
                log.info(f"Updated {dll_path}")

            if updated_count > 0:
                # Repack archive
                if fmt == "7z":
                    # Remove old archive and create new
                    archive_path.unlink()
                    with py7zr.SevenZipFile(archive_path, "w") as z:
                        for item in extract_dir.rglob("*"):
                            if item.is_file():
                                arcname = str(item.relative_to(extract_dir))
                                z.write(item, arcname)
                else:  # zip
                    archive_path.unlink()
                    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as z:
                        for item in extract_dir.rglob("*"):
                            if item.is_file():
                                arcname = str(item.relative_to(extract_dir))
                                z.write(item, arcname)

        return updated_count

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
        """Set the basefiles archive for a game.

        Supports both `.7z` and `.zip` formats.

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

                        # Detect archive format
                        fmt = _detect_archive_format(content)
                        if not fmt:
                            await ctx.send(
                                "The downloaded file is not a valid archive. Supported formats: `.7z`, `.zip`"
                            )
                            return

                        # Remove old basefiles if format changed
                        for old_fmt in ("7z", "zip"):
                            old_path = self._get_basefiles_path(game, old_fmt)
                            if old_path.exists() and old_fmt != fmt:
                                old_path.unlink()

                        basefiles_path = self._get_basefiles_path(game, fmt)
                        with open(basefiles_path, "wb") as f:
                            f.write(content)

                        # Validate archive structure
                        target_dir = str(Path(profile["config_target"]).parent)
                        try:
                            if fmt == "7z":
                                with py7zr.SevenZipFile(basefiles_path, "r") as z:
                                    names = z.getnames()
                            else:  # zip
                                with zipfile.ZipFile(basefiles_path, "r") as z:
                                    names = z.namelist()

                            if not any(target_dir in n for n in names):
                                await ctx.send(
                                    f"Warning: basefiles may not have the expected structure. "
                                    f"Expected path containing `{target_dir}`.\n"
                                    f"Use `[p]pubhelper setpath {game}` to change the config path."
                                )
                        except Exception as e:
                            await ctx.send(
                                f"Warning: Could not validate archive structure: {e}"
                            )

                        async with self.config.profiles() as profiles:
                            profiles[game]["basefiles_set"] = True
                            profiles[game]["basefiles_format"] = fmt

                        size_mb = len(content) / (1024 * 1024)
                        await ctx.send(
                            f"{profile['name']} basefiles saved successfully ({fmt}, {size_mb:.2f} MB). "
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
            basefiles_path = self._find_basefiles_path(game)
            is_set = profile.get("basefiles_set", False)
            fmt = profile.get("basefiles_format", "7z")

            if is_set and basefiles_path and basefiles_path.exists():
                size_mb = basefiles_path.stat().st_size / (1024 * 1024)
                status = f"Configured ({fmt}, {size_mb:.2f} MB)"
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
            name="Step 3: Sync Slash Commands",
            value=(
                "```\n[p]pubhelper syncslash\n```\n"
                "Or use Red's built-in:\n"
                "```\n[p]slash sync\n```"
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
                "`[p]pubhelper addgame` - Add a new game profile\n"
                "`[p]pubhelper removegame <id>` - Remove a game profile\n"
                "`[p]pubhelper updatedll` - Update steamclient64.dll in all basefiles\n"
                "`[p]pubhelper syncslash` - Sync slash commands to Discord\n"
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

        basefiles_path = self._find_basefiles_path(game)
        is_set = profile.get("basefiles_set", False)

        if not is_set or not basefiles_path or not basefiles_path.exists():
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
                None, self._combine_files, user_zip_data, game, profile, basefiles_path
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
        self, user_zip_data: bytes, game: str, profile: dict, basefiles_path: Path
    ) -> tuple[str, bytes] | str:
        """Combine user config with basefiles. Runs in executor."""
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

            # Extract basefiles based on format
            fmt = basefiles_path.suffix.lstrip(".")
            try:
                if fmt == "7z":
                    with py7zr.SevenZipFile(basefiles_path, "r") as z:
                        z.extractall(extract_dir)
                else:  # zip
                    with zipfile.ZipFile(basefiles_path, "r") as z:
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
