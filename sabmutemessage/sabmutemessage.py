import asyncio
import logging
import random
from datetime import timedelta
from pathlib import Path
from string import Template
from typing import Any, Dict, Literal, Optional, Union

import discord
import humanize
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.commands import GuildContext
from redbot.core.config import Config
from redbot.core.data_manager import cog_data_path
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.predicates import MessagePredicate

log = logging.getLogger("red.sablinova.sabmutemessage")

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]


class SabMuteMessage(commands.Cog):
    """Send message on mute in a chosen channel. Supports images and audit log info!"""

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=7832019466, force_registration=True
        )
        self.config.register_guild(
            channel=None,
            mute_role=None,
            detect_timeout=True,
            detect_role=False,
            message_templates=[],
        )
        self.message_images = cog_data_path(self) / "message_images"
        self.message_images.mkdir(exist_ok=True)

    async def red_get_data_for_user(self, *, user_id: int) -> Dict[str, Any]:
        return {}

    async def red_delete_data_for_user(
        self, *, requester: RequestType, user_id: int
    ) -> None:
        pass

    @commands.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    @commands.group(aliases=["sabmm"])
    async def sabmutemessage(self, ctx: GuildContext) -> None:
        """SabMuteMessage settings."""

    @sabmutemessage.command(name="channel")
    async def sabmutemessage_channel(
        self,
        ctx: GuildContext,
        channel: Optional[
            Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel]
        ] = None,
    ) -> None:
        """Set channel for mute messages. Leave empty to disable."""
        if channel is None:
            await self.config.guild(ctx.guild).channel.clear()
            await ctx.send("Mute messages are now disabled.")
            return
        await self.config.guild(ctx.guild).channel.set(channel.id)
        await ctx.send(f"Mute messages will now be sent in {channel.mention}")

    @sabmutemessage.command(name="muterole")
    async def sabmutemessage_muterole(
        self, ctx: GuildContext, role: Optional[discord.Role] = None
    ) -> None:
        """Set the mute role to detect. Leave empty to disable role detection."""
        if role is None:
            await self.config.guild(ctx.guild).mute_role.clear()
            await ctx.send("Mute role detection disabled.")
            return
        await self.config.guild(ctx.guild).mute_role.set(role.id)
        await ctx.send(f"Will now detect when {role.mention} is assigned as a mute.")

    @sabmutemessage.command(name="detecttimeout")
    async def sabmutemessage_detecttimeout(
        self, ctx: GuildContext, enabled: Optional[bool] = None
    ) -> None:
        """Set if Discord timeouts should trigger mute messages."""
        config_value = self.config.guild(ctx.guild).detect_timeout
        if enabled is None:
            if await config_value():
                message = "Discord timeouts trigger mute messages in this server."
            else:
                message = "Discord timeouts don't trigger mute messages in this server."
            await ctx.send(message)
            return

        await config_value.set(enabled)

        if enabled:
            message = "Discord timeouts will now trigger mute messages in this server."
        else:
            message = (
                "Discord timeouts will no longer trigger mute messages in this server."
            )
        await ctx.send(message)

    @sabmutemessage.command(name="detectrole")
    async def sabmutemessage_detectrole(
        self, ctx: GuildContext, enabled: Optional[bool] = None
    ) -> None:
        """Set if mute role assignment should trigger mute messages."""
        config_value = self.config.guild(ctx.guild).detect_role
        if enabled is None:
            if await config_value():
                message = "Mute role assignment triggers mute messages in this server."
            else:
                message = (
                    "Mute role assignment doesn't trigger mute messages in this server."
                )
            await ctx.send(message)
            return

        await config_value.set(enabled)

        if enabled:
            message = (
                "Mute role assignment will now trigger mute messages in this server."
            )
        else:
            message = "Mute role assignment will no longer trigger mute messages in this server."
        await ctx.send(message)

    @sabmutemessage.command(name="addmessage")
    async def sabmutemessage_addmessage(
        self, ctx: GuildContext, *, message: str
    ) -> None:
        """
        Add mute message.

        Those fields will get replaced automatically:
        $username - The muted user's name
        $server - The name of the server
        $duration - How long the mute is for (e.g., "1 hour", "indefinitely")
        $moderator - Who issued the mute (requires View Audit Log permission)
        $reason - The mute reason (requires View Audit Log permission)

        Note: Mute message can also have image.
        To set it, use `[p]sabmutemessage setimage`
        """
        guild = ctx.guild
        async with self.config.guild(guild).all() as guild_settings:
            guild_settings["message_templates"].append(message)

        # Test message with sample values
        content = Template(message).safe_substitute(
            username=str(ctx.author),
            server=guild.name,
            duration="1 hour",
            moderator=str(ctx.author),
            reason="Test reason",
        )

        filename = next(self.message_images.glob(f"{guild.id}.*"), None)
        file = None
        warning = ""
        if filename is not None:
            channel_id = guild_settings["channel"]
            channel = guild.get_channel(channel_id) if channel_id is not None else None
            if (
                channel is not None
                and not channel.permissions_for(guild.me).attach_files
            ):
                warning = (
                    "WARNING: Bot doesn't have permissions to send images"
                    " in channel used for mute messages.\n\n"
                )

            if not ctx.channel.permissions_for(guild.me).attach_files:
                await ctx.send(
                    f"{warning}Mute message set.\n"
                    "I wasn't able to send test message here"
                    ' due to missing "Attach files" permission.'
                )
                return

            file = discord.File(str(filename))
        await ctx.send(f"{warning}Mute message set, sending a test message here...")
        await ctx.send(content, file=file)

    @sabmutemessage.command(name="removemessage", aliases=["deletemessage"])
    async def sabmutemessage_removemessage(self, ctx: GuildContext) -> None:
        """Remove mute message."""
        templates = await self.config.guild(ctx.guild).message_templates()
        if not templates:
            await ctx.send("This guild doesn't have any mute message set.")
            return

        msg = "Choose a mute message to delete:\n\n"
        for idx, template in enumerate(templates, 1):
            msg += f"  {idx}. {template}\n"
        for page in pagify(msg):
            await ctx.send(box(page))

        pred = MessagePredicate.valid_int(ctx)

        try:
            await self.bot.wait_for(
                "message",
                check=lambda m: pred(m) and pred.result >= 1,
                timeout=30,
            )
        except asyncio.TimeoutError:
            await ctx.send("Okay, no messages will be removed.")
            return

        result = pred.result
        try:
            templates.pop(result - 1)
        except IndexError:
            await ctx.send("Wow! That's a big number. Too big...")
            return
        await self.config.guild(ctx.guild).message_templates.set(templates)
        await ctx.send("Message removed.")

    @sabmutemessage.command(name="listmessages")
    async def sabmutemessage_listmessages(self, ctx: GuildContext) -> None:
        """List mute message templates."""
        templates = await self.config.guild(ctx.guild).message_templates()
        if not templates:
            await ctx.send("This guild doesn't have any mute message templates set.")
            return

        msg = "Mute message templates:\n\n"
        for idx, template in enumerate(templates, 1):
            msg += f"  {idx}. {template}\n"
        for page in pagify(msg):
            await ctx.send(box(page))

    @sabmutemessage.command(name="setimage")
    async def sabmutemessage_setimage(self, ctx: GuildContext) -> None:
        """Set image for mute message."""
        guild = ctx.guild
        if len(ctx.message.attachments) != 1:
            await ctx.send("You have to send exactly one attachment.")
            return

        a = ctx.message.attachments[0]
        if a.width is None:
            await ctx.send("The attachment has to be an image.")
            return

        ext = a.filename.rpartition(".")[2]
        filename = self.message_images / f"{ctx.guild.id}.{ext}"
        with open(filename, "wb") as fp:
            await a.save(fp)

        for file in self.message_images.glob(f"{ctx.guild.id}.*"):
            if not file == filename:
                file.unlink()

        channel_id = await self.config.guild(guild).channel()
        channel = guild.get_channel(channel_id) if channel_id is not None else None
        if channel is not None and not channel.permissions_for(guild.me).attach_files:
            await ctx.send(
                "WARNING: Bot doesn't have permissions to send images"
                " in channel used for mute messages.\n\nImage set."
            )
        else:
            await ctx.send("Image set.")

    @sabmutemessage.command(name="unsetimage")
    async def sabmutemessage_unsetimage(self, ctx: GuildContext) -> None:
        """Unset image for mute message."""
        for file in self.message_images.glob(f"{ctx.guild.id}.*"):
            file.unlink()
        await ctx.send("Image unset.")

    @sabmutemessage.command(name="setup")
    async def sabmutemessage_setup(self, ctx: GuildContext) -> None:
        """Interactive setup wizard for SabMuteMessage."""
        guild = ctx.guild
        author = ctx.author

        await ctx.send(
            "**Welcome to SabMuteMessage setup!**\n\n"
            "I'll guide you through the configuration process.\n"
            "You can type `cancel` at any time to exit setup.\n\n"
            "First, let's set up the channel where mute messages will be sent."
        )

        # Step 1: Channel selection
        await ctx.send(
            "**Step 1/5:** Please mention a channel or type its name/ID where mute messages should be sent."
        )
        pred_channel = MessagePredicate.valid_text_channel(ctx)
        pred_cancel = MessagePredicate.contained_in(["cancel"], ctx)

        def check_channel(m):
            return (
                m.author == author
                and m.channel == ctx.channel
                and (pred_channel(m) or pred_cancel(m))
            )

        try:
            await self.bot.wait_for("message", check=check_channel, timeout=60)
        except asyncio.TimeoutError:
            await ctx.send("Setup timed out. Run the command again to restart.")
            return

        if pred_cancel.result:
            await ctx.send("Setup cancelled.")
            return

        channel = pred_channel.result
        await self.config.guild(guild).channel.set(channel.id)
        await ctx.send(f"Channel set to {channel.mention}")

        # Step 2: Detection mode
        await ctx.send(
            "**Step 2/5:** What should trigger mute messages?\n\n"
            "Reply with:\n"
            "`1` - Only Discord timeouts (recommended)\n"
            "`2` - Only mute role assignment\n"
            "`3` - Both timeouts and mute role\n"
            "`4` - Skip this step"
        )
        pred_mode = MessagePredicate.valid_int(ctx)
        try:
            await self.bot.wait_for(
                "message",
                check=lambda m: pred_mode(m) and 1 <= pred_mode.result <= 4,
                timeout=60,
            )
        except asyncio.TimeoutError:
            await ctx.send("Setup timed out. Current progress has been saved.")
            return

        mode = pred_mode.result
        if mode == 1:
            await self.config.guild(guild).detect_timeout.set(True)
            await self.config.guild(guild).detect_role.set(False)
            await ctx.send("Detection mode: Discord timeouts only")
        elif mode == 2:
            await self.config.guild(guild).detect_timeout.set(False)
            await self.config.guild(guild).detect_role.set(True)
            await ctx.send("Detection mode: Mute role only")
        elif mode == 3:
            await self.config.guild(guild).detect_timeout.set(True)
            await self.config.guild(guild).detect_role.set(True)
            await ctx.send("Detection mode: Both timeouts and mute role")
        else:
            await ctx.send("Skipped detection mode configuration.")

        # Step 3: Mute role (if role detection enabled)
        settings = await self.config.guild(guild).all()
        if settings["detect_role"]:
            await ctx.send(
                "**Step 3/5:** Please mention the mute role, type its name/ID, or type `skip` to configure later."
            )
            pred_role = MessagePredicate.valid_role(ctx)
            pred_skip = MessagePredicate.contained_in(["skip"], ctx)

            def check(m):
                return (
                    m.author == author
                    and m.channel == ctx.channel
                    and (pred_role(m) or pred_skip(m))
                )

            try:
                msg = await self.bot.wait_for("message", check=check, timeout=60)
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Current progress has been saved.")
                return

            if pred_skip.result:
                await ctx.send(
                    "Skipped mute role configuration. You can set it later with `[p]sabmm muterole`"
                )
            else:
                role = pred_role.result
                await self.config.guild(guild).mute_role.set(role.id)
                await ctx.send(f"Mute role set to {role.mention}")
        else:
            await ctx.send("**Step 3/5:** Skipped (mute role detection is disabled)")

        # Step 4: Message template
        await ctx.send(
            "**Step 4/5:** Now let's add a mute message template.\n\n"
            "Available variables:\n"
            "`$username` - The muted user's name\n"
            "`$server` - Server name\n"
            "`$duration` - Mute duration (e.g., '1 hour')\n"
            "`$moderator` - Who issued the mute\n"
            "`$reason` - Mute reason\n\n"
            "Example: `$username was muted in $server for $duration by $moderator. Reason: $reason`\n\n"
            "Type your message template, `skip` to use a default message, or `cancel` to exit:"
        )
        pred_msg = MessagePredicate.same_context(ctx)
        try:
            msg = await self.bot.wait_for("message", check=pred_msg, timeout=120)
        except asyncio.TimeoutError:
            await ctx.send("Setup timed out. Current progress has been saved.")
            return

        message = msg.content
        if message.lower() == "cancel":
            await ctx.send("Setup cancelled.")
            return
        elif message.lower() == "skip":
            message = "$username was muted in $server for $duration by $moderator. Reason: $reason"
            await ctx.send("Using default message template.")

        async with self.config.guild(guild).all() as guild_settings:
            guild_settings["message_templates"].append(message)

        await ctx.send("Message template added!")

        # Step 5: Image (optional)
        await ctx.send(
            "**Step 5/5:** (Optional) You can attach an image that will be sent with mute messages.\n\n"
            "Reply with an image attachment, or type `skip` to finish setup without an image."
        )

        def check_image(m):
            return (
                m.author == author
                and m.channel == ctx.channel
                and (len(m.attachments) == 1 or m.content.lower() == "skip")
            )

        try:
            msg = await self.bot.wait_for("message", check=check_image, timeout=60)
        except asyncio.TimeoutError:
            await ctx.send(
                "Setup timed out. Configuration is complete without an image."
            )
            await self._show_setup_summary(ctx)
            return

        if msg.content.lower() == "skip":
            await ctx.send("Skipped image configuration.")
        else:
            a = msg.attachments[0]
            if a.width is None:
                await ctx.send(
                    "The attachment must be an image. Skipping image configuration."
                )
            else:
                ext = a.filename.rpartition(".")[2]
                filename = self.message_images / f"{guild.id}.{ext}"
                with open(filename, "wb") as fp:
                    await a.save(fp)

                for file in self.message_images.glob(f"{guild.id}.*"):
                    if not file == filename:
                        file.unlink()

                await ctx.send("Image saved!")

        # Final summary
        await self._show_setup_summary(ctx)

    async def _show_setup_summary(self, ctx: GuildContext) -> None:
        """Show setup completion summary."""
        guild = ctx.guild
        settings = await self.config.guild(guild).all()

        channel = (
            guild.get_channel(settings["channel"]) if settings["channel"] else None
        )
        mute_role = (
            guild.get_role(settings["mute_role"]) if settings["mute_role"] else None
        )
        has_image = any(self.message_images.glob(f"{guild.id}.*"))

        msg = (
            "**Setup Complete!**\n\n"
            "**Current Configuration:**\n"
            f"Channel: {channel.mention if channel else 'Not set'}\n"
            f"Detect timeouts: {settings['detect_timeout']}\n"
            f"Detect mute role: {settings['detect_role']}\n"
            f"Mute role: {mute_role.mention if mute_role else 'Not set'}\n"
            f"Message templates: {len(settings['message_templates'])}\n"
            f"Image: {'Set' if has_image else 'Not set'}\n\n"
            "SabMuteMessage is now ready to use! You can modify settings anytime with `[p]sabmm` commands.\n"
            "Use `[p]sabmm settings` to view current configuration."
        )
        await ctx.send(msg)

    @sabmutemessage.command(name="settings")
    async def sabmutemessage_settings(self, ctx: GuildContext) -> None:
        """Show current mute message settings."""
        guild = ctx.guild
        settings = await self.config.guild(guild).all()

        channel = (
            guild.get_channel(settings["channel"]) if settings["channel"] else None
        )
        mute_role = (
            guild.get_role(settings["mute_role"]) if settings["mute_role"] else None
        )

        msg = "**SabMuteMessage Settings**\n\n"
        msg += f"Channel: {channel.mention if channel else 'Not set'}\n"
        msg += f"Mute role: {mute_role.mention if mute_role else 'Not set'}\n"
        msg += f"Detect Discord timeouts: {settings['detect_timeout']}\n"
        msg += f"Detect mute role: {settings['detect_role']}\n"
        msg += f"Message templates: {len(settings['message_templates'])}\n"

        has_image = any(self.message_images.glob(f"{guild.id}.*"))
        msg += f"Image set: {has_image}\n"

        await ctx.send(msg)

    async def _get_audit_info(
        self, member: discord.Member, action: discord.AuditLogAction
    ) -> tuple[Optional[discord.User], Optional[str]]:
        """Get moderator and reason from audit log."""
        if not member.guild.me.guild_permissions.view_audit_log:
            return None, None

        try:
            async for entry in member.guild.audit_logs(action=action, limit=5):
                if entry.target and entry.target.id == member.id:
                    # Check timestamp is recent (within 5 seconds)
                    if (discord.utils.utcnow() - entry.created_at).total_seconds() < 5:
                        return entry.user, entry.reason
        except discord.Forbidden:
            log.warning(f"Missing audit log permissions in guild {member.guild.id}")
        except Exception as e:
            log.error(f"Error fetching audit log: {e}")

        return None, None

    async def _send_mute_message(
        self,
        member: discord.Member,
        duration: Optional[timedelta],
        moderator: Optional[discord.User],
        reason: Optional[str],
    ) -> None:
        """Send the mute message to the configured channel."""
        settings = await self.config.guild(member.guild).all()
        channel_id = settings["channel"]
        if channel_id is None:
            return

        channel = member.guild.get_channel(channel_id)
        if channel is None:
            log.error(
                f"Channel with ID {channel_id} can't be found in guild with ID {member.guild.id}."
            )
            return

        message_templates = settings["message_templates"]
        if not message_templates:
            return

        message_template = random.choice(message_templates)

        # Format duration
        if duration:
            duration_str = humanize.naturaldelta(duration)
        else:
            duration_str = "indefinitely"

        # Substitute template variables
        content = Template(message_template).safe_substitute(
            username=str(member),
            server=member.guild.name,
            duration=duration_str,
            moderator=str(moderator) if moderator else "Unknown",
            reason=reason if reason else "No reason provided",
        )

        filename = next(self.message_images.glob(f"{member.guild.id}.*"), None)
        file = discord.utils.MISSING
        if filename is not None:
            if channel.permissions_for(member.guild.me).attach_files:
                file = discord.File(str(filename))
            else:
                log.info(
                    f'Bot doesn\'t have "Attach files" in channel with ID {channel_id} (guild ID: {member.guild.id})'
                )

        try:
            await channel.send(content, file=file)
        except discord.Forbidden:
            log.error(
                f"Bot can't send messages in channel with ID {channel_id} (guild ID: {member.guild.id})"
            )

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        """Detect mutes via timeout or role assignment."""
        guild = after.guild
        settings = await self.config.guild(guild).all()

        # Debug: Log all member updates where timeout changes
        if before.timed_out_until != after.timed_out_until:
            log.info(
                f"Timeout change detected for {after} (ID: {after.id}) in guild {guild.name} (ID: {guild.id}): "
                f"before={before.timed_out_until}, after={after.timed_out_until}"
            )

        # Check for Discord timeout
        if settings["detect_timeout"]:
            # Detect timeout being applied OR extended
            if (
                after.timed_out_until is not None
                and before.timed_out_until != after.timed_out_until
            ):
                # Timeout applied or extended
                log.info(f"Timeout detected for {after}, sending mute message")
                duration = after.timed_out_until - discord.utils.utcnow()
                moderator, reason = await self._get_audit_info(
                    after, discord.AuditLogAction.member_update
                )
                await self._send_mute_message(after, duration, moderator, reason)
                return

        # Check for mute role assignment
        if settings["detect_role"] and settings["mute_role"]:
            mute_role_id = settings["mute_role"]
            before_roles = {r.id for r in before.roles}
            after_roles = {r.id for r in after.roles}

            if mute_role_id in after_roles and mute_role_id not in before_roles:
                # Mute role added
                log.info(f"Mute role added to {after}, sending mute message")
                moderator, reason = await self._get_audit_info(
                    after, discord.AuditLogAction.member_role_update
                )
                await self._send_mute_message(after, None, moderator, reason)
