import asyncio
import datetime
import logging
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set, Union

import discord
from redbot.core import Config, commands
from redbot.core.data_manager import cog_data_path

from .db import SnipeDatabase
from .views import SnipePaginationView

logger = logging.getLogger("red.sabbysnipe")

ChannelType = Optional[Union[discord.TextChannel, discord.VoiceChannel, discord.Thread]]
MemberType = Union[discord.Member, discord.User]


class SabbySnipe(commands.Cog):
    """Blazing fast, persistent, database-backed message sniping engine for Redbot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_path = cog_data_path(self) / "sabbysnipe.db"
        self.db = SnipeDatabase(self.db_path)

        # Fast in-memory RAM tier (maxlen=25 per channel)
        self._cache_deleted: Dict[int, deque] = defaultdict(lambda: deque(maxlen=25))
        self._cache_edited: Dict[int, deque] = defaultdict(lambda: deque(maxlen=25))

        # In-memory settings sets for 0ms lookup
        self._ignored_channels: Set[int] = set()
        self._ignored_roles: Set[int] = set()
        self._disabled_guilds: Set[int] = set()
        self._retention_days: int = 30

        self._prune_task: Optional[asyncio.Task] = None

        self.config = Config.get_conf(self, identifier=948172658192, force_registration=True)
        self.config.register_guild(
            enabled=True,
            ignored_channels=[],
            ignored_roles=[],
        )
        self.config.register_global(
            retention_days=30,
        )

    async def cog_load(self) -> None:
        """Initialize database, load settings into memory, and start tasks."""
        self.db.init_schema()
        self.db.start_worker()

        # Load global settings
        self._retention_days = await self.config.retention_days()

        # Load guild settings into RAM
        all_guilds = await self.config.all_guilds()
        for gid, gdata in all_guilds.items():
            if not gdata.get("enabled", True):
                self._disabled_guilds.add(int(gid))
            for cid in gdata.get("ignored_channels", []):
                self._ignored_channels.add(int(cid))
            for rid in gdata.get("ignored_roles", []):
                self._ignored_roles.add(int(rid))

        # Start daily pruning task
        self._prune_task = asyncio.create_task(self._daily_prune_loop())
        logger.info("SabbySnipe loaded successfully with persistent WAL database.")

    async def cog_unload(self) -> None:
        """Gracefully stop tasks and flush database queue."""
        if self._prune_task and not self._prune_task.done():
            self._prune_task.cancel()
        await self.db.stop_worker()
        logger.info("SabbySnipe unloaded cleanly.")

    async def _daily_prune_loop(self) -> None:
        """Prune old records once every 24 hours."""
        while True:
            try:
                await asyncio.sleep(86400)
                if self._retention_days > 0:
                    d_del, d_edit = await self.db.prune_older_than(self._retention_days)
                    logger.info("SabbySnipe auto-pruned %d deleted and %d edited records.", d_del, d_edit)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in SabbySnipe daily prune loop: %s", exc)

    # =========================================================================
    # EVENT LISTENERS (0ms synchronous RAM checks, zero event loop blocking)
    # =========================================================================

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if message.guild.id in self._disabled_guilds:
            return
        if message.channel.id in self._ignored_channels:
            return
        if getattr(message.channel, "parent", None) and message.channel.parent.id in self._ignored_channels:
            return
        if self._ignored_roles and hasattr(message.author, "roles"):
            if any(r.id in self._ignored_roles for r in message.author.roles):
                return

        now = time.time()
        created_at = message.created_at.timestamp() if message.created_at else now

        attachments = [
            {"filename": a.filename, "url": a.url, "content_type": getattr(a, "content_type", "")}
            for a in message.attachments
        ]
        stickers = [
            {"name": s.name, "url": str(s.url)}
            for s in getattr(message, "stickers", [])
        ]

        record: Dict[str, Any] = {
            "message_id": message.id,
            "guild_id": message.guild.id,
            "channel_id": message.channel.id,
            "author_id": message.author.id,
            "author_name": str(message.author),
            "author_display_name": message.author.display_name,
            "author_avatar_url": str(message.author.display_avatar.url),
            "content": message.content or "",
            "attachments": attachments,
            "stickers": stickers,
            "created_at": created_at,
            "deleted_at": now,
        }

        # 1. Store in fast RAM cache
        self._cache_deleted[message.channel.id].appendleft(record)
        # 2. Enqueue for async WAL SQLite write
        self.db.enqueue_deleted(record)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not after.guild or after.author.bot:
            return
        if after.guild.id in self._disabled_guilds:
            return
        if after.channel.id in self._ignored_channels:
            return
        if getattr(after.channel, "parent", None) and after.channel.parent.id in self._ignored_channels:
            return
        if before.content == after.content:
            return
        if self._ignored_roles and hasattr(after.author, "roles"):
            if any(r.id in self._ignored_roles for r in after.author.roles):
                return

        now = time.time()
        created_at = before.created_at.timestamp() if before.created_at else now

        record: Dict[str, Any] = {
            "message_id": after.id,
            "guild_id": after.guild.id,
            "channel_id": after.channel.id,
            "author_id": after.author.id,
            "author_name": str(after.author),
            "author_display_name": after.author.display_name,
            "author_avatar_url": str(after.author.display_avatar.url),
            "before_content": before.content or "",
            "after_content": after.content or "",
            "jump_url": after.jump_url,
            "created_at": created_at,
            "edited_at": now,
        }

        # 1. Store in fast RAM cache
        self._cache_edited[after.channel.id].appendleft(record)
        # 2. Enqueue for async WAL SQLite write
        self.db.enqueue_edited(record)

    # =========================================================================
    # EMBED BUILDERS
    # =========================================================================

    def _build_deleted_embed(self, record: Dict[str, Any], color: discord.Color) -> discord.Embed:
        embed = discord.Embed(
            title="Deleted Message",
            color=color,
            timestamp=datetime.datetime.fromtimestamp(record["deleted_at"], tz=datetime.timezone.utc),
        )
        embed.set_author(
            name=f"{record['author_display_name']} ({record['author_name']})",
            icon_url=record["author_avatar_url"],
        )
        content = record.get("content", "").strip()
        if content:
            embed.description = f">>> {content[:2048]}"
        else:
            embed.description = "*No text content (attachments or stickers only).*"

        embed.add_field(name="Channel", value=f"<#{record['channel_id']}>", inline=True)
        embed.add_field(name="Deleted", value=f"<t:{int(record['deleted_at'])}:R>", inline=True)
        embed.add_field(name="Sent", value=f"<t:{int(record['created_at'])}:R>", inline=True)

        attachments = record.get("attachments", [])
        if attachments:
            att_lines = []
            for a in attachments:
                att_lines.append(f"[{a['filename']}]({a['url']})")
            embed.add_field(name="Attachments", value="\n".join(att_lines[:5]), inline=False)
            first_url = attachments[0]["url"].lower()
            if any(first_url.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
                embed.set_image(url=attachments[0]["url"])

        stickers = record.get("stickers", [])
        if stickers:
            stk_lines = [f"[{s['name']}]({s['url']})" for s in stickers]
            embed.add_field(name="Stickers", value="\n".join(stk_lines[:5]), inline=False)

        embed.set_footer(text=f"User ID: {record['author_id']} | Msg ID: {record['message_id']}")
        return embed

    def _build_edited_embed(self, record: Dict[str, Any], color: discord.Color) -> discord.Embed:
        embed = discord.Embed(
            title="Edited Message",
            color=color,
            timestamp=datetime.datetime.fromtimestamp(record["edited_at"], tz=datetime.timezone.utc),
        )
        embed.set_author(
            name=f"{record['author_display_name']} ({record['author_name']})",
            icon_url=record["author_avatar_url"],
        )

        embed.add_field(name="Channel", value=f"<#{record['channel_id']}>", inline=True)
        embed.add_field(name="Edited", value=f"<t:{int(record['edited_at'])}:R>", inline=True)
        embed.add_field(name="Sent", value=f"<t:{int(record['created_at'])}:R>", inline=True)

        before = record.get("before_content", "").strip() or "*None*"
        after = record.get("after_content", "").strip() or "*None*"

        embed.add_field(name="Before", value=f">>> {before[:1024]}", inline=False)
        embed.add_field(name="After", value=f">>> {after[:1024]}", inline=False)

        if record.get("jump_url"):
            embed.add_field(name="Message Link", value=f"[Jump to Message]({record['jump_url']})", inline=False)

        embed.set_footer(text=f"User ID: {record['author_id']} | Msg ID: {record['message_id']}")
        return embed

    # =========================================================================
    # COMMANDS: SNIPE
    # =========================================================================

    @commands.guild_only()
    @commands.mod_or_permissions(manage_messages=True)
    @commands.bot_has_permissions(embed_links=True)
    @commands.hybrid_group(name="snipe", invoke_without_command=True)
    async def snipe(
        self,
        ctx: commands.Context,
        target_channel: ChannelType = None,
        index: int = 0,
    ) -> None:
        """Snipe a deleted message from the current channel or specified channel."""
        channel = target_channel or ctx.channel
        color = await ctx.embed_color()

        # 1. Fast check RAM cache
        ram_cache = self._cache_deleted.get(channel.id)
        if ram_cache and 0 <= index < len(ram_cache):
            record = ram_cache[index]
            embed = self._build_deleted_embed(record, color)
            await ctx.send(embed=embed)
            return

        # 2. Database query fallback
        records = await self.db.get_deleted(channel.id, limit=1, offset=index)
        if not records:
            await ctx.send(f"No deleted message found at index {index} in {channel.mention}.")
            return

        embed = self._build_deleted_embed(records[0], color)
        await ctx.send(embed=embed)

    @snipe.command(name="member", aliases=["user"])
    async def snipe_member(
        self,
        ctx: commands.Context,
        channel: ChannelType = None,
        *,
        member: MemberType,
    ) -> None:
        """Snipe deleted messages by a specific member."""
        cid = channel.id if channel else None
        records = await self.db.get_deleted_by_author(
            guild_id=ctx.guild.id,
            author_id=member.id,
            channel_id=cid,
            limit=1,
            offset=0,
        )

        if not records:
            scope = f"in {channel.mention}" if channel else "in this server"
            await ctx.send(f"No deleted messages found for {member.mention} {scope}.")
            return

        color = await ctx.embed_color()
        embed = self._build_deleted_embed(records[0], color)
        await ctx.send(embed=embed)

    @snipe.command(name="bulk")
    async def snipe_bulk(
        self,
        ctx: commands.Context,
        target_channel: ChannelType = None,
    ) -> None:
        """Interactively browse recent deleted messages in a channel."""
        channel = target_channel or ctx.channel
        records = await self.db.get_deleted(channel.id, limit=20)
        if not records:
            await ctx.send(f"No deleted messages recorded in {channel.mention}.")
            return

        color = await ctx.embed_color()
        pages = [self._build_deleted_embed(r, color) for r in records]
        view = SnipePaginationView(author_id=ctx.author.id, pages=pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    @snipe.command(name="search")
    async def snipe_search(
        self,
        ctx: commands.Context,
        query: str,
        target_channel: ChannelType = None,
    ) -> None:
        """Search deleted messages by text content."""
        cid = target_channel.id if target_channel else None
        records = await self.db.search_deleted(ctx.guild.id, query=query, channel_id=cid, limit=20)
        if not records:
            await ctx.send(f"No deleted messages found matching `{query}`.")
            return

        color = await ctx.embed_color()
        pages = [self._build_deleted_embed(r, color) for r in records]
        view = SnipePaginationView(author_id=ctx.author.id, pages=pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    @snipe.command(name="clear")
    async def snipe_clear(
        self,
        ctx: commands.Context,
        target_channel: ChannelType = None,
    ) -> None:
        """Clear sniped deleted messages for a channel."""
        channel = target_channel or ctx.channel
        if channel.id in self._cache_deleted:
            self._cache_deleted[channel.id].clear()
        self.db.enqueue_clear_channel(channel.id)
        await ctx.send(f"Cleared snipe history for {channel.mention}.")

    # =========================================================================
    # COMMANDS: ESNIPE
    # =========================================================================

    @commands.guild_only()
    @commands.mod_or_permissions(manage_messages=True)
    @commands.bot_has_permissions(embed_links=True)
    @commands.hybrid_group(name="esnipe", invoke_without_command=True)
    async def esnipe(
        self,
        ctx: commands.Context,
        target_channel: ChannelType = None,
        index: int = 0,
    ) -> None:
        """Snipe an edited message from the current channel or specified channel."""
        channel = target_channel or ctx.channel
        color = await ctx.embed_color()

        # 1. Fast check RAM cache
        ram_cache = self._cache_edited.get(channel.id)
        if ram_cache and 0 <= index < len(ram_cache):
            record = ram_cache[index]
            embed = self._build_edited_embed(record, color)
            await ctx.send(embed=embed)
            return

        # 2. Database query fallback
        records = await self.db.get_edited(channel.id, limit=1, offset=index)
        if not records:
            await ctx.send(f"No edited message found at index {index} in {channel.mention}.")
            return

        embed = self._build_edited_embed(records[0], color)
        await ctx.send(embed=embed)

    @esnipe.command(name="member", aliases=["user"])
    async def esnipe_member(
        self,
        ctx: commands.Context,
        channel: ChannelType = None,
        *,
        member: MemberType,
    ) -> None:
        """Snipe edited messages by a specific member."""
        cid = channel.id if channel else None
        records = await self.db.get_edited_by_author(
            guild_id=ctx.guild.id,
            author_id=member.id,
            channel_id=cid,
            limit=1,
            offset=0,
        )

        if not records:
            scope = f"in {channel.mention}" if channel else "in this server"
            await ctx.send(f"No edited messages found for {member.mention} {scope}.")
            return

        color = await ctx.embed_color()
        embed = self._build_edited_embed(records[0], color)
        await ctx.send(embed=embed)

    @esnipe.command(name="bulk")
    async def esnipe_bulk(
        self,
        ctx: commands.Context,
        target_channel: ChannelType = None,
    ) -> None:
        """Interactively browse recent edited messages in a channel."""
        channel = target_channel or ctx.channel
        records = await self.db.get_edited(channel.id, limit=20)
        if not records:
            await ctx.send(f"No edited messages recorded in {channel.mention}.")
            return

        color = await ctx.embed_color()
        pages = [self._build_edited_embed(r, color) for r in records]
        view = SnipePaginationView(author_id=ctx.author.id, pages=pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    @esnipe.command(name="search")
    async def esnipe_search(
        self,
        ctx: commands.Context,
        query: str,
        target_channel: ChannelType = None,
    ) -> None:
        """Search edited messages by before or after text content."""
        cid = target_channel.id if target_channel else None
        records = await self.db.search_edited(ctx.guild.id, query=query, channel_id=cid, limit=20)
        if not records:
            await ctx.send(f"No edited messages found matching `{query}`.")
            return

        color = await ctx.embed_color()
        pages = [self._build_edited_embed(r, color) for r in records]
        view = SnipePaginationView(author_id=ctx.author.id, pages=pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    @esnipe.command(name="history")
    async def esnipe_history(
        self,
        ctx: commands.Context,
        message_id: int,
    ) -> None:
        """Show full edit history revisions for a specific message ID."""
        records = await self.db.get_edit_history(message_id)
        if not records:
            await ctx.send(f"No edit revisions recorded for message ID `{message_id}`.")
            return

        color = await ctx.embed_color()
        pages = [self._build_edited_embed(r, color) for r in records]
        view = SnipePaginationView(author_id=ctx.author.id, pages=pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    # =========================================================================
    # COMMANDS: SETSNIPE (Administration & Configuration)
    # =========================================================================

    @commands.admin_or_permissions(manage_guild=True)
    @commands.hybrid_group(name="setsnipe")
    async def setsnipe(self, ctx: commands.Context) -> None:
        """Configuration options for SabbySnipe."""
        pass

    @setsnipe.command(name="toggle")
    async def setsnipe_toggle(self, ctx: commands.Context) -> None:
        """Toggle message sniping tracking on or off for this server."""
        current = ctx.guild.id not in self._disabled_guilds
        new_state = not current
        if new_state:
            self._disabled_guilds.discard(ctx.guild.id)
            await self.config.guild(ctx.guild).enabled.set(True)
            await ctx.send("SabbySnipe tracking has been **enabled** for this server.")
        else:
            self._disabled_guilds.add(ctx.guild.id)
            await self.config.guild(ctx.guild).enabled.set(False)
            await ctx.send("SabbySnipe tracking has been **disabled** for this server.")

    @setsnipe.command(name="ignorechannel")
    async def setsnipe_ignorechannel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Toggle ignoring a specific channel from snipe tracking."""
        if channel.id in self._ignored_channels:
            self._ignored_channels.discard(channel.id)
            async with self.config.guild(ctx.guild).ignored_channels() as ignored:
                if channel.id in ignored:
                    ignored.remove(channel.id)
            await ctx.send(f"{channel.mention} is **no longer ignored**.")
        else:
            self._ignored_channels.add(channel.id)
            async with self.config.guild(ctx.guild).ignored_channels() as ignored:
                if channel.id not in ignored:
                    ignored.append(channel.id)
            await ctx.send(f"{channel.mention} is now **ignored**.")

    @setsnipe.command(name="ignorerole")
    async def setsnipe_ignorerole(self, ctx: commands.Context, role: discord.Role) -> None:
        """Toggle ignoring messages from members with a specific role."""
        if role.id in self._ignored_roles:
            self._ignored_roles.discard(role.id)
            async with self.config.guild(ctx.guild).ignored_roles() as ignored:
                if role.id in ignored:
                    ignored.remove(role.id)
            await ctx.send(f"Role `{role.name}` is **no longer ignored**.")
        else:
            self._ignored_roles.add(role.id)
            async with self.config.guild(ctx.guild).ignored_roles() as ignored:
                if role.id not in ignored:
                    ignored.append(role.id)
            await ctx.send(f"Role `{role.name}` is now **ignored**.")

    @commands.is_owner()
    @setsnipe.command(name="retention")
    async def setsnipe_retention(self, ctx: commands.Context, days: int) -> None:
        """Set history retention in days (0 = unlimited)."""
        if days < 0:
            await ctx.send("Days must be 0 or greater.")
            return
        self._retention_days = days
        await self.config.retention_days.set(days)
        if days == 0:
            await ctx.send("Retention set to **unlimited** (records will not be auto-deleted).")
        else:
            await ctx.send(f"Retention set to **{days} days**.")

    @setsnipe.command(name="stats")
    async def setsnipe_stats(self, ctx: commands.Context) -> None:
        """Show SabbySnipe database and cache performance statistics."""
        stats = await self.db.get_stats(ctx.guild.id)
        global_stats = await self.db.get_stats()
        color = await ctx.embed_color()

        embed = discord.Embed(title="SabbySnipe Performance & Database Stats", color=color)
        embed.add_field(
            name="Server Deleted Messages",
            value=f"`{stats['deleted_count']:,}`",
            inline=True,
        )
        embed.add_field(
            name="Server Edited Messages",
            value=f"`{stats['edited_count']:,}`",
            inline=True,
        )
        embed.add_field(
            name="Global Records in DB",
            value=f"`{global_stats['deleted_count'] + global_stats['edited_count']:,}`",
            inline=True,
        )

        size_mb = global_stats["size_bytes"] / (1024 * 1024)
        embed.add_field(
            name="SQLite Database Size",
            value=f"`{size_mb:.2f} MB` (WAL mode)",
            inline=True,
        )
        embed.add_field(
            name="Pending Write Queue",
            value=f"`{stats['queue_size']}` items",
            inline=True,
        )
        embed.add_field(
            name="Retention Policy",
            value=f"`{self._retention_days} days`" if self._retention_days > 0 else "`Unlimited`",
            inline=True,
        )

        active_del = len(self._cache_deleted)
        active_edit = len(self._cache_edited)
        embed.add_field(
            name="Fast RAM Buffer Channels",
            value=f"`{active_del}` deleted / `{active_edit}` edited",
            inline=True,
        )
        await ctx.send(embed=embed)
