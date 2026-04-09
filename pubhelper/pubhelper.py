"""
PubHelper - Config Combiner Cog for Red-DiscordBot

Provides slash commands to combine user configs with basefiles for different games.
"""

import asyncio
import io
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp
import discord
import py7zr
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.data_manager import cog_data_path

from .savesigner import SAVE_PROFILES, SaveSigner

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
        "install_path": "Resident Evil 9/",
    },
    "cd": {
        "name": "CD",
        "config_target": "steam_settings/configs.user.ini",
        "output_name": "CD_Combined.zip",
        "description": "Crimson Desert",
        "basefiles_set": False,
        "basefiles_format": "7z",  # "7z" or "zip"
        "install_path": "Crimson Desert/bin64/",
    },
}

# MandarinJuice save signing profiles
SAVE_PROFILES = {
    "re9": {
        "name": "Resident Evil 9 Requiem",
        "profile": "Resident Evil 9 Requiem v1.bin",
    },
    "dd2": {
        "name": "Dragon's Dogma 2",
        "profile": "Dragon's Dogma 2 v1.bin",
    },
    "mhwilds": {
        "name": "Monster Hunter Wilds",
        "profile": "Monster Hunter Wilds v1.bin",
    },
    "kunitsu": {
        "name": "Kunitsu-Gami Path of the Goddess",
        "profile": "Kunitsu-Gami Path of the Goddess v1.bin",
    },
    "deadrising": {
        "name": "Dead Rising Deluxe Remaster",
        "profile": "Dead Rising Deluxe Remaster v1.bin",
    },
    "mhstories3": {
        "name": "Monster Hunter Stories 3 Twisted Reflection",
        "profile": "Monster Hunter Stories 3 Twisted Reflection v1.bin",
    },
    "megaman": {
        "name": "Mega Man Star Force Legacy Collection",
        "profile": "Mega Man Star Force Legacy Collection v1.bin",
    },
}

# Bruteforce timeouts
BRUTEFORCE_INLINE_TIMEOUT = 840  # 14 minutes - switch to DM mode
BRUTEFORCE_MAX_TIMEOUT = 3600  # 60 minutes - give up


def _extract_filename_from_url(url: str) -> str | None:
    """Extract filename from URL path.

    Returns the filename without query params, or None if not found.
    """
    try:
        parsed = urlparse(url)
        path = unquote(parsed.path)
        if path:
            filename = Path(path).name
            if filename and "." in filename:
                return filename
    except Exception:
        pass
    return None


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
            base_instructions_text=(
                "1. Extract the folder as well as the 2 files into the game folder\n"
                "   → For {game_name} into **{install_path}**\n\n"
                "2. Run **START_GAME.exe** as Administrator\n\n"
                "3. Let your bartender know if it works"
            ),
            base_instructions_image="https://cdn.discordapp.com/attachments/1483155606545367040/1486841498904563782/image.png",
            log_channel=None,  # Channel ID for logging command usage
            cli_log_channel=None,  # Channel ID for live CLI progress logs
        )
        self.data_path = cog_data_path(self)
        self.save_signer = SaveSigner(self.data_path)
        self.active_brutes: dict[int, asyncio.Task] = {}

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

    @pubhelper.command(name="setclilog")
    async def set_cli_log_channel(
        self, ctx: commands.Context, channel: discord.TextChannel = None
    ) -> None:
        """Set the channel for live CLI progress logs (e.g. savebrute).

        Leave blank to disable logging.
        """
        if channel:
            await self.config.cli_log_channel.set(channel.id)
            await ctx.send(f"CLI progress logs will now be sent to {channel.mention}.")
        else:
            await self.config.cli_log_channel.set(None)
            await ctx.send("CLI progress logging has been disabled.")

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
        - Install path (for instructions)
        """
        embed = discord.Embed(
            title="Add New Game",
            description=(
                "Let's add a new game profile!\n\n"
                "**Step 1/5:** Enter the game ID (lowercase, no spaces).\n"
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
                    "**Step 2/5:** Enter the display name for this game.\n"
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
                    "**Step 3/5:** Enter a short description (optional).\n"
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
                    "**Step 4/5:** Enter the config target path.\n"
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

            # Step 5: Install Path
            embed = discord.Embed(
                title="Add New Game",
                description=(
                    f"**Game ID:** `{game_id}`\n"
                    f"**Name:** {display_name}\n"
                    f"**Config Path:** `{config_path}`\n\n"
                    "**Step 5/5:** Enter the install path for instructions.\n"
                    "This tells users where to extract files in the game folder.\n\n"
                    "Examples:\n"
                    "- `Resident Evil 9/`\n"
                    "- `Crimson Desert/bin64/`\n\n"
                    "Type the path below, or `cancel` to abort:"
                ),
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)

            msg = await self.bot.wait_for("message", check=check, timeout=60)

            if msg.content.strip().lower() == "cancel":
                await ctx.send("Game setup cancelled.")
                return

            install_path = msg.content.strip()

            # Create the new profile
            new_profile = {
                "name": display_name,
                "config_target": config_path,
                "output_name": f"{display_name.replace(' ', '_')}_Combined.zip",
                "description": description,
                "basefiles_set": False,
                "basefiles_format": "7z",
                "install_path": install_path,
            }

            async with self.config.profiles() as profiles:
                profiles[game_id] = new_profile

            # Success message
            embed = discord.Embed(
                title="✅ Game Added Successfully!",
                description=(
                    f"**Game ID:** `{game_id}`\n"
                    f"**Name:** {display_name}\n"
                    f"**Description:** {description}\n"
                    f"**Config Path:** `{config_path}`\n"
                    f"**Install Path:** `{install_path}`\n\n"
                    "**Next steps:**\n"
                    f"1. Reload cog: `[p]reload pubhelper`\n"
                    f"2. Enable command: `[p]slash enable {game_id}cc`\n"
                    f"3. Sync slash: `[p]slash sync ~`\n"
                    f"4. Upload basefiles: `[p]pubhelper setup` and select {display_name}\n"
                    f"5. **📝 Set custom instructions (recommended):**\n"
                    f"   • `[p]pubhelper setinstructions {game_id} <text>`\n"
                    f"   • `[p]pubhelper setinstructionsimage {game_id} <url>`\n\n"
                    f"ℹ️ Until you set custom instructions, the game will use base/default instructions.\n\n"
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

    @pubhelper.command(name="setinstructions")
    async def set_instructions(
        self, ctx: commands.Context, game: str = None, *, text: str = None
    ) -> None:
        """Update installation instructions for a specific game or the base default.

        **Usage:**
        `[p]pubhelper setinstructions <game_id> <text>` - Set game-specific instructions
        `[p]pubhelper setinstructions base <text>` - Set base/default instructions
        `[p]pubhelper setinstructions <game_id>` - View game's current instructions
        `[p]pubhelper setinstructions` - View base instructions

        **Placeholders:**
        - `{game_name}` - Will be replaced with the game's display name
        - `{install_path}` - Will be replaced with the game's install path

        **Examples:**
        ```
        [p]pubhelper setinstructions re9 1. Extract to {install_path}
        2. Run the game
        3. Enjoy!
        ```

        ```
        [p]pubhelper setinstructions base 1. Extract files to {install_path}
        2. Run START_GAME.exe
        ```
        """
        profiles = await self.config.profiles()

        # No game specified - show guide and base instructions
        if not game:
            current = await self.config.base_instructions_text()
            current_image = await self.config.base_instructions_image()

            embed = discord.Embed(
                title="📝 Per-Game Instructions & Images",
                description=(
                    "**Each game can have custom instructions and images!**\n\n"
                    "Games without custom instructions use the **base/default** as fallback.\n\n"
                ),
                color=discord.Color.blurple(),
            )

            # Show available games
            game_list = ", ".join([f"`{g}`" for g in profiles.keys()])
            embed.add_field(
                name="Available Games",
                value=game_list,
                inline=False,
            )

            # How to set instructions
            embed.add_field(
                name="📄 Set Instructions Text",
                value=(
                    "**Set for specific game:**\n"
                    "`[p]pubhelper setinstructions <game> <text>`\n"
                    "Example: `[p]pubhelper setinstructions re9 1. Extract...`\n\n"
                    "**Set base/default:**\n"
                    "`[p]pubhelper setinstructions base <text>`\n\n"
                    "**View game's current:**\n"
                    "`[p]pubhelper setinstructions <game>`"
                ),
                inline=False,
            )

            # How to set images
            embed.add_field(
                name="🖼️ Set Instructions Image",
                value=(
                    "**Set for specific game:**\n"
                    "`[p]pubhelper setinstructionsimage <game> <url>`\n"
                    "Example: `[p]pubhelper setinstructionsimage cd https://i.imgur.com/...`\n\n"
                    "**Set base/default:**\n"
                    "`[p]pubhelper setinstructionsimage base <url>`\n\n"
                    "**Clear custom image:**\n"
                    "`[p]pubhelper setinstructionsimage <game> clear`"
                ),
                inline=False,
            )

            # Placeholders
            embed.add_field(
                name="✨ Placeholders (for text)",
                value=(
                    "`{game_name}` - Game display name\n`{install_path}` - Install path"
                ),
                inline=False,
            )

            # Current base
            embed.add_field(
                name="📋 Current Base Instructions",
                value=f"```\n{current[:500]}{'...' if len(current) > 500 else ''}\n```",
                inline=False,
            )

            if current_image:
                embed.add_field(
                    name="🖼️ Current Base Image",
                    value=f"[View Image]({current_image})",
                    inline=False,
                )

            embed.set_footer(
                text="💡 Tip: Set custom instructions when adding new games!"
            )
            await ctx.send(embed=embed)
            return

        game = game.lower()

        # Setting base instructions
        if game == "base":
            if not text:
                current = await self.config.base_instructions_text()
                embed = discord.Embed(
                    title="Base Installation Instructions (Default Fallback)",
                    description=f"```\n{current}\n```",
                    color=discord.Color.blue(),
                )
                embed.add_field(
                    name="Placeholders",
                    value="`{game_name}` - Game display name\n`{install_path}` - Install path",
                    inline=False,
                )
                await ctx.send(embed=embed)
                return

            await self.config.base_instructions_text.set(text)
            await ctx.send(
                embed=discord.Embed(
                    description="✅ Base instructions updated!\n\nPreview:\n"
                    + text.format(game_name="Example Game", install_path="Game/bin/"),
                    color=discord.Color.green(),
                )
            )
            return

        # Game-specific instructions
        if game not in profiles:
            await ctx.send(
                f"Unknown game `{game}`. Available: {', '.join(profiles.keys())}, base"
            )
            return

        profile = profiles[game]

        # View current instructions for this game
        if not text:
            custom_text = profile.get("instructions_text")
            if custom_text:
                embed = discord.Embed(
                    title=f"{profile['name']} - Custom Instructions",
                    description=f"```\n{custom_text}\n```",
                    color=discord.Color.green(),
                )
                embed.set_footer(text=f"Custom instructions for {game}")
            else:
                base_text = await self.config.base_instructions_text()
                embed = discord.Embed(
                    title=f"{profile['name']} - Using Base Instructions",
                    description=f"```\n{base_text}\n```",
                    color=discord.Color.orange(),
                )
                embed.set_footer(
                    text=f"No custom instructions set for {game}. Using base fallback."
                )

            embed.add_field(
                name="Placeholders",
                value="`{game_name}` - Game display name\n`{install_path}` - Install path",
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        # Set custom instructions for this game
        async with self.config.profiles() as profiles:
            profiles[game]["instructions_text"] = text

        await ctx.send(
            embed=discord.Embed(
                description=f"✅ Custom instructions set for **{profile['name']}**!\n\nPreview:\n"
                + text.format(
                    game_name=profile["name"],
                    install_path=profile.get("install_path", "Game folder"),
                ),
                color=discord.Color.green(),
            )
        )

    @pubhelper.command(name="setinstructionsimage")
    async def set_instructions_image(
        self, ctx: commands.Context, game: str = None, url: str = None
    ) -> None:
        """Update installation instructions image for a specific game or the base default.

        **Usage:**
        `[p]pubhelper setinstructionsimage <game_id> <url>` - Set game-specific image from URL
        `[p]pubhelper setinstructionsimage <game_id>` - Set from attached image
        `[p]pubhelper setinstructionsimage <game_id> clear` - Remove game's custom image
        `[p]pubhelper setinstructionsimage base <url>` - Set base/default image
        `[p]pubhelper setinstructionsimage` - Show guide

        **Examples:**
        ```
        [p]pubhelper setinstructionsimage re9 https://i.imgur.com/abc123.png
        [p]pubhelper setinstructionsimage cd clear
        [p]pubhelper setinstructionsimage base https://i.imgur.com/default.png
        ```

        **Or attach an image to your message:**
        ```
        [p]pubhelper setinstructionsimage re9
        [attach image]
        ```
        """
        profiles = await self.config.profiles()

        # Check for image attachment if no URL provided
        if not url and ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            # Verify it's an image
            if attachment.content_type and attachment.content_type.startswith("image/"):
                url = attachment.url
            else:
                await ctx.send("❌ The attachment must be an image file.")
                return

        # No game specified - show guide and base image
        if not game:
            current = await self.config.base_instructions_image()
            current_text = await self.config.base_instructions_text()

            embed = discord.Embed(
                title="🖼️ Per-Game Instructions Images",
                description=(
                    "**Each game can have a custom instructions image!**\n\n"
                    "Games without custom images use the **base/default** as fallback.\n\n"
                ),
                color=discord.Color.blurple(),
            )

            # Show available games
            game_list = ", ".join([f"`{g}`" for g in profiles.keys()])
            embed.add_field(
                name="Available Games",
                value=game_list,
                inline=False,
            )

            # How to set images
            embed.add_field(
                name="🖼️ Set Instructions Image",
                value=(
                    "**Set from URL:**\n"
                    "`[p]pubhelper setinstructionsimage <game> <url>`\n\n"
                    "**Set from attachment:**\n"
                    "`[p]pubhelper setinstructionsimage <game>` + attach image\n\n"
                    "**Set base/default:**\n"
                    "`[p]pubhelper setinstructionsimage base <url>`\n\n"
                    "**View game's current:**\n"
                    "`[p]pubhelper setinstructionsimage <game>`\n\n"
                    "**Clear custom image:**\n"
                    "`[p]pubhelper setinstructionsimage <game> clear`"
                ),
                inline=False,
            )

            # How to set text
            embed.add_field(
                name="📄 Set Instructions Text",
                value=(
                    "**Set for specific game:**\n"
                    "`[p]pubhelper setinstructions <game> <text>`\n\n"
                    "**Set base/default:**\n"
                    "`[p]pubhelper setinstructions base <text>`\n\n"
                    "Use `[p]pubhelper setinstructions` for full guide."
                ),
                inline=False,
            )

            if current:
                embed.add_field(
                    name="📋 Current Base Image",
                    value=f"[View Image]({current})\n`{current}`",
                    inline=False,
                )
                embed.set_image(url=current)
            else:
                embed.add_field(
                    name="📋 Current Base Image",
                    value="No base image set.",
                    inline=False,
                )

            embed.set_footer(text="💡 Tip: Combine custom text + image for each game!")
            await ctx.send(embed=embed)
            return

        game = game.lower()

        # Setting base image
        if game == "base":
            if not url:
                current = await self.config.base_instructions_image()
                if current:
                    embed = discord.Embed(
                        title="Base Instructions Image (Default Fallback)",
                        color=discord.Color.blue(),
                    )
                    embed.set_image(url=current)
                    embed.add_field(name="URL", value=current, inline=False)
                    await ctx.send(embed=embed)
                else:
                    await ctx.send("No base instructions image is currently set.")
                return

            if url.lower() == "clear":
                await self.config.base_instructions_image.set("")
                await ctx.send("✅ Base instructions image cleared.")
                return

            await self.config.base_instructions_image.set(url)
            embed = discord.Embed(
                description="✅ Base instructions image updated!",
                color=discord.Color.green(),
            )
            embed.set_image(url=url)
            await ctx.send(embed=embed)
            return

        # Game-specific image
        if game not in profiles:
            await ctx.send(
                f"Unknown game `{game}`. Available: {', '.join(profiles.keys())}, base"
            )
            return

        profile = profiles[game]

        # View current image for this game
        if not url:
            custom_image = profile.get("instructions_image")
            if custom_image:
                embed = discord.Embed(
                    title=f"{profile['name']} - Custom Instructions Image",
                    color=discord.Color.green(),
                )
                embed.set_image(url=custom_image)
                embed.add_field(name="URL", value=custom_image, inline=False)
                embed.set_footer(text=f"Custom image for {game}")
            else:
                base_image = await self.config.base_instructions_image()
                if base_image:
                    embed = discord.Embed(
                        title=f"{profile['name']} - Using Base Image",
                        color=discord.Color.orange(),
                    )
                    embed.set_image(url=base_image)
                    embed.add_field(name="URL", value=base_image, inline=False)
                    embed.set_footer(
                        text=f"No custom image set for {game}. Using base fallback."
                    )
                else:
                    await ctx.send(
                        f"**{profile['name']}** has no custom image, and no base image is set."
                    )
                    return
            await ctx.send(embed=embed)
            return

        # Clear custom image
        if url.lower() == "clear":
            async with self.config.profiles() as profiles:
                if "instructions_image" in profiles[game]:
                    del profiles[game]["instructions_image"]
            await ctx.send(
                f"✅ Custom image cleared for **{profile['name']}**. Will use base image as fallback."
            )
            return

        # Set custom image for this game
        async with self.config.profiles() as profiles:
            profiles[game]["instructions_image"] = url

        embed = discord.Embed(
            description=f"✅ Custom image set for **{profile['name']}**!",
            color=discord.Color.green(),
        )
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @pubhelper.command(name="logchannel")
    async def set_log_channel(
        self, ctx: commands.Context, channel: discord.TextChannel = None
    ) -> None:
        """Set the channel for logging slash command usage.

        **Usage:**
        `[p]pubhelper logchannel #channel` - Set log channel
        `[p]pubhelper logchannel` - Show current log channel
        `[p]pubhelper logchannel clear` - Disable logging

        **What gets logged:**
        - User who ran the command
        - Which game command was used (/re9cc, /cdcc, etc.)
        - Token URL provided
        - Timestamp
        - Success or failure status

        **Example:**
        ```
        [p]pubhelper logchannel #bot-logs
        ```
        """
        if channel is None and ctx.message.content.strip().endswith("clear"):
            # Clear log channel
            await self.config.log_channel.set(None)
            await ctx.send("✅ Command logging disabled.")
            return

        if channel is None:
            # Show current log channel
            channel_id = await self.config.log_channel()
            if channel_id:
                channel_obj = self.bot.get_channel(channel_id)
                if channel_obj:
                    await ctx.send(f"Current log channel: {channel_obj.mention}")
                else:
                    await ctx.send(
                        f"Log channel set to ID {channel_id}, but channel not found."
                    )
            else:
                await ctx.send("No log channel is currently set.")
            return

        # Set log channel
        await self.config.log_channel.set(channel.id)
        await ctx.send(
            f"✅ Command logging enabled. Logs will be sent to {channel.mention}"
        )

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

    @pubhelper.command(name="structure")
    async def show_structure(self, ctx: commands.Context, game: str) -> None:
        """Show the file structure of a game's basefiles.

        **Usage:**
        `[p]pubhelper structure <game_id>`

        **Examples:**
        `[p]pubhelper structure re9`
        `[p]pubhelper structure cd`
        `[p]pubhelper structure mhw`

        This displays the directory tree of the basefiles archive.
        """
        game = game.lower()
        profiles = await self.config.profiles()

        if game not in profiles:
            await ctx.send(
                f"Unknown game `{game}`. Available: {', '.join(profiles.keys())}"
            )
            return

        profile = profiles[game]
        basefiles_path = self._find_basefiles_path(game)
        is_set = profile.get("basefiles_set", False)

        if not is_set or not basefiles_path or not basefiles_path.exists():
            await ctx.send(
                f"{profile['name']} basefiles not configured. "
                f"Run `[p]pubhelper setup` to upload basefiles first."
            )
            return

        async with ctx.typing():
            try:
                loop = asyncio.get_event_loop()
                structure = await loop.run_in_executor(
                    None, self._get_archive_structure, basefiles_path
                )

                if isinstance(structure, str) and structure.startswith("Error:"):
                    await ctx.send(structure)
                    return

                # Create header with metadata
                size_mb = basefiles_path.stat().st_size / (1024 * 1024)
                fmt = basefiles_path.suffix.lstrip(".")
                header = (
                    f"{profile['name']} Basefiles Structure\n"
                    f"Format: {fmt.upper()}\n"
                    f"Size: {size_mb:.2f} MB\n"
                    f"Config Target: {profile['config_target']}\n"
                    f"\n{'=' * 60}\n\n"
                )

                content = header + structure

                # Send as text file
                file = discord.File(
                    io.BytesIO(content.encode("utf-8")),
                    filename=f"{game}_structure.txt",
                )

                await ctx.send(
                    f"📁 **{profile['name']} Basefiles Structure**", file=file
                )

            except Exception as e:
                log.exception("Error reading basefiles structure")
                await ctx.send(f"Error reading basefiles: {e}")

    def _get_archive_structure(self, archive_path: Path) -> str:
        """Get the file structure of an archive as a tree string."""
        try:
            fmt = archive_path.suffix.lstrip(".")
            files = []

            if fmt == "7z":
                with py7zr.SevenZipFile(archive_path, "r") as archive:
                    files = sorted(archive.getnames())
            else:  # zip
                with zipfile.ZipFile(archive_path, "r") as archive:
                    files = sorted(archive.namelist())

            if not files:
                return "Error: Archive is empty"

            # Build tree structure
            tree = {}
            for filepath in files:
                parts = Path(filepath).parts
                current = tree
                for part in parts:
                    if part not in current:
                        current[part] = {}
                    current = current[part]

            # Format as tree
            lines = []

            def format_tree(node, prefix="", is_last=True):
                items = sorted(node.items())
                for i, (name, children) in enumerate(items):
                    is_last_item = i == len(items) - 1
                    connector = "└── " if is_last_item else "├── "
                    lines.append(f"{prefix}{connector}{name}")

                    if children:
                        extension = "    " if is_last_item else "│   "
                        format_tree(children, prefix + extension, is_last_item)

            format_tree(tree)
            return "\n".join(lines)  # No limit - output to file

        except Exception as e:
            return f"Error: {e}"

    @pubhelper.command(name="pullbasefiles")
    async def pull_basefiles(self, ctx: commands.Context, game: str) -> None:
        """Export a game's basefiles archive via Discord.

        **Usage:**
        `[p]pubhelper pullbasefiles <game_id>`

        **Examples:**
        `[p]pubhelper pullbasefiles re9`
        `[p]pubhelper pullbasefiles cd`
        `[p]pubhelper pullbasefiles mhw`

        This uploads the basefiles archive as a Discord attachment for download.
        """
        game = game.lower()
        profiles = await self.config.profiles()

        if game not in profiles:
            await ctx.send(
                f"Unknown game `{game}`. Available: {', '.join(profiles.keys())}"
            )
            return

        profile = profiles[game]
        basefiles_path = self._find_basefiles_path(game)
        is_set = profile.get("basefiles_set", False)

        if not is_set or not basefiles_path or not basefiles_path.exists():
            await ctx.send(
                f"{profile['name']} basefiles not configured. "
                f"Run `[p]pubhelper setup` to upload basefiles first."
            )
            return

        # Check file size (Discord has limits)
        file_size_mb = basefiles_path.stat().st_size / (1024 * 1024)

        # Discord file size limit (8MB for non-boosted, 50MB for boosted servers)
        # We'll use a safe limit of 45MB to account for variations
        max_size_mb = 45

        if file_size_mb > max_size_mb:
            await ctx.send(
                f"⚠️ **File too large for Discord upload**\n\n"
                f"**{profile['name']}** basefiles: `{file_size_mb:.2f} MB`\n"
                f"Discord limit: `{max_size_mb} MB`\n\n"
                f"The basefiles are too large to upload directly via Discord. "
                f"Consider using an external file host or splitting the archive."
            )
            return

        async with ctx.typing():
            try:
                fmt = basefiles_path.suffix.lstrip(".")
                file = discord.File(
                    str(basefiles_path),
                    filename=f"{game}_basefiles.{fmt}",
                )

                embed = discord.Embed(
                    title=f"📦 {profile['name']} Basefiles",
                    color=discord.Color.green(),
                )
                embed.add_field(name="Game ID", value=f"`{game}`", inline=True)
                embed.add_field(name="Format", value=fmt.upper(), inline=True)
                embed.add_field(
                    name="Size", value=f"{file_size_mb:.2f} MB", inline=True
                )
                embed.add_field(
                    name="Config Target",
                    value=f"`{profile['config_target']}`",
                    inline=False,
                )

                await ctx.send(embed=embed, file=file)

            except discord.HTTPException as e:
                log.exception("Failed to upload basefiles")
                await ctx.send(
                    f"❌ **Failed to upload basefiles**\n\n"
                    f"Discord error: {e}\n\n"
                    f"The file may be too large for this server's upload limit."
                )
            except Exception as e:
                log.exception("Error exporting basefiles")
                await ctx.send(f"Error exporting basefiles: {e}")

    @pubhelper.command(name="ccini")
    async def show_ccini(self, ctx: commands.Context, game: str) -> None:
        """Show ColdClientLoader.ini from a game's basefiles.

        **Usage:**
        `[p]pubhelper ccini <game_id>`

        **Examples:**
        `[p]pubhelper ccini re9`
        `[p]pubhelper ccini cd`
        `[p]pubhelper ccini mhw`

        This extracts and displays the ColdClientLoader.ini file from the basefiles.
        """
        game = game.lower()
        profiles = await self.config.profiles()

        if game not in profiles:
            await ctx.send(
                f"Unknown game `{game}`. Available: {', '.join(profiles.keys())}"
            )
            return

        profile = profiles[game]
        basefiles_path = self._find_basefiles_path(game)
        is_set = profile.get("basefiles_set", False)

        if not is_set or not basefiles_path or not basefiles_path.exists():
            await ctx.send(
                f"{profile['name']} basefiles not configured. "
                f"Run `[p]pubhelper setup` to upload basefiles first."
            )
            return

        async with ctx.typing():
            try:
                loop = asyncio.get_event_loop()
                ini_content = await loop.run_in_executor(
                    None, self._extract_ccini, basefiles_path
                )

                if isinstance(ini_content, str) and ini_content.startswith("Error:"):
                    await ctx.send(ini_content)
                    return

                # Send as text file if content is too long, otherwise as code block
                if len(ini_content) > 1800:
                    file = discord.File(
                        io.BytesIO(ini_content.encode("utf-8")),
                        filename=f"{game}_ColdClientLoader.ini",
                    )
                    await ctx.send(
                        f"📄 **{profile['name']} - ColdClientLoader.ini**",
                        file=file,
                    )
                else:
                    await ctx.send(
                        f"📄 **{profile['name']} - ColdClientLoader.ini**\n```ini\n{ini_content}\n```"
                    )

            except Exception as e:
                log.exception("Error reading ColdClientLoader.ini")
                await ctx.send(f"Error reading ColdClientLoader.ini: {e}")

    def _extract_ccini(self, archive_path: Path) -> str:
        """Extract and return ColdClientLoader.ini content from archive."""
        try:
            fmt = archive_path.suffix.lstrip(".")

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

                # Find ColdClientLoader.ini
                ini_files = list(extract_dir.rglob("ColdClientLoader.ini"))

                if not ini_files:
                    return "Error: ColdClientLoader.ini not found in basefiles"

                if len(ini_files) > 1:
                    # Multiple found, list them
                    paths = [str(f.relative_to(extract_dir)) for f in ini_files]
                    return (
                        f"Error: Multiple ColdClientLoader.ini files found:\n"
                        + "\n".join(f"  - {p}" for p in paths)
                    )

                # Read the ini file
                ini_path = ini_files[0]
                with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                return content

        except Exception as e:
            return f"Error: {e}"

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
                "`[p]pubhelper structure <game>` - Show basefiles file structure\n"
                "`[p]pubhelper ccini <game>` - Show ColdClientLoader.ini\n"
                "`[p]pubhelper pullbasefiles <game>` - Export basefiles via Discord\n"
                "`[p]pubhelper logchannel #channel` - Set command usage log channel\n"
                "`[p]pubhelper setup` - Interactive basefiles setup\n"
                "`[p]pubhelper setpath` - Interactive path change\n"
                "`[p]pubhelper addgame` - Add a new game profile\n"
                "`[p]pubhelper removegame <id>` - Remove a game profile\n"
                "`[p]pubhelper updatedll` - Update steamclient64.dll in all basefiles\n"
                "`[p]pubhelper syncslash` - Sync slash commands to Discord\n"
                "`[p]pubhelper setinstructions <game|base>` - Set game or base instructions\n"
                "`[p]pubhelper setinstructionsimage <game|base>` - Set game or base image\n"
                "`[p]pubhelper help` - This guide"
            ),
            inline=False,
        )

        await ctx.send(embed=embed)

    @pubhelper.command(name="setuptool")
    @commands.is_owner()
    async def setuptool(self, ctx: commands.Context) -> None:
        """Download and install MandarinJuice CLI and game profiles."""
        await ctx.send("⏳ Downloading MandarinJuice CLI and profiles from GitHub...")

        tools_dir = self.data_path / "tools"
        profiles_dir = tools_dir / "profiles"
        tools_dir.mkdir(parents=True, exist_ok=True)
        profiles_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with aiohttp.ClientSession() as session:
                # Download Linux CLI binary
                async with session.get(
                    "https://github.com/Sablinova/Sablinova-Cogs/releases/download/mandarin-juice-promax-v1.0.0/mandarin-juice-promax.zip",
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        await ctx.send(f"❌ Failed to download CLI: HTTP {resp.status}")
                        return
                    cli_zip = await resp.read()

                # Download profiles
                async with session.get(
                    "https://github.com/mi5hmash/MandarinJuice/releases/download/v1.1.0/_profiles.zip",
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        await ctx.send(
                            f"❌ Failed to download profiles: HTTP {resp.status}"
                        )
                        return
                    profiles_zip = await resp.read()

            # Extract CLI
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)
                cli_zip_path = tmpdir_path / "cli.zip"
                cli_zip_path.write_bytes(cli_zip)

                with zipfile.ZipFile(cli_zip_path, "r") as zf:
                    zf.extractall(tmpdir_path / "cli")

                # Find the CLI binary in extracted files
                cli_binary = None
                for file in (tmpdir_path / "cli").rglob("mandarin-juice-promax"):
                    if file.is_file():
                        cli_binary = file
                        break

                if not cli_binary:
                    await ctx.send("❌ Could not find CLI binary in archive")
                    return

                # Copy CLI to tools directory
                target_cli = tools_dir / "mandarin-juice-promax"
                shutil.copy(cli_binary, target_cli)
                target_cli.chmod(0o755)  # Make executable

            # Extract profiles
            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir_path = Path(tmpdir)
                profiles_zip_path = tmpdir_path / "profiles.zip"
                profiles_zip_path.write_bytes(profiles_zip)

                with zipfile.ZipFile(profiles_zip_path, "r") as zf:
                    zf.extractall(tmpdir_path / "profiles")

                # Find the _profiles directory and copy contents
                for item in (tmpdir_path / "profiles").rglob("*.bin"):
                    shutil.copy(item, profiles_dir / item.name)

            # Verify installation
            available_profiles = self.save_signer.get_available_profiles()
            profile_list = (
                ", ".join(available_profiles) if available_profiles else "None"
            )

            await ctx.send(
                f"✅ **MandarinJuice ProMax CLI installed successfully!**\n\n"
                f"CLI: `{target_cli}`\n"
                f"Profiles: `{profiles_dir}`\n"
                f"Available games: {profile_list}"
            )

        except Exception as e:
            log.error(f"setuptool error: {e}", exc_info=True)
            await ctx.send(f"❌ Installation failed: {str(e)}")

    @pubhelper.command(name="toolstatus")
    async def toolstatus(self, ctx: commands.Context) -> None:
        """Check MandarinJuice CLI installation status."""
        tool_path = self.save_signer.get_tool_path()
        available_profiles = self.save_signer.get_available_profiles()

        if not tool_path:
            await ctx.send(
                "❌ **MandarinJuice CLI not installed**\n\n"
                "Run `[p]pubhelper setuptool` to install."
            )
            return

        profile_list = []
        for game_id in SAVE_PROFILES:
            profile_path = self.save_signer.get_profile_path(game_id)
            status = "✅" if profile_path else "❌"
            profile_list.append(
                f"{status} **{game_id}**: {SAVE_PROFILES[game_id]['name']}"
            )

        await ctx.send(
            f"✅ **MandarinJuice ProMax Installed**\n\n"
            f"**Game Profiles:**\n" + "\n".join(profile_list)
        )

    @pubhelper.command(name="cancelbrute")
    async def admin_cancelbrute(
        self, ctx: commands.Context, user: discord.Member
    ) -> None:
        """Cancel a specific user's active savebrute task.

        **Usage:**
        `[p]pubhelper cancelbrute <user>`
        """
        task = getattr(self, "active_brutes", {}).get(user.id)
        if task and not task.done():
            task.cancel()
            self.active_brutes.pop(user.id, None)
            await ctx.send(
                f"🛑 Successfully cancelled savebrute task for **{user.display_name}**."
            )
        else:
            await ctx.send(
                f"❌ **{user.display_name}** doesn't have any active savebrute tasks running."
            )

    @pubhelper.command(name="updateexe")
    async def update_exe(self, ctx: commands.Context, exe_link: str) -> None:
        """Update start_game.exe in all basefiles.

        Downloads the exe and updates it in all configured game basefiles,
        renaming it appropriately for each game (start_re9.exe, start_cd.exe, etc.).

        Args:
            exe_link: Direct download link to the new start_game.exe
        """
        msg = await ctx.send("⏳ Downloading exe...")

        try:
            # Download the exe
            download_result = await self._download_file(exe_link)
            if isinstance(download_result, str):
                await msg.edit(content=f"❌ Download failed: {download_result}")
                return

            exe_data = download_result

            # Get all configured profiles
            profiles = await self.config.profiles()
            updated_games = []
            failed_games = []
            skipped_games = []

            await msg.edit(content="⏳ Updating basefiles...")

            for game_id, profile in profiles.items():
                # Skip if basefiles not set
                if not profile.get("basefiles_set", False):
                    skipped_games.append(f"{game_id} (no basefiles)")
                    continue

                basefiles_path = self._find_basefiles_path(game_id)
                if not basefiles_path or not basefiles_path.exists():
                    skipped_games.append(f"{game_id} (basefiles missing)")
                    continue

                try:
                    # Get old file size
                    old_size_mb = basefiles_path.stat().st_size / (1024 * 1024)

                    # Determine the target exe name
                    new_exe_name = f"start_{game_id}.exe"
                    fmt = basefiles_path.suffix.lstrip(".")

                    # Create a temporary directory for processing
                    with tempfile.TemporaryDirectory() as temp_dir:
                        temp_path = Path(temp_dir)
                        extract_path = temp_path / "extracted"
                        extract_path.mkdir()

                        # Extract basefiles
                        if fmt == "7z":
                            with py7zr.SevenZipFile(basefiles_path, "r") as z:
                                z.extractall(extract_path)
                        else:
                            with zipfile.ZipFile(basefiles_path, "r") as z:
                                z.extractall(extract_path)

                        # Find and replace any existing start_*.exe files (case-insensitive)
                        exe_found = False
                        for exe_file in extract_path.rglob("*.exe"):
                            if exe_file.name.lower().startswith("start_"):
                                exe_file.unlink()  # Remove old exe
                                exe_found = True

                        # Write new exe with game-specific name
                        # Place it in the root of extracted files
                        new_exe_path = extract_path / new_exe_name
                        with open(new_exe_path, "wb") as f:
                            f.write(exe_data)

                        # Repackage the basefiles
                        if fmt == "7z":
                            with py7zr.SevenZipFile(basefiles_path, "w") as z:
                                for item in extract_path.rglob("*"):
                                    if item.is_file():
                                        arcname = item.relative_to(extract_path)
                                        z.write(item, arcname)
                        else:
                            with zipfile.ZipFile(
                                basefiles_path, "w", zipfile.ZIP_DEFLATED
                            ) as z:
                                for item in extract_path.rglob("*"):
                                    if item.is_file():
                                        arcname = item.relative_to(extract_path)
                                        z.write(item, arcname)

                        # Get new file size
                        new_size_mb = basefiles_path.stat().st_size / (1024 * 1024)
                        size_diff = new_size_mb - old_size_mb
                        size_sign = "+" if size_diff > 0 else ""

                        updated_games.append(
                            f"✅ {game_id} → `{new_exe_name}` "
                            f"({old_size_mb:.1f} MB → {new_size_mb:.1f} MB, {size_sign}{size_diff:.1f} MB)"
                        )

                except Exception as e:
                    failed_games.append(f"❌ {game_id}: {str(e)}")
                    log.exception(f"Failed to update exe in {game_id} basefiles")

            # Build result message
            result_parts = []
            if updated_games:
                result_parts.append("**Updated:**\n" + "\n".join(updated_games))
            if failed_games:
                result_parts.append("\n**Failed:**\n" + "\n".join(failed_games))
            if skipped_games:
                result_parts.append("\n**Skipped:**\n" + "\n".join(skipped_games))

            if not result_parts:
                await msg.edit(content="⚠️ No games to update (no basefiles configured)")
            else:
                await msg.edit(content="\n".join(result_parts))

        except Exception as e:
            await msg.edit(content=f"❌ Error: {str(e)}")
            log.exception("Error in update_exe command")

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

        # Extract filename from URL to use as output name
        url_filename = _extract_filename_from_url(url)

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

            _, data = result
            # Use URL filename if available, otherwise fallback to profile output_name
            output_filename = url_filename if url_filename else profile["output_name"]
            file = discord.File(io.BytesIO(data), filename=output_filename)
            size_mb = len(data) / (1024 * 1024)

            await interaction.edit_original_response(
                embed=discord.Embed(
                    description=f"Your combined {profile['name']} package is ready! ({size_mb:.2f} MB)",
                    color=discord.Color.green(),
                ),
                attachments=[file],
            )

            # Send instructions as a follow-up message
            install_path = profile.get("install_path", "the game folder")

            # Get instructions (per-game with fallback to base)
            instructions_text = profile.get("instructions_text")
            if not instructions_text:
                instructions_text = await self.config.base_instructions_text()

            instructions_image = profile.get("instructions_image")
            if not instructions_image:
                instructions_image = await self.config.base_instructions_image()

            # Format the text with game-specific values
            formatted_text = instructions_text.format(
                game_name=profile["name"], install_path=install_path
            )

            instructions_embed = discord.Embed(
                title="Installation Instructions",
                description=formatted_text,
                color=discord.Color.blue(),
            )

            if instructions_image:
                instructions_embed.set_image(url=instructions_image)

            await interaction.followup.send(embed=instructions_embed)

            # Log successful command usage
            await self._log_command_usage(
                interaction=interaction,
                game=game,
                url=url,
                success=True,
                output_filename=output_filename,
                size_mb=size_mb,
            )

        except Exception as e:
            log.exception("Error processing config")
            await interaction.edit_original_response(
                embed=discord.Embed(
                    description=f"Error: {e}\n\n{INVALID_LINK_MSG}",
                    color=discord.Color.red(),
                )
            )
            # Log failed command usage
            await self._log_command_usage(
                interaction=interaction, game=game, url=url, success=False, error=str(e)
            )

    async def _log_command_usage(
        self,
        interaction: discord.Interaction,
        game: str,
        url: str,
        success: bool,
        output_filename: str = None,
        size_mb: float = None,
        error: str = None,
    ) -> None:
        """Log slash command usage to the configured log channel."""
        try:
            log_channel_id = await self.config.log_channel()
            if not log_channel_id:
                return  # Logging disabled

            log_channel = self.bot.get_channel(log_channel_id)
            if not log_channel:
                return  # Channel not found

            profiles = await self.config.profiles()
            profile = profiles.get(game, {})
            game_name = profile.get("name", game.upper())

            # Create log embed
            if success:
                embed = discord.Embed(
                    title=f"✅ {game_name} Command Used",
                    color=discord.Color.green(),
                    timestamp=interaction.created_at,
                )
                embed.add_field(name="Output File", value=output_filename, inline=False)
                embed.add_field(name="Size", value=f"{size_mb:.2f} MB", inline=True)
            else:
                embed = discord.Embed(
                    title=f"❌ {game_name} Command Failed",
                    color=discord.Color.red(),
                    timestamp=interaction.created_at,
                )
                embed.add_field(
                    name="Error", value=f"```{error[:1000]}```", inline=False
                )

            # Common fields
            embed.add_field(
                name="User",
                value=f"{interaction.user.mention} ({interaction.user})",
                inline=True,
            )
            embed.add_field(name="Command", value=f"`/{game}cc`", inline=True)

            # Truncate URL if too long
            url_display = url if len(url) < 100 else url[:97] + "..."
            embed.add_field(name="Token URL", value=url_display, inline=False)

            if interaction.guild:
                embed.add_field(
                    name="Server", value=interaction.guild.name, inline=True
                )
                embed.add_field(
                    name="Channel", value=interaction.channel.mention, inline=True
                )

            await log_channel.send(embed=embed)

        except Exception as e:
            log.exception(f"Failed to log command usage: {e}")

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

    @app_commands.command(
        name="savebrute",
        description="Bruteforce save User ID and re-sign to your Steam ID",
    )
    @app_commands.describe(
        game="Select game",
        new_id="Your Steam ID to sign saves to",
        link="URL to save archive (zip/7z)",
    )
    @app_commands.choices(
        game=[
            app_commands.Choice(name=profile["name"], value=game_id)
            for game_id, profile in SAVE_PROFILES.items()
        ]
    )
    async def savebrute(
        self, interaction: discord.Interaction, game: str, new_id: str, link: str
    ) -> None:
        """Bruteforce User ID from save and re-sign to new ID."""
        await interaction.response.defer(thinking=True)

        # Check if tool is installed
        if not self.save_signer.is_tool_installed():
            await interaction.followup.send(
                "❌ MandarinJuice CLI is not installed. Please ask an admin to run `[p]pubhelper setuptool`",
                ephemeral=True,
            )
            return

        # Check if profile exists
        if not self.save_signer.get_profile_path(game):
            await interaction.followup.send(
                f"❌ Game profile for {game} not found.", ephemeral=True
            )
            return

        # Download archive
        result = await self._download_file(link)
        if isinstance(result, str):
            await interaction.followup.send(f"❌ Download failed: {result}")
            return

        save_archive = result

        if interaction.user.id in getattr(self, "active_brutes", {}):
            task = self.active_brutes[interaction.user.id]
            if not task.done():
                await interaction.followup.send(
                    "❌ You already have a savebrute running. Use `/cancelbrute` to stop it first.",
                    ephemeral=True,
                )
                return

        # Send initial message
        await interaction.followup.send(
            f"⏳ Bruteforcing User ID for **{SAVE_PROFILES[game]['name']}**...\n"
            f"_This may take 1-2 hours for large Steam saves. I'll update you when done!_"
        )

        # Create background task with timeout
        task = asyncio.create_task(
            self._savebrute_task(interaction, game, new_id, save_archive)
        )
        self.active_brutes[interaction.user.id] = task

    async def _savebrute_task(
        self,
        interaction: discord.Interaction,
        game: str,
        new_id: str,
        save_archive: bytes,
    ):
        """Background task for savebrute with timeout handling."""
        start_time = asyncio.get_event_loop().time()
        inline_timeout = 840  # 14 minutes
        max_timeout = 7200  # 120 minutes

        log_channel_id = await self.config.log_channel()
        fallback_channel = (
            self.bot.get_channel(log_channel_id) if log_channel_id else None
        )

        cli_log_channel_id = await self.config.cli_log_channel()
        cli_log_channel = (
            self.bot.get_channel(cli_log_channel_id) if cli_log_channel_id else None
        )

        async def send_final_message(content, file=None):
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed < inline_timeout:
                try:
                    kwargs = {"content": content}
                    if file:
                        kwargs["attachments"] = [file]
                    await interaction.edit_original_response(**kwargs)
                    return
                except Exception as e:
                    log.warning(f"Failed to edit original response: {e}")

            # Interaction expired or failed, try DM
            try:
                kwargs = {"content": content}
                if file:
                    kwargs["file"] = file
                await interaction.user.send(**kwargs)
                return
            except discord.Forbidden as e:
                log.warning(f"Failed to send DM to {interaction.user}: {e}")

                # Fallback chain: command channel -> general log channel -> cli log channel
                fallback_channels = []
                if interaction.channel and hasattr(interaction.channel, "send"):
                    fallback_channels.append(interaction.channel)
                if fallback_channel:
                    fallback_channels.append(fallback_channel)
                if cli_log_channel:
                    fallback_channels.append(cli_log_channel)

                sent = False
                for ch in fallback_channels:
                    kwargs = {"content": f"{interaction.user.mention} {content}"}
                    if file and hasattr(file, "fp"):
                        file.fp.seek(0)
                    if file:
                        kwargs["file"] = file
                    try:
                        await ch.send(**kwargs)
                        sent = True
                        break
                    except Exception as e2:
                        log.error(f"Failed to send to fallback channel {ch.id}: {e2}")
                        continue

                if not sent:
                    log.error(
                        f"Failed to deliver savebrute results to {interaction.user} via any fallback channel."
                    )
            except Exception as e:
                log.error(f"Unexpected error sending DM: {e}")

        log_queue = asyncio.Queue()

        async def progress_callback(line: str):
            await log_queue.put(line)

        progress_task = None
        log_message = None

        if cli_log_channel:
            try:
                log_message = await cli_log_channel.send(
                    f"🔄 **Savebrute started for {interaction.user.name}**\nGame: {SAVE_PROFILES[game]['name']}\n```\nWaiting for logs...\n```"
                )
            except Exception as e:
                log.error(f"Failed to send initial log message: {e}")
                cli_log_channel = None

        log_buffer = []

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
                    break

                if log_buffer and cli_log_channel and log_message:
                    log_text = "\n".join(log_buffer[-10:])
                    try:
                        await log_message.edit(
                            content=f"🔄 **Savebrute running for {interaction.user.name}**\nGame: {SAVE_PROFILES[game]['name']}\n```\n{log_text}\n```"
                        )
                    except Exception as e:
                        log.warning(f"Failed to update log message: {e}")

        if cli_log_channel:
            progress_task = asyncio.create_task(log_updater())

        success = False
        try:
            brute_task = asyncio.create_task(
                self.save_signer.run_bruteforce(game, save_archive, progress_callback)
            )

            try:
                brute_result = await asyncio.wait_for(
                    brute_task, timeout=inline_timeout
                )
            except asyncio.TimeoutError:
                try:
                    await interaction.edit_original_response(
                        content=(
                            f"⏳ Still bruteforcing **{SAVE_PROFILES[game]['name']}**...\n"
                            f"_This can take up to 2 hours for Steam saves. I will DM you the files when done!_"
                        )
                    )
                except Exception:
                    pass

                remaining_time = max_timeout - inline_timeout
                try:
                    brute_result = await asyncio.wait_for(
                        brute_task, timeout=remaining_time
                    )
                except asyncio.TimeoutError:
                    brute_task.cancel()
                    await send_final_message(
                        f"❌ **Savebrute Timed Out**\n\n"
                        f"Game: {SAVE_PROFILES[game]['name']}\n"
                        f"Bruteforce exceeded 120 minutes. This appears to be a very large Steam save.\n"
                        f"Please find your Steam64 ID manually and use `/savesign` instead."
                    )
                    return

            if brute_result is None:
                await send_final_message(
                    f"❌ **Bruteforce Failed**\n\n"
                    f"Game: {SAVE_PROFILES[game]['name']}\n"
                    f"Could not find User ID. Make sure the archive contains save files."
                )
                return

            found_id = brute_result["user_id"]

            await send_final_message(
                f"✅ **Found User ID: `{found_id}`**\n\nRe-signing to `{new_id}`..."
            )

            resign_result = await self.save_signer.run_resign(
                game, save_archive, found_id, new_id
            )

            if resign_result is None:
                await send_final_message(
                    f"❌ **Re-sign Failed**\n\nFound ID: `{found_id}`\nCould not re-sign saves."
                )
                return

            zip_file = discord.File(
                io.BytesIO(resign_result), filename=f"{game}_resigned.zip"
            )
            await send_final_message(
                f"✅ **Savebrute Complete!**\n\n"
                f"Game: {SAVE_PROFILES[game]['name']}\n"
                f"Original ID: `{found_id}` → New ID: `{new_id}`",
                file=zip_file,
            )
            success = True

        except asyncio.CancelledError:
            await send_final_message(f"🛑 **Savebrute Cancelled manually by user.**")
        except Exception as e:
            log.error(f"Savebrute error: {e}", exc_info=True)
            await send_final_message(f"❌ **Error**: {str(e)}")
        finally:
            if (
                getattr(self, "active_brutes", {}).get(interaction.user.id)
                == asyncio.current_task()
            ):
                self.active_brutes.pop(interaction.user.id, None)

            if progress_task:
                progress_task.cancel()
                if cli_log_channel and log_message:
                    try:
                        icon = "✅" if success else "❌"
                        status_text = "finished" if success else "failed"
                        final_logs = (
                            "\n".join(log_buffer[-10:])
                            if log_buffer
                            else "No logs produced."
                        )
                        await log_message.edit(
                            content=f"{icon} **Savebrute {status_text} for {interaction.user.name}**\nGame: {SAVE_PROFILES[game]['name']}\n```\n{final_logs}\n```"
                        )
                    except Exception:
                        pass

    @app_commands.command(
        name="cancelbrute",
        description="Cancel your currently running savebrute task",
    )
    async def cancelbrute(self, interaction: discord.Interaction) -> None:
        task = getattr(self, "active_brutes", {}).get(interaction.user.id)
        if task and not task.done():
            task.cancel()
            self.active_brutes.pop(interaction.user.id, None)
            await interaction.response.send_message(
                "🛑 Successfully sent cancellation signal to your savebrute task. It will stop shortly.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ You don't have any active savebrute tasks running.", ephemeral=True
            )

    @app_commands.command(
        name="savesign",
        description="Re-sign save files to a new Steam ID",
    )
    @app_commands.describe(
        game="Select game",
        old_id="Original User ID",
        new_id="Your Steam ID to sign saves to",
        link="URL to save archive (zip/7z)",
    )
    @app_commands.choices(
        game=[
            app_commands.Choice(name=profile["name"], value=game_id)
            for game_id, profile in SAVE_PROFILES.items()
        ]
    )
    async def savesign(
        self,
        interaction: discord.Interaction,
        game: str,
        old_id: str,
        new_id: str,
        link: str,
    ) -> None:
        """Re-sign save files to a new User ID."""
        await interaction.response.defer(thinking=True)

        # Check if tool is installed
        if not self.save_signer.is_tool_installed():
            await interaction.followup.send(
                "❌ MandarinJuice CLI is not installed. Please ask an admin to run `[p]pubhelper setuptool`",
                ephemeral=True,
            )
            return

        # Check if profile exists
        if not self.save_signer.get_profile_path(game):
            await interaction.followup.send(
                f"❌ Game profile for {game} not found.", ephemeral=True
            )
            return

        # Download archive
        result = await self._download_file(link)
        if isinstance(result, str):
            await interaction.followup.send(f"❌ Download failed: {result}")
            return

        save_archive = result

        # Send initial message
        await interaction.followup.send(
            f"⏳ Re-signing saves for **{SAVE_PROFILES[game]['name']}**..."
        )

        # Run re-sign
        resign_result = await self.save_signer.run_resign(
            game, save_archive, old_id, new_id
        )

        if resign_result is None:
            await interaction.edit_original_response(
                content=f"❌ Re-sign failed. Make sure the archive contains valid save files."
            )
            return

        # Success - send zip
        success_msg = (
            f"✅ **Re-sign Complete!**\n\n"
            f"Game: {SAVE_PROFILES[game]['name']}\n"
            f"Original ID: `{old_id}` → New ID: `{new_id}`"
        )
        zip_file = discord.File(
            io.BytesIO(resign_result), filename=f"{game}_resigned.zip"
        )

        await interaction.edit_original_response(
            content=success_msg, attachments=[zip_file]
        )

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
