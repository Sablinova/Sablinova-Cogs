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

        # Check for Discord timeout
        if settings["detect_timeout"]:
            if before.timed_out_until is None and after.timed_out_until is not None:
                # Timeout applied
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
                moderator, reason = await self._get_audit_info(
                    after, discord.AuditLogAction.member_role_update
                )
                await self._send_mute_message(after, None, moderator, reason)
