import asyncio

import discord
from redbot.core import commands, Config, modlog
import logging
from typing import Optional

log = logging.getLogger("red.sablinova.sabhoneypot")

# AAA3A Honeypot config constants (for migration)
AAA3A_HONEYPOT_IDENTIFIER = 205192943327321000143939875896557571750
AAA3A_HONEYPOT_COG_NAME = "Honeypot"
AAA3A_HONEYPOT_DEFAULTS = {
    "enabled": False,
    "action": None,
    "logs_channel": None,
    "ping_role": None,
    "honeypot_channel": None,
    "mute_role": None,
    "ban_delete_message_days": 3,
}


class SabHoneypot(commands.Cog):
    """Trap channel to catch self-bots and scammers."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=7832019465, force_registration=True
        )
        self.config.register_guild(
            enabled=False,
            action=None,
            logs_channel=None,
            ping_role=None,
            honeypot_channel=None,
            mute_role=None,
            warning_text=(
                "\U0001f6a8 **__WARNING!__** \U0001f6a8\n"
                "# \u2757 **DO NOT SEND A MESSAGE HERE** \u2757\n"
                "> \U0001f41d This is a **HONEYPOT** for bots.\n"
                "> \U0001f6ab Sending a message here will result in an **instant ban**.\n\n"
                "\U0001f512 **This channel is monitored.**\n"
                "\U0001f440 If you're human, move along.\n"
                "\U0001f916 If you're a bot... well, thanks for making it easy.\n\n"
                "\U0001f6d1 **FINAL WARNING: Do NOT type anything here.** \U0001f6d1"
            ),
            warning_image=None,
            warning_message_id=None,
            kick_delete_days=1,
            ban_delete_days=3,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _build_warning_embed(self, guild: discord.Guild) -> discord.Embed:
        """Build the warning embed for the honeypot channel."""
        config = await self.config.guild(guild).all()
        warning_text = config["warning_text"]
        warning_image = config["warning_image"]

        embed = discord.Embed(
            description=warning_text,
            color=discord.Color.red(),
        )
        if warning_image:
            embed.set_image(url=warning_image)
        embed.set_footer(text="Powered by SabHoneypot")
        return embed

    # ------------------------------------------------------------------
    # Command group
    # ------------------------------------------------------------------

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def sabhoneypot(self, ctx):
        """Manage the honeypot trap channel."""
        pass

    # ------------------------------------------------------------------
    # Interactive setup wizard
    # ------------------------------------------------------------------

    @sabhoneypot.command(name="setup")
    @commands.bot_has_guild_permissions(manage_channels=True, ban_members=True)
    async def honeypot_setup(self, ctx):
        """Interactive guided setup. Walks you through full configuration step by step."""
        TIMEOUT = 60.0
        cancelled_msg = "Setup cancelled."

        def wait_check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        async def ask(prompt: str) -> Optional[discord.Message]:
            """Send a prompt and wait for a reply. Returns None if cancelled/timed out."""
            await ctx.send(prompt)
            try:
                reply = await self.bot.wait_for(
                    "message", check=wait_check, timeout=TIMEOUT
                )
            except asyncio.TimeoutError:
                await ctx.send(
                    "Setup timed out. Run `sabhoneypot setup` to start over."
                )
                return None
            if reply.content.lower() in ("cancel", "stop", "exit"):
                await ctx.send(cancelled_msg)
                return None
            return reply

        # --- Welcome ---
        await ctx.send(
            "**SabHoneypot Setup**\n"
            "I'll walk you through configuring the honeypot step by step.\n"
            "Type `cancel` at any point to abort.\n" + "\u2500" * 40
        )

        # --- Step 1: Honeypot Channel ---
        reply = await ask(
            "**Step 1/5 \u2014 Honeypot Channel**\n"
            "Mention an existing channel (e.g. `#honeypot`) to use as the trap, "
            "or type `create` to make a new one."
        )
        if reply is None:
            return

        honeypot_channel = None
        created_channel = False
        if reply.content.lower().strip() == "create":
            # Create the channel (reuse createchannel logic)
            overwrites = {
                ctx.guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    read_messages=True,
                    send_messages=True,
                    manage_messages=True,
                    manage_channels=True,
                ),
                ctx.guild.default_role: discord.PermissionOverwrite(
                    view_channel=True,
                    read_messages=True,
                    send_messages=True,
                ),
            }
            try:
                honeypot_channel = await ctx.guild.create_text_channel(
                    name="\U0001f36f",
                    overwrites=overwrites,
                    position=0,
                    reason="Honeypot trap channel — setup wizard",
                )
                created_channel = True
                embed = await self._build_warning_embed(ctx.guild)
                warning_msg = await honeypot_channel.send(embed=embed)
                await self.config.guild(ctx.guild).warning_message_id.set(
                    warning_msg.id
                )
                await ctx.send(f"Created {honeypot_channel.mention}.")
            except discord.HTTPException as e:
                await ctx.send(f"Failed to create channel: `{e}`\nSetup cancelled.")
                return
        else:
            # Try to resolve a mentioned channel
            if reply.channel_mentions:
                honeypot_channel = reply.channel_mentions[0]
            else:
                # Try by name or ID
                try:
                    honeypot_channel = await commands.TextChannelConverter().convert(
                        ctx, reply.content.strip()
                    )
                except commands.BadArgument:
                    await ctx.send("Could not find that channel. Setup cancelled.")
                    return
            await ctx.send(f"Using {honeypot_channel.mention} as the honeypot channel.")

        await self.config.guild(ctx.guild).honeypot_channel.set(honeypot_channel.id)

        # --- Step 2: Log Channel ---
        reply = await ask(
            "**Step 2/5 \u2014 Log Channel**\n"
            "Mention the channel where detection logs should be sent (e.g. `#mod-logs`)."
        )
        if reply is None:
            return

        if reply.channel_mentions:
            logs_channel = reply.channel_mentions[0]
        else:
            try:
                logs_channel = await commands.TextChannelConverter().convert(
                    ctx, reply.content.strip()
                )
            except commands.BadArgument:
                await ctx.send("Could not find that channel. Setup cancelled.")
                return

        await self.config.guild(ctx.guild).logs_channel.set(logs_channel.id)
        await ctx.send(f"Log channel set to {logs_channel.mention}.")

        # --- Step 3: Action ---
        reply = await ask(
            "**Step 3/5 \u2014 Action**\n"
            "What should happen when someone posts in the honeypot?\n"
            "`ban` \u2014 Permanent ban and delete recent messages\n"
            "`kick` \u2014 Softban (kick + delete recent messages)\n"
            "`mute` \u2014 Assign a mute role\n"
            "`none` \u2014 Log only, no moderation action"
        )
        if reply is None:
            return

        action = reply.content.lower().strip()
        if action not in ("ban", "kick", "mute", "none"):
            await ctx.send(
                "Invalid action. Must be `ban`, `kick`, `mute`, or `none`. Setup cancelled."
            )
            return

        action_value = None if action == "none" else action
        await self.config.guild(ctx.guild).action.set(action_value)
        await ctx.send(f"Action set to **{action}**.")

        # --- Step 3b: Mute Role (only if mute) ---
        if action == "mute":
            reply = await ask(
                "**Step 3b \u2014 Mute Role**\n"
                "Mention the role to assign when muting (e.g. `@Muted`)."
            )
            if reply is None:
                return

            if reply.role_mentions:
                mute_role = reply.role_mentions[0]
            else:
                try:
                    mute_role = await commands.RoleConverter().convert(
                        ctx, reply.content.strip()
                    )
                except commands.BadArgument:
                    await ctx.send("Could not find that role. Setup cancelled.")
                    return

            await self.config.guild(ctx.guild).mute_role.set(mute_role.id)
            await ctx.send(f"Mute role set to **{mute_role.name}**.")

        # --- Step 4: Ping Role ---
        reply = await ask(
            "**Step 4/5 \u2014 Ping Role** (optional)\n"
            "Mention a role to ping on each detection (e.g. `@Moderator`), "
            "or type `skip` to skip."
        )
        if reply is None:
            return

        if reply.content.lower().strip() == "skip":
            await ctx.send("Ping role skipped.")
        else:
            if reply.role_mentions:
                ping_role = reply.role_mentions[0]
            else:
                try:
                    ping_role = await commands.RoleConverter().convert(
                        ctx, reply.content.strip()
                    )
                except commands.BadArgument:
                    await ctx.send("Could not find that role. Skipping ping role.")
                    ping_role = None

            if ping_role:
                await self.config.guild(ctx.guild).ping_role.set(ping_role.id)
                await ctx.send(f"Ping role set to **{ping_role.name}**.")

        # --- Step 5: Enable ---
        reply = await ask(
            "**Step 5/5 \u2014 Enable**\n"
            "Everything is configured. Enable the honeypot now?\n"
            "Type `yes` or `no`."
        )
        if reply is None:
            return

        if reply.content.lower().strip() in ("yes", "y"):
            await self.config.guild(ctx.guild).enabled.set(True)
            await ctx.send("Honeypot trap is now **enabled**.")
        else:
            await ctx.send(
                "Honeypot is configured but **not enabled**. "
                "Run `sabhoneypot enable` when ready."
            )

        # --- Summary ---
        config = await self.config.guild(ctx.guild).all()
        action_display = config["action"] or "none (log-only)"
        hp = ctx.guild.get_channel(config["honeypot_channel"])
        lc = ctx.guild.get_channel(config["logs_channel"])

        summary = discord.Embed(
            title="Setup Complete",
            color=discord.Color.green(),
        )
        summary.add_field(
            name="Honeypot Channel", value=hp.mention if hp else "Unknown", inline=True
        )
        summary.add_field(
            name="Log Channel", value=lc.mention if lc else "Unknown", inline=True
        )
        summary.add_field(name="Action", value=action_display, inline=True)
        summary.add_field(name="Enabled", value=str(config["enabled"]), inline=True)

        ping_role_id = config["ping_role"]
        if ping_role_id:
            pr = ctx.guild.get_role(ping_role_id)
            summary.add_field(
                name="Ping Role", value=pr.mention if pr else "Not set", inline=True
            )

        mute_role_id = config["mute_role"]
        if mute_role_id:
            mr = ctx.guild.get_role(mute_role_id)
            summary.add_field(
                name="Mute Role", value=mr.mention if mr else "Not set", inline=True
            )

        summary.set_footer(text="Use individual commands to adjust settings later.")
        await ctx.send(embed=summary)

    # ------------------------------------------------------------------
    # Channel management
    # ------------------------------------------------------------------

    @sabhoneypot.command(name="createchannel")
    @commands.bot_has_guild_permissions(manage_channels=True)
    async def honeypot_createchannel(self, ctx):
        """Create a new honeypot trap channel at the top of the server."""
        existing = await self.config.guild(ctx.guild).honeypot_channel()
        if existing is not None:
            channel = ctx.guild.get_channel(existing)
            if channel is not None:
                await ctx.send(
                    f"A honeypot channel already exists: {channel.mention}. "
                    "Delete it first with `sabhoneypot deletechannel`."
                )
                return

        overwrites = {
            ctx.guild.me: discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
                manage_messages=True,
                manage_channels=True,
            ),
            ctx.guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
            ),
        }

        channel = await ctx.guild.create_text_channel(
            name="\U0001f36f",
            overwrites=overwrites,
            position=0,
            reason="Honeypot trap channel creation",
        )

        embed = await self._build_warning_embed(ctx.guild)
        warning_msg = await channel.send(embed=embed)

        await self.config.guild(ctx.guild).honeypot_channel.set(channel.id)
        await self.config.guild(ctx.guild).warning_message_id.set(warning_msg.id)
        await ctx.send(
            f"Honeypot channel created: {channel.mention}\n"
            "Set a log channel with `sabhoneypot logchannel` and enable with `sabhoneypot enable`."
        )

    @sabhoneypot.command(name="choosechannel")
    async def honeypot_choosechannel(self, ctx, channel: discord.TextChannel):
        """Designate an existing channel as the honeypot trap."""
        await self.config.guild(ctx.guild).honeypot_channel.set(channel.id)
        await ctx.send(
            f"Honeypot channel set to {channel.mention}.\n"
            "Make sure the channel has appropriate permissions and a warning message."
        )

    @sabhoneypot.command(name="deletechannel")
    @commands.bot_has_guild_permissions(manage_channels=True)
    async def honeypot_deletechannel(self, ctx):
        """Delete the honeypot channel and disable the trap."""
        channel_id = await self.config.guild(ctx.guild).honeypot_channel()
        if channel_id is None:
            await ctx.send("No honeypot channel is configured.")
            return

        channel = ctx.guild.get_channel(channel_id)
        if channel is not None:
            try:
                await channel.delete(reason="Honeypot channel removed by admin")
            except discord.NotFound:
                pass
            except discord.Forbidden:
                await ctx.send(
                    "I don't have permission to delete that channel. "
                    "Clearing config anyway."
                )

        await self.config.guild(ctx.guild).honeypot_channel.set(None)
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Honeypot channel removed and trap disabled.")

    # ------------------------------------------------------------------
    # Toggle commands
    # ------------------------------------------------------------------

    @sabhoneypot.command(name="enable")
    async def honeypot_enable(self, ctx):
        """Enable the honeypot trap. Requires a honeypot channel and log channel."""
        config = await self.config.guild(ctx.guild).all()
        if config["honeypot_channel"] is None:
            await ctx.send(
                "Set a honeypot channel first with `sabhoneypot createchannel` or `sabhoneypot choosechannel`."
            )
            return
        if config["logs_channel"] is None:
            await ctx.send("Set a log channel first with `sabhoneypot logchannel`.")
            return

        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("Honeypot trap is now **enabled**.")

    @sabhoneypot.command(name="disable")
    async def honeypot_disable(self, ctx):
        """Disable the honeypot trap."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Honeypot trap is now **disabled**.")

    # ------------------------------------------------------------------
    # Configuration commands
    # ------------------------------------------------------------------

    @sabhoneypot.command(name="action")
    async def honeypot_action(self, ctx, action: str):
        """Set the action on detection: mute, kick, ban, or none (log-only)."""
        action = action.lower()
        if action not in ("mute", "kick", "ban", "none"):
            await ctx.send("Action must be `mute`, `kick`, `ban`, or `none`.")
            return

        value = None if action == "none" else action
        await self.config.guild(ctx.guild).action.set(value)

        if action == "mute":
            mute_role = await self.config.guild(ctx.guild).mute_role()
            if mute_role is None:
                await ctx.send(
                    f"Action set to **{action}**.\n"
                    "**Warning:** No mute role configured. Set one with `sabhoneypot muterole`."
                )
                return

        await ctx.send(f"Action set to **{action}**.")

    @sabhoneypot.command(name="logchannel")
    async def honeypot_logchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel for incident log embeds."""
        await self.config.guild(ctx.guild).logs_channel.set(channel.id)
        await ctx.send(f"Log channel set to {channel.mention}.")

    @sabhoneypot.command(name="pingrole")
    async def honeypot_pingrole(self, ctx, role: Optional[discord.Role] = None):
        """Set the role to ping on detection. Omit to clear."""
        if role is None:
            await self.config.guild(ctx.guild).ping_role.set(None)
            await ctx.send("Ping role cleared.")
        else:
            await self.config.guild(ctx.guild).ping_role.set(role.id)
            await ctx.send(f"Ping role set to **{role.name}**.")

    @sabhoneypot.command(name="muterole")
    async def honeypot_muterole(self, ctx, role: discord.Role):
        """Set the role to assign when action is mute."""
        await self.config.guild(ctx.guild).mute_role.set(role.id)
        await ctx.send(f"Mute role set to **{role.name}**.")

    @sabhoneypot.command(name="warningtext")
    async def honeypot_warningtext(self, ctx, *, text: str):
        """Set the custom warning text for the honeypot channel embed."""
        await self.config.guild(ctx.guild).warning_text.set(text)
        await ctx.send(f"Warning text updated.")

    @sabhoneypot.command(name="kickdeletedays")
    async def honeypot_kickdeletedays(self, ctx, days: int):
        """Set days of message history to delete on kick/softban (0-7)."""
        if days < 0 or days > 7:
            await ctx.send("Must be between 0 and 7.")
            return
        await self.config.guild(ctx.guild).kick_delete_days.set(days)
        await ctx.send(f"Kick delete days set to **{days}**.")

    @sabhoneypot.command(name="bandeletedays")
    async def honeypot_bandeletedays(self, ctx, days: int):
        """Set days of message history to delete on ban (0-7)."""
        if days < 0 or days > 7:
            await ctx.send("Must be between 0 and 7.")
            return
        await self.config.guild(ctx.guild).ban_delete_days.set(days)
        await ctx.send(f"Ban delete days set to **{days}**.")

    @sabhoneypot.command(name="warningimage")
    async def honeypot_warningimage(self, ctx, url: Optional[str] = None):
        """Set a custom image URL for the warning embed. Omit to clear."""
        if url is None:
            await self.config.guild(ctx.guild).warning_image.set(None)
            await ctx.send("Warning image cleared.")
        else:
            await self.config.guild(ctx.guild).warning_image.set(url)
            await ctx.send(
                f"Warning image set. Use `sabhoneypot refresh` to update the channel."
            )

    @sabhoneypot.command(name="refresh")
    @commands.bot_has_guild_permissions(manage_channels=True)
    async def honeypot_refresh(self, ctx):
        """Update the warning message in the honeypot channel with current settings."""
        config = await self.config.guild(ctx.guild).all()
        channel_id = config["honeypot_channel"]
        if channel_id is None:
            await ctx.send("No honeypot channel is configured.")
            return

        channel = ctx.guild.get_channel(channel_id)
        if channel is None:
            await ctx.send(
                "Honeypot channel no longer exists. Clear it with `sabhoneypot deletechannel`."
            )
            return

        embed = await self._build_warning_embed(ctx.guild)
        message_id = config["warning_message_id"]

        # Try to edit the existing warning message
        if message_id is not None:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.edit(embed=embed)
                await ctx.send("Warning message updated.")
                return
            except (discord.NotFound, discord.HTTPException):
                pass  # Message gone, send a new one

        # No existing message or it was deleted — send a new one
        try:
            new_msg = await channel.send(embed=embed)
            await self.config.guild(ctx.guild).warning_message_id.set(new_msg.id)
            await ctx.send(
                "Warning message sent (old message not found, created a new one)."
            )
        except discord.HTTPException as e:
            await ctx.send(f"Failed to send warning message: `{e}`")

    # ------------------------------------------------------------------
    # Settings display
    # ------------------------------------------------------------------

    @sabhoneypot.command(name="settings")
    async def honeypot_settings(self, ctx):
        """Display all current honeypot settings."""
        config = await self.config.guild(ctx.guild).all()

        def resolve_channel(cid):
            if cid is None:
                return "Not set"
            ch = ctx.guild.get_channel(cid)
            return ch.mention if ch else f"Unknown (`{cid}`)"

        def resolve_role(rid):
            if rid is None:
                return "Not set"
            role = ctx.guild.get_role(rid)
            return role.mention if role else f"Unknown (`{rid}`)"

        action_display = config["action"] or "none (log-only)"

        embed = discord.Embed(
            title="Honeypot Settings",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Enabled", value=str(config["enabled"]), inline=True)
        embed.add_field(name="Action", value=action_display, inline=True)
        embed.add_field(
            name="Kick Delete Days", value=str(config["kick_delete_days"]), inline=True
        )
        embed.add_field(
            name="Ban Delete Days", value=str(config["ban_delete_days"]), inline=True
        )
        embed.add_field(
            name="Honeypot Channel",
            value=resolve_channel(config["honeypot_channel"]),
            inline=True,
        )
        embed.add_field(
            name="Log Channel",
            value=resolve_channel(config["logs_channel"]),
            inline=True,
        )
        embed.add_field(
            name="Ping Role", value=resolve_role(config["ping_role"]), inline=True
        )
        embed.add_field(
            name="Mute Role", value=resolve_role(config["mute_role"]), inline=True
        )
        embed.add_field(
            name="Warning Text", value=config["warning_text"][:1024], inline=False
        )
        embed.add_field(
            name="Warning Image",
            value=config["warning_image"] or "Not set",
            inline=False,
        )
        embed.set_footer(
            text=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None
        )

        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Migration from AAA3A Honeypot
    # ------------------------------------------------------------------

    def _migrate_guild_settings(self, guild: discord.Guild, old_data: dict):
        """Map AAA3A Honeypot guild config to SabHoneypot config.

        Returns (mapped: dict, skipped: list[str]).
        Does NOT write anything — caller is responsible for saving.
        """
        mapped = {}
        skipped = []

        # Honeypot channel
        if old_data["honeypot_channel"] is not None:
            ch = guild.get_channel(old_data["honeypot_channel"])
            if ch is not None:
                mapped["honeypot_channel"] = old_data["honeypot_channel"]
            else:
                skipped.append(
                    f"Honeypot channel (`{old_data['honeypot_channel']}`) \u2014 no longer exists"
                )

        # Logs channel
        if old_data["logs_channel"] is not None:
            ch = guild.get_channel(old_data["logs_channel"])
            if ch is not None:
                mapped["logs_channel"] = old_data["logs_channel"]
            else:
                skipped.append(
                    f"Log channel (`{old_data['logs_channel']}`) \u2014 no longer exists"
                )

        # Ping role
        if old_data["ping_role"] is not None:
            role = guild.get_role(old_data["ping_role"])
            if role is not None:
                mapped["ping_role"] = old_data["ping_role"]
            else:
                skipped.append(
                    f"Ping role (`{old_data['ping_role']}`) \u2014 no longer exists"
                )

        # Mute role
        if old_data["mute_role"] is not None:
            role = guild.get_role(old_data["mute_role"])
            if role is not None:
                mapped["mute_role"] = old_data["mute_role"]
            else:
                skipped.append(
                    f"Mute role (`{old_data['mute_role']}`) \u2014 no longer exists"
                )

        # Action
        old_action = old_data["action"]
        if old_action in ("mute", "kick", "ban"):
            mapped["action"] = old_action

        # ban_delete_message_days -> ban_delete_days and kick_delete_days
        mapped["ban_delete_days"] = old_data["ban_delete_message_days"]
        mapped["kick_delete_days"] = old_data["ban_delete_message_days"]

        # enabled
        mapped["enabled"] = old_data["enabled"]

        return mapped, skipped

    @sabhoneypot.command(name="migrate")
    async def honeypot_migrate(self, ctx):
        """Migrate settings from AAA3A's Honeypot cog for this server."""
        old_config = Config.get_conf(
            None,
            identifier=AAA3A_HONEYPOT_IDENTIFIER,
            cog_name=AAA3A_HONEYPOT_COG_NAME,
        )
        old_config.register_guild(**AAA3A_HONEYPOT_DEFAULTS)

        old_data = await old_config.guild(ctx.guild).all()

        has_data = any(
            old_data[k] != AAA3A_HONEYPOT_DEFAULTS[k] for k in AAA3A_HONEYPOT_DEFAULTS
        )
        if not has_data:
            await ctx.send("No AAA3A Honeypot configuration found for this server.")
            return

        mapped, skipped = self._migrate_guild_settings(ctx.guild, old_data)

        # Build preview embed
        def resolve_channel(cid):
            if cid is None:
                return "Not set"
            ch = ctx.guild.get_channel(cid)
            return ch.mention if ch else f"Unknown (`{cid}`)"

        def resolve_role(rid):
            if rid is None:
                return "Not set"
            role = ctx.guild.get_role(rid)
            return role.mention if role else f"Unknown (`{rid}`)"

        embed = discord.Embed(
            title="Honeypot Migration Preview",
            description="The following settings will be imported from AAA3A's Honeypot:",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Enabled", value=str(mapped.get("enabled", False)), inline=True
        )
        embed.add_field(
            name="Action",
            value=mapped.get("action") or "none (log-only)",
            inline=True,
        )
        embed.add_field(
            name="Kick Delete Days",
            value=str(mapped.get("kick_delete_days", 1)),
            inline=True,
        )
        embed.add_field(
            name="Ban Delete Days",
            value=str(mapped.get("ban_delete_days", 3)),
            inline=True,
        )
        embed.add_field(
            name="Honeypot Channel",
            value=resolve_channel(mapped.get("honeypot_channel")),
            inline=True,
        )
        embed.add_field(
            name="Log Channel",
            value=resolve_channel(mapped.get("logs_channel")),
            inline=True,
        )
        embed.add_field(
            name="Ping Role",
            value=resolve_role(mapped.get("ping_role")),
            inline=True,
        )
        embed.add_field(
            name="Mute Role",
            value=resolve_role(mapped.get("mute_role")),
            inline=True,
        )

        if skipped:
            embed.add_field(
                name="Skipped (not found)",
                value="\n".join(f"- {s}" for s in skipped),
                inline=False,
            )

        embed.set_footer(text="Reply 'yes' within 30 seconds to confirm migration.")
        await ctx.send(embed=embed)

        def check(m):
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.content.lower() in ("yes", "y")
            )

        try:
            await self.bot.wait_for("message", check=check, timeout=30.0)
        except Exception:
            await ctx.send("Migration cancelled \u2014 timed out.")
            return

        guild_config = self.config.guild(ctx.guild)
        for key, value in mapped.items():
            await getattr(guild_config, key).set(value)

        old_cog = self.bot.get_cog("Honeypot")
        warning = ""
        if old_cog is not None:
            warning = (
                "\n**Warning:** The old Honeypot cog is still loaded. "
                "Unload it with `[p]unload honeypot` to avoid conflicts."
            )

        await ctx.send(f"Migration complete. Settings imported successfully.{warning}")

    @sabhoneypot.command(name="migrateall")
    @commands.is_owner()
    async def honeypot_migrateall(self, ctx):
        """(Bot Owner) Migrate AAA3A Honeypot settings for ALL servers at once."""
        old_config = Config.get_conf(
            None,
            identifier=AAA3A_HONEYPOT_IDENTIFIER,
            cog_name=AAA3A_HONEYPOT_COG_NAME,
        )
        old_config.register_guild(**AAA3A_HONEYPOT_DEFAULTS)

        all_old = await old_config.all_guilds()
        if not all_old:
            await ctx.send("No AAA3A Honeypot configuration found for any server.")
            return

        # Filter to guilds the bot is currently in and that have non-default config
        candidates = []
        for guild_id, old_data in all_old.items():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            has_data = any(
                old_data.get(k) != AAA3A_HONEYPOT_DEFAULTS[k]
                for k in AAA3A_HONEYPOT_DEFAULTS
                if k in old_data
            )
            if has_data:
                candidates.append((guild, old_data))

        if not candidates:
            await ctx.send(
                "No AAA3A Honeypot configuration found for any server the bot is in."
            )
            return

        # Preview
        await ctx.send(
            f"Found AAA3A Honeypot configuration in **{len(candidates)}** server(s).\n"
            "Reply `yes` within 30 seconds to migrate all of them."
        )

        def check(m):
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.content.lower() in ("yes", "y")
            )

        try:
            await self.bot.wait_for("message", check=check, timeout=30.0)
        except Exception:
            await ctx.send("Migration cancelled \u2014 timed out.")
            return

        migrated = []

        for guild, old_data in candidates:
            # Fill in any missing keys with defaults
            for k, v in AAA3A_HONEYPOT_DEFAULTS.items():
                old_data.setdefault(k, v)

            mapped, skipped = self._migrate_guild_settings(guild, old_data)

            # Apply settings
            guild_config = self.config.guild(guild)
            for key, value in mapped.items():
                await getattr(guild_config, key).set(value)

            entry = f"**{guild.name}** (`{guild.id}`)"
            if skipped:
                entry += f" \u2014 skipped: {', '.join(skipped)}"
            migrated.append(entry)

        # Build result embed
        embed = discord.Embed(
            title="Migration Complete",
            description=f"Migrated settings for **{len(migrated)}** server(s).",
            color=discord.Color.green(),
        )

        # Chunk the results to fit embed field limits
        result_text = "\n".join(f"- {m}" for m in migrated)
        if len(result_text) <= 4096:
            embed.description += f"\n\n{result_text}"
        else:
            # Split into multiple fields
            chunks = []
            current = []
            current_len = 0
            for m in migrated:
                line = f"- {m}\n"
                if current_len + len(line) > 1024:
                    chunks.append("".join(current))
                    current = [line]
                    current_len = len(line)
                else:
                    current.append(line)
                    current_len += len(line)
            if current:
                chunks.append("".join(current))
            for i, chunk in enumerate(chunks):
                embed.add_field(name=f"Servers ({i + 1})", value=chunk, inline=False)

        old_cog = self.bot.get_cog("Honeypot")
        if old_cog is not None:
            embed.set_footer(
                text="Warning: The old Honeypot cog is still loaded. "
                "Unload it with [p]unload honeypot to avoid conflicts."
            )

        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Detection listener
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        if message.author.bot:
            return

        if await self.bot.cog_disabled_in_guild(self, message.guild):
            return

        config = await self.config.guild(message.guild).all()

        if not config["enabled"]:
            return

        honeypot_channel_id = config["honeypot_channel"]
        if honeypot_channel_id is None:
            return

        if message.channel.id != honeypot_channel_id:
            return

        logs_channel_id = config["logs_channel"]
        if logs_channel_id is None:
            return
        logs_channel = message.guild.get_channel(logs_channel_id)
        if logs_channel is None:
            log.warning(
                "Honeypot logs channel %s not found in guild %s",
                logs_channel_id,
                message.guild.id,
            )
            return

        # Exemption checks
        if await self.bot.is_owner(message.author):
            return
        if await self.bot.is_mod(message.author):
            return
        if await self.bot.is_admin(message.author):
            return
        if message.author.guild_permissions.manage_guild:
            return
        if message.author.top_role >= message.guild.me.top_role:
            return

        # Delete the trapped message
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException) as e:
            log.error("Failed to delete honeypot message: %s", e)

        # Execute action
        action = config["action"]
        action_display = "Log Only"
        failed = None

        if action == "mute":
            mute_role_id = config["mute_role"]
            if mute_role_id is None:
                failed = "Mute role not configured — logged only."
            else:
                mute_role = message.guild.get_role(mute_role_id)
                if mute_role is None:
                    failed = (
                        f"Mute role (`{mute_role_id}`) no longer exists — logged only."
                    )
                else:
                    try:
                        await message.author.add_roles(
                            mute_role,
                            reason="Honeypot detection — self-bot/scammer.",
                        )
                        action_display = "Muted"
                    except discord.HTTPException as e:
                        failed = f"Failed to mute: `{e}`"

        elif action == "kick":
            kick_delete_days = config["kick_delete_days"]
            try:
                # DM the user before softbanning with reason and invite
                try:
                    invite = await message.channel.guild.text_channels[0].create_invite(
                        max_uses=1,
                        max_age=86400,
                        unique=True,
                        reason="Honeypot softban — rejoin invite for kicked user.",
                    )
                except (discord.Forbidden, discord.HTTPException, IndexError):
                    invite = None

                dm_embed = discord.Embed(
                    title=f"You were kicked from {message.guild.name}",
                    color=discord.Color.orange(),
                )
                dm_embed.add_field(
                    name="Reason",
                    value="You sent a message in a honeypot trap channel.",
                    inline=False,
                )
                dm_embed.add_field(
                    name="What happened?",
                    value=(
                        "The channel you posted in was a trap designed to catch bots and scammers. "
                        "If you are a real person, you may rejoin using the invite below."
                    ),
                    inline=False,
                )
                if invite:
                    dm_embed.add_field(
                        name="Rejoin the server",
                        value=str(invite),
                        inline=False,
                    )
                dm_embed.set_footer(
                    text="This invite is single-use and expires in 24 hours."
                )

                try:
                    await message.author.send(embed=dm_embed)
                except (discord.Forbidden, discord.HTTPException):
                    pass  # User has DMs disabled

                await message.guild.ban(
                    message.author,
                    reason="Honeypot detection — self-bot/scammer (softban).",
                    delete_message_seconds=kick_delete_days * 86400,
                )
                await message.guild.unban(
                    message.author,
                    reason="Honeypot softban — immediate unban.",
                )
                action_display = f"Kicked (softban, {kick_delete_days}d msgs deleted)"
            except discord.HTTPException as e:
                failed = f"Failed to kick/softban: `{e}`"

        elif action == "ban":
            ban_delete_days = config["ban_delete_days"]
            try:
                await message.guild.ban(
                    message.author,
                    reason="Honeypot detection — self-bot/scammer.",
                    delete_message_seconds=ban_delete_days * 86400,
                )
                action_display = f"Banned ({ban_delete_days}d msgs deleted)"
            except discord.HTTPException as e:
                failed = f"Failed to ban: `{e}`"

        if failed:
            action_display = f"Failed — {failed}"
            log.warning(
                "Honeypot action failed in guild %s: %s", message.guild.id, failed
            )

        # Build incident embed
        embed = discord.Embed(
            title="Honeypot Detection",
            color=discord.Color.red(),
            timestamp=message.created_at,
        )
        embed.set_author(
            name=f"{message.author} ({message.author.id})",
            icon_url=message.author.display_avatar.url,
        )
        embed.add_field(
            name="User",
            value=f"{message.author.mention} (`{message.author.id}`)",
            inline=True,
        )
        embed.add_field(name="Action", value=action_display, inline=True)

        content_text = (
            message.content[:1024] if message.content else "*[No text content]*"
        )
        embed.add_field(name="Message Content", value=content_text, inline=False)

        if message.attachments:
            attachment_list = "\n".join(a.filename for a in message.attachments[:10])
            embed.add_field(
                name=f"Attachments ({len(message.attachments)})",
                value=attachment_list,
                inline=False,
            )

        embed.set_footer(
            text=f"{message.guild.name}",
            icon_url=message.guild.icon.url if message.guild.icon else None,
        )

        # Create modlog case
        if not failed:
            action_type = "honeypot"
            try:
                await modlog.create_case(
                    bot=self.bot,
                    guild=message.guild,
                    created_at=message.created_at,
                    action_type=action_type,
                    user=message.author,
                    moderator=message.guild.me,
                    reason=f"Posted in honeypot channel. Action: {action_display}",
                )
            except Exception as e:
                log.error("Failed to create modlog case: %s", e)

        # Send to logs channel
        ping_content = None
        ping_role_id = config["ping_role"]
        if ping_role_id:
            role = message.guild.get_role(ping_role_id)
            if role:
                ping_content = role.mention

        try:
            await logs_channel.send(
                content=ping_content,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except discord.HTTPException as e:
            log.error("Failed to send honeypot log embed: %s", e)


async def setup(bot):
    cog = SabHoneypot(bot)
    try:
        await modlog.register_casetype(
            name="honeypot",
            default_setting=True,
            image="\N{HONEY POT}",
            case_str="Honeypot Detection",
        )
    except RuntimeError:
        pass  # Already registered
    await bot.add_cog(cog)
