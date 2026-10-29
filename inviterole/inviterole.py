"""
InviteRole Cog for Red-DiscordBot.
Assign roles automatically to users who join via specific invite links.
"""

import asyncio
from datetime import datetime, timezone
import logging
import re
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

logger = logging.getLogger("red.sablinova.inviterole")

DURATION_REGEX = re.compile(
    r"^((?P<days>\d+)d)?((?P<hours>\d+)h)?((?P<minutes>\d+)m)?((?P<seconds>\d+)s)?$",
    re.IGNORECASE,
)


def parse_duration(duration_str: Optional[str]) -> int:
    """
    Parse a human-readable duration string into total seconds.

    Supports:
        '0', 'never', 'infinite' -> 0 (permanent invite)
        '30m', '12h', '1d', '7d', '1d12h30m', or raw seconds as digits.

    Returns:
        int: Number of seconds (0 for permanent).

    Raises:
        commands.BadArgument: If the string is invalid or exceeds Discord's 7-day limit.
    """
    if not duration_str or duration_str.strip().lower() in ("0", "never", "infinite", "none", "perm", "permanent"):
        return 0

    clean_str = duration_str.strip().lower()

    # If purely digits, treat as raw seconds
    if clean_str.isdigit():
        secs = int(clean_str)
        if secs > 604800:
            raise commands.BadArgument("Invite duration cannot exceed 7 days (604,800 seconds). Use `0` for permanent.")
        return secs

    match = DURATION_REGEX.match(clean_str)
    if not match:
        raise commands.BadArgument(
            f"Invalid duration format: `{duration_str}`. Examples: `1d`, `12h`, `30m`, `1d12h`, or `0` for permanent."
        )

    parts = match.groupdict()
    days = int(parts.get("days") or 0)
    hours = int(parts.get("hours") or 0)
    minutes = int(parts.get("minutes") or 0)
    seconds = int(parts.get("seconds") or 0)

    total_seconds = (days * 86400) + (hours * 3600) + (minutes * 60) + seconds

    if total_seconds > 604800:
        raise commands.BadArgument("Invite duration cannot exceed 7 days (604,800 seconds). Use `0` for permanent.")

    return total_seconds


def format_duration(seconds: int) -> str:
    """Format seconds into a human-readable string."""
    if seconds <= 0:
        return "Permanent (Never expires)"

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 and not parts:
        parts.append(f"{secs}s")

    return " ".join(parts) or "Permanent"


def extract_invite_code(code_or_url: str) -> str:
    """Extract raw invite code from a URL or raw string."""
    val = code_or_url.strip()
    if "/" in val:
        parsed = urlparse(val if "://" in val else f"https://{val}")
        path_parts = [p for p in parsed.path.split("/") if p and p != "invite"]
        if path_parts:
            return path_parts[-1]
    return val


class InviteRole(commands.Cog):
    """
    Assign roles automatically to users who join via specific Discord invites.
    """

    __author__ = "Sablinova"
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=839217491028471920,
            force_registration=True,
        )

        default_guild = {
            "invites": {},
            # code -> {
            #     "role_ids": [int],
            #     "channel_id": int,
            #     "max_uses": int,
            #     "max_age": int,
            #     "created_by": int,
            #     "created_at": str,
            #     "total_joined": int,
            #     "note": str
            # }
            "log_channel_id": None,
            "enabled": True,
            "dm_user": False,
            "dm_message": "Welcome to **{guild}**! You were assigned the role: **{roles}**.",
        }

        self.config.register_guild(**default_guild)

        # In-memory cache of invite uses: guild_id -> {code: uses}
        self._invite_cache: Dict[int, Dict[str, int]] = {}
        self._cache_locks: Dict[int, asyncio.Lock] = {}
        self._init_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        """Start background initialization of invite cache."""
        self._init_task = asyncio.create_task(self._initialize_cache())

    def cog_unload(self) -> None:
        """Cancel background tasks on unload."""
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()

    def _get_lock(self, guild_id: int) -> asyncio.Lock:
        """Get or create an asyncio lock for guild invite cache mutations."""
        if guild_id not in self._cache_locks:
            self._cache_locks[guild_id] = asyncio.Lock()
        return self._cache_locks[guild_id]

    async def _fetch_and_cache_guild_invites(self, guild: discord.Guild) -> Dict[str, int]:
        """Fetch all live invites for a guild and update cache."""
        if not guild.me.guild_permissions.manage_guild:
            return {}

        try:
            invites = await guild.invites()
            invite_map = {inv.code: inv.uses or 0 for inv in invites}
            self._invite_cache[guild.id] = invite_map
            return invite_map
        except discord.Forbidden:
            logger.warning("Missing permissions to fetch invites in guild %s (%s)", guild.name, guild.id)
            return {}
        except Exception as exc:
            logger.error("Failed to fetch invites for guild %s (%s): %s", guild.name, guild.id, exc)
            return {}

    async def _initialize_cache(self) -> None:
        """Wait until bot is ready and cache invites for all connected guilds."""
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self._fetch_and_cache_guild_invites(guild)
            except Exception as exc:
                logger.error("Error initialising cache for guild %s: %s", guild.id, exc)

    # -------------------------------------------------------------------------
    # Event Listeners
    # -------------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Refresh invite cache when bot reconnections occur."""
        for guild in self.bot.guilds:
            try:
                await self._fetch_and_cache_guild_invites(guild)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Cache invites for a newly joined guild."""
        await self._fetch_and_cache_guild_invites(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Clean up cache when leaving a guild."""
        self._invite_cache.pop(guild.id, None)
        self._cache_locks.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        """Track new invites in real time."""
        if not invite.guild:
            return
        guild_id = invite.guild.id
        if guild_id in self._invite_cache:
            self._invite_cache[guild_id][invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        """Remove deleted invites from live tracking."""
        if not invite.guild:
            return
        guild_id = invite.guild.id
        if guild_id in self._invite_cache:
            self._invite_cache[guild_id].pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """
        Detect which invite link the new member joined through,
        and assign the corresponding configured roles.
        """
        if member.bot:
            return

        guild = member.guild
        if not guild.me.guild_permissions.manage_roles or not guild.me.guild_permissions.manage_guild:
            return

        guild_conf = await self.config.guild(guild).all()
        if not guild_conf.get("enabled", True):
            return

        configured_invites = guild_conf.get("invites", {})
        if not configured_invites:
            return

        async with self._get_lock(guild.id):
            cached_invites = dict(self._invite_cache.get(guild.id, {}))

            try:
                live_invites = await guild.invites()
            except Exception as exc:
                logger.error("Failed to fetch live invites on member join for guild %s: %s", guild.id, exc)
                return

            live_map = {inv.code: inv.uses or 0 for inv in live_invites}

            used_code: Optional[str] = None

            # 1. Primary check: Find an invite whose use count incremented
            for inv in live_invites:
                code = inv.code
                curr_uses = inv.uses or 0
                prev_uses = cached_invites.get(code, 0)
                if curr_uses > prev_uses:
                    used_code = code
                    break

            # 2. Secondary check: Handle single-use or max-uses reached invites.
            # When an invite reaches max_uses, Discord immediately purges it from live_invites!
            if not used_code:
                for code, prev_uses in cached_invites.items():
                    if code not in live_map and code in configured_invites:
                        cfg = configured_invites[code]
                        max_uses = cfg.get("max_uses", 0)
                        # If configured max_uses was 1 (or prev_uses was max_uses - 1)
                        if max_uses > 0 and (prev_uses == max_uses - 1 or max_uses == 1):
                            used_code = code
                            break
                        # Even if max_uses was unknown, a vanished configured invite was consumed
                        used_code = code
                        break

            # Always update cache with the latest live map
            self._invite_cache[guild.id] = live_map

        if not used_code or used_code not in configured_invites:
            return

        inv_data = configured_invites[used_code]
        role_ids = inv_data.get("role_ids", [])
        if not role_ids:
            return

        # Hierarchy and existence validation
        roles_to_assign: List[discord.Role] = []
        for rid in role_ids:
            role = guild.get_role(rid)
            if not role:
                continue
            if role == guild.default_role:
                continue
            if role.is_bot_managed() or role.is_integration():
                continue
            bot_top_pos = getattr(guild.me.top_role, "position", 0)
            role_pos = getattr(role, "position", 0)
            if role_pos >= bot_top_pos:
                logger.warning(
                    "Cannot assign role %s (%s) in %s: higher or equal to bot top role",
                    role.name,
                    role.id,
                    guild.name,
                )
                continue
            roles_to_assign.append(role)

        if not roles_to_assign:
            logger.warning("No valid assignable roles found for invite %s in %s", used_code, guild.name)
            return

        # Assign roles to member
        try:
            await member.add_roles(
                *roles_to_assign,
                reason=f"InviteRole: Joined via invite link {used_code}",
            )
            logger.info(
                "Assigned roles [%s] to %s (%s) via invite %s",
                ", ".join(r.name for r in roles_to_assign),
                member,
                member.id,
                used_code,
            )
        except discord.HTTPException as exc:
            logger.error("Failed to add roles to %s via invite %s: %s", member, used_code, exc)
            return

        # Increment total joined counter in config
        inv_data["total_joined"] = inv_data.get("total_joined", 0) + 1
        await self.config.guild(guild).invites.set(configured_invites)

        # Logging channel notification
        log_channel_id = guild_conf.get("log_channel_id")
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel and log_channel.permissions_for(guild.me).send_messages:
                try:
                    embed = discord.Embed(
                        title="📥 Invite Role Assigned",
                        description=f"{member.mention} (`{member.id}`) joined using an invite role link.",
                        color=discord.Color.green(),
                        timestamp=datetime.now(timezone.utc),
                    )
                    embed.add_field(name="Invite Code", value=f"`{used_code}`", inline=True)
                    embed.add_field(
                        name="Roles Given",
                        value=", ".join(r.mention for r in roles_to_assign),
                        inline=True,
                    )
                    embed.add_field(
                        name="Total Joins via Link",
                        value=str(inv_data["total_joined"]),
                        inline=True,
                    )
                    note = inv_data.get("note")
                    if note:
                        embed.add_field(name="Note", value=note, inline=False)
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await log_channel.send(embed=embed)
                except Exception as log_exc:
                    logger.error("Failed to send log message for InviteRole: %s", log_exc)

        # Optional direct message to user
        if guild_conf.get("dm_user", False):
            dm_template = guild_conf.get(
                "dm_message",
                "Welcome to **{guild}**! You were assigned the role: **{roles}**.",
            )
            roles_str = ", ".join(r.name for r in roles_to_assign)
            try:
                dm_content = dm_template.format(
                    user=str(member),
                    mention=member.mention,
                    guild=guild.name,
                    roles=roles_str,
                    code=used_code,
                )
                await member.send(dm_content)
            except (discord.Forbidden, discord.HTTPException):
                pass

    # -------------------------------------------------------------------------
    # Commands
    # -------------------------------------------------------------------------

    @commands.group(name="inviterole", aliases=["irole"])
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True, manage_guild=True)
    @commands.bot_has_permissions(manage_roles=True, manage_guild=True)
    async def inviterole(self, ctx: commands.Context) -> None:
        """
        Manage invite links that automatically assign roles upon member join.
        """
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @inviterole.command(name="create")
    async def create_invite_role(
        self,
        ctx: commands.Context,
        role: discord.Role,
        max_uses: Optional[int] = 0,
        duration: Optional[str] = "0",
        channel: Optional[discord.TextChannel] = None,
        *,
        note: str = "",
    ) -> None:
        """
        Create a new Discord invite that automatically assigns a role to joiners.

        **Arguments:**
        - `role`: The role to assign (must be lower than bot & your top role).
        - `max_uses`: Maximum number of times this link can be used (0 for unlimited).
        - `duration`: How long before link expires (e.g. `1d`, `12h`, `30m`, or `0` for permanent).
        - `channel`: The destination channel for the invite (defaults to current channel).
        - `note`: An optional label or note for this invite.

        **Example:**
        `[p]inviterole create @Member 0 0`
        `[p]inviterole create @VIP 50 7d #welcome VIP Promo Link`
        """
        target_channel = channel or ctx.channel

        # Validate channel permissions
        ch_perms = target_channel.permissions_for(ctx.guild.me)
        if not ch_perms.create_instant_invite:
            await ctx.send(f"❌ I don't have permission to create invites in {target_channel.mention}.")
            return

        # Validate role hierarchy and safety
        val_err = self._validate_role_assignable(ctx, role)
        if val_err:
            await ctx.send(f"❌ {val_err}")
            return

        # Parse duration
        try:
            max_age = parse_duration(duration)
        except commands.BadArgument as exc:
            await ctx.send(f"❌ {exc}")
            return

        uses = max(0, max_uses or 0)

        # Create unique Discord invite
        try:
            invite = await target_channel.create_invite(
                max_age=max_age,
                max_uses=uses,
                unique=True,
                reason=f"InviteRole created by {ctx.author} ({ctx.author.id}) for role @{role.name}",
            )
        except discord.HTTPException as exc:
            await ctx.send(f"❌ Failed to generate invite from Discord: `{exc}`")
            return

        # Store in config
        async with self.config.guild(ctx.guild).invites() as invites:
            invites[invite.code] = {
                "role_ids": [role.id],
                "channel_id": target_channel.id,
                "max_uses": uses,
                "max_age": max_age,
                "created_by": ctx.author.id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "total_joined": 0,
                "note": note.strip(),
            }

        # Sync into cache immediately
        self._invite_cache.setdefault(ctx.guild.id, {})[invite.code] = 0

        embed = discord.Embed(
            title="✅ Invite Role Created",
            description=f"Anyone who joins using this link will receive the **{role.name}** role.",
            color=role.color if role.color.value != 0 else discord.Color.blue(),
        )
        embed.add_field(name="🔗 Invite URL", value=f"https://discord.gg/{invite.code}", inline=False)
        embed.add_field(name="🎭 Role", value=role.mention, inline=True)
        embed.add_field(name="📍 Channel", value=target_channel.mention, inline=True)
        embed.add_field(
            name="🔢 Max Uses",
            value=f"{uses} uses" if uses > 0 else "Unlimited",
            inline=True,
        )
        embed.add_field(name="⏳ Duration", value=format_duration(max_age), inline=True)
        if note.strip():
            embed.add_field(name="📝 Note", value=note.strip(), inline=False)

        embed.set_footer(text=f"Invite code: {invite.code} • Created by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @inviterole.command(name="link")
    async def link_existing_invite(
        self,
        ctx: commands.Context,
        invite_code_or_url: str,
        role: discord.Role,
        *,
        note: str = "",
    ) -> None:
        """
        Link an existing Discord invite to automatically assign a role.

        **Arguments:**
        - `invite_code_or_url`: The invite link or code (e.g. `discord.gg/abc` or `abc`).
        - `role`: The role to assign when someone joins with this invite.
        - `note`: An optional label or note for this invite.

        **Example:**
        `[p]inviterole link https://discord.gg/xyz123 @Supporter Community partner link`
        """
        code = extract_invite_code(invite_code_or_url)

        val_err = self._validate_role_assignable(ctx, role)
        if val_err:
            await ctx.send(f"❌ {val_err}")
            return

        try:
            guild_invites = await ctx.guild.invites()
        except discord.HTTPException as exc:
            await ctx.send(f"❌ Failed to fetch server invites: `{exc}`")
            return

        matched_invite = next((inv for inv in guild_invites if inv.code == code), None)
        if not matched_invite:
            await ctx.send(
                f"❌ Could not find active invite `{code}` belonging to this server. "
                f"Make sure the link is valid and was created in this server."
            )
            return

        async with self.config.guild(ctx.guild).invites() as invites:
            invites[code] = {
                "role_ids": [role.id],
                "channel_id": matched_invite.channel.id if matched_invite.channel else ctx.channel.id,
                "max_uses": matched_invite.max_uses or 0,
                "max_age": matched_invite.max_age or 0,
                "created_by": ctx.author.id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "total_joined": 0,
                "note": note.strip(),
            }

        # Update cache
        self._invite_cache.setdefault(ctx.guild.id, {})[code] = matched_invite.uses or 0

        embed = discord.Embed(
            title="🔗 Invite Linked",
            description=f"Existing invite `{code}` is now linked to **{role.name}**.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Invite Link", value=f"https://discord.gg/{code}", inline=False)
        embed.add_field(name="Assigned Role", value=role.mention, inline=True)
        embed.add_field(
            name="Current Uses",
            value=f"{matched_invite.uses} / {matched_invite.max_uses or '∞'}",
            inline=True,
        )
        if note.strip():
            embed.add_field(name="Note", value=note.strip(), inline=False)

        await ctx.send(embed=embed)

    @inviterole.command(name="unlink", aliases=["remove", "delete"])
    async def unlink_invite(
        self,
        ctx: commands.Context,
        invite_code_or_url: str,
        delete_discord_invite: bool = False,
    ) -> None:
        """
        Unlink an invite from role assignment.

        **Arguments:**
        - `invite_code_or_url`: The invite link or code to unlink.
        - `delete_discord_invite`: Set to `true` to also permanently delete the Discord invite.

        **Example:**
        `[p]inviterole unlink xyz123`
        `[p]inviterole unlink https://discord.gg/xyz123 true`
        """
        code = extract_invite_code(invite_code_or_url)

        async with self.config.guild(ctx.guild).invites() as invites:
            if code not in invites:
                await ctx.send(f"❌ Invite `{code}` is not currently configured as an invite role.")
                return
            invites.pop(code)

        deleted_msg = ""
        if delete_discord_invite:
            try:
                guild_invites = await ctx.guild.invites()
                target_inv = next((inv for inv in guild_invites if inv.code == code), None)
                if target_inv:
                    await target_inv.delete(reason=f"InviteRole unlinked by {ctx.author}")
                    deleted_msg = " and permanently deleted from Discord"
            except Exception as e:
                deleted_msg = f" (failed to delete from Discord: {e})"

        await ctx.send(f"✅ Successfully unlinked invite `{code}`{deleted_msg}.")

    @inviterole.command(name="addrole")
    async def add_role_to_invite(
        self,
        ctx: commands.Context,
        invite_code_or_url: str,
        role: discord.Role,
    ) -> None:
        """
        Add an additional role to give joiners using an existing invite link.

        **Example:**
        `[p]inviterole addrole xyz123 @Announcements`
        """
        code = extract_invite_code(invite_code_or_url)

        val_err = self._validate_role_assignable(ctx, role)
        if val_err:
            await ctx.send(f"❌ {val_err}")
            return

        async with self.config.guild(ctx.guild).invites() as invites:
            if code not in invites:
                await ctx.send(f"❌ Invite `{code}` is not configured as an invite role.")
                return

            role_ids = invites[code].setdefault("role_ids", [])
            if role.id in role_ids:
                await ctx.send(f"⚠️ Role {role.mention} is already assigned by invite `{code}`.")
                return

            role_ids.append(role.id)

        await ctx.send(f"✅ Added {role.mention} to invite `{code}`. Joiners will now receive this role too.")

    @inviterole.command(name="removerole")
    async def remove_role_from_invite(
        self,
        ctx: commands.Context,
        invite_code_or_url: str,
        role: discord.Role,
    ) -> None:
        """
        Remove a role from an existing invite role link.

        **Example:**
        `[p]inviterole removerole xyz123 @Announcements`
        """
        code = extract_invite_code(invite_code_or_url)

        async with self.config.guild(ctx.guild).invites() as invites:
            if code not in invites:
                await ctx.send(f"❌ Invite `{code}` is not configured as an invite role.")
                return

            role_ids = invites[code].get("role_ids", [])
            if role.id not in role_ids:
                await ctx.send(f"⚠️ Role {role.mention} is not assigned by invite `{code}`.")
                return

            role_ids.remove(role.id)
            if not role_ids:
                await ctx.send(
                    f"⚠️ Warning: Invite `{code}` now has no roles configured. "
                    f"Use `[p]inviterole addrole {code} <role>` or `[p]inviterole unlink {code}`."
                )

        await ctx.send(f"✅ Removed {role.mention} from invite `{code}`.")

    @inviterole.command(name="list")
    async def list_invites(self, ctx: commands.Context) -> None:
        """
        List all active invite role configurations in this server.
        """
        invites_dict = await self.config.guild(ctx.guild).invites()
        if not invites_dict:
            await ctx.send("ℹ️ No invite roles configured in this server. Use `[p]inviterole create` to start.")
            return

        try:
            live_invites = {inv.code: inv for inv in await ctx.guild.invites()}
        except Exception:
            live_invites = {}

        embed = discord.Embed(
            title=f"📋 Configured Invite Roles for {ctx.guild.name}",
            description=f"Total active configurations: **{len(invites_dict)}**",
            color=discord.Color.blurple(),
        )

        for code, data in list(invites_dict.items())[:25]:
            role_mentions = []
            for rid in data.get("role_ids", []):
                role = ctx.guild.get_role(rid)
                role_mentions.append(role.mention if role else f"`[Deleted Role {rid}]`")

            roles_str = ", ".join(role_mentions) or "None"
            channel_id = data.get("channel_id")
            ch_mention = f"<#{channel_id}>" if channel_id else "Unknown"

            live_inv = live_invites.get(code)
            current_uses = live_inv.uses if live_inv else "N/A"
            max_uses = data.get("max_uses", 0)
            uses_str = f"{current_uses} / {max_uses or '∞'}"
            total_joins = data.get("total_joined", 0)
            note = data.get("note", "").strip()

            field_val = (
                f"**Roles:** {roles_str}\n"
                f"**Channel:** {ch_mention}\n"
                f"**Uses:** {uses_str} (Tracked Joins: `{total_joins}`)\n"
                f"**Link:** https://discord.gg/{code}"
            )
            if note:
                field_val += f"\n**Note:** {note}"

            embed.add_field(name=f"Invite `{code}`", value=field_val, inline=False)

        await ctx.send(embed=embed)

    @inviterole.command(name="logchannel")
    async def set_log_channel(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        """
        Set or clear the logging channel for invite role joins.

        **Arguments:**
        - `channel`: The text channel to send join notifications to (omit to disable logging).

        **Example:**
        `[p]inviterole logchannel #member-logs`
        `[p]inviterole logchannel` (disables logging)
        """
        if channel:
            if not channel.permissions_for(ctx.guild.me).send_messages:
                await ctx.send(f"❌ I don't have permission to send messages in {channel.mention}.")
                return
            await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
            await ctx.send(f"✅ Invite role join events will now be logged to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel_id.set(None)
            await ctx.send("✅ Invite role logging has been disabled.")

    @inviterole.command(name="toggledm")
    async def toggle_dm(self, ctx: commands.Context) -> None:
        """
        Toggle sending a welcome DM to members who join via an invite role.
        """
        curr = await self.config.guild(ctx.guild).dm_user()
        new_val = not curr
        await self.config.guild(ctx.guild).dm_user.set(new_val)
        status = "enabled" if new_val else "disabled"
        await ctx.send(f"✅ Welcome DM for invite role joiners is now **{status}**.")

    @inviterole.command(name="dmmsg")
    async def set_dm_message(self, ctx: commands.Context, *, message: str) -> None:
        """
        Set custom welcome DM message template.

        Supported variables:
        - `{user}`: Full username (e.g. John#1234 or john)
        - `{mention}`: Member mention (@user)
        - `{guild}`: Server name
        - `{roles}`: List of roles assigned
        - `{code}`: The invite code used

        **Example:**
        `[p]inviterole dmmsg Welcome {mention} to {guild}! You have been granted the {roles} role.`
        """
        await self.config.guild(ctx.guild).dm_message.set(message)
        await ctx.send(f"✅ Custom DM message template updated:\n>>> {message}")

    @inviterole.command(name="sync")
    async def sync_cache(self, ctx: commands.Context) -> None:
        """
        Force a refresh of the server's invite cache from Discord.
        """
        async with ctx.typing():
            cache = await self._fetch_and_cache_guild_invites(ctx.guild)
        await ctx.send(f"✅ Successfully refreshed invite cache. Currently tracking **{len(cache)}** live invites.")

    @inviterole.command(name="status")
    async def show_status(self, ctx: commands.Context) -> None:
        """
        Show diagnostic and permission status for the InviteRole cog.
        """
        guild = ctx.guild
        me = guild.me

        can_manage_guild = me.guild_permissions.manage_guild
        can_manage_roles = me.guild_permissions.manage_roles
        conf = await self.config.guild(guild).all()
        inv_count = len(conf.get("invites", {}))
        cached_count = len(self._invite_cache.get(guild.id, {}))
        log_ch_id = conf.get("log_channel_id")
        log_ch = guild.get_channel(log_ch_id) if log_ch_id else None
        dm_enabled = conf.get("dm_user", False)

        embed = discord.Embed(
            title=f"⚙️ InviteRole Status — {guild.name}",
            color=discord.Color.green() if (can_manage_guild and can_manage_roles) else discord.Color.red(),
        )
        embed.add_field(
            name="Permissions",
            value=(
                f"{'✅' if can_manage_guild else '❌'} Manage Server (read invites)\n"
                f"{'✅' if can_manage_roles else '❌'} Manage Roles (assign roles)"
            ),
            inline=False,
        )
        embed.add_field(name="Configured Links", value=str(inv_count), inline=True)
        embed.add_field(name="Cached Discord Invites", value=str(cached_count), inline=True)
        embed.add_field(name="Logging Channel", value=log_ch.mention if log_ch else "Disabled", inline=True)
        embed.add_field(name="Welcome DM", value="Enabled" if dm_enabled else "Disabled", inline=True)
        embed.add_field(name="Bot Top Role", value=me.top_role.mention, inline=True)

        await ctx.send(embed=embed)

    # -------------------------------------------------------------------------
    # Helper Validation
    # -------------------------------------------------------------------------

    def _validate_role_assignable(self, ctx: commands.Context, role: discord.Role) -> Optional[str]:
        """Verify role can be safely managed and assigned."""
        guild = ctx.guild
        me = guild.me

        if role == guild.default_role:
            return "Cannot assign the `@everyone` role."

        if role.is_bot_managed() or role.is_integration():
            return f"Role {role.mention} is managed by an external bot or integration and cannot be assigned."

        bot_pos = getattr(me.top_role, "position", 0)
        role_pos = getattr(role, "position", 0)

        if role_pos >= bot_pos:
            return (
                f"Role {role.mention} is higher than or equal to my highest role ({me.top_role.mention}). "
                f"Please move my role above {role.mention} in Server Settings -> Roles."
            )

        # Invoker hierarchy check (exempt guild owner and bot owner)
        if ctx.author.id != guild.owner_id and not self.bot.is_owner(ctx.author):
            author_pos = getattr(ctx.author.top_role, "position", 0)
            if role_pos >= author_pos:
                return (
                    f"Role {role.mention} is higher than or equal to your highest role ({ctx.author.top_role.mention})."
                )

        return None


async def setup(bot: Red) -> None:
    cog = InviteRole(bot)
    await bot.add_cog(cog)
