"""
InviteRole Cog for Red-DiscordBot.
Assign roles automatically to users who join via specific invite links,
and manage ThreadGates to automatically revoke temporary panel/ticket roles.
"""

import asyncio
from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union
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
    Assign roles automatically to users who join via specific Discord invites,
    and monitor ThreadGates to revoke temporary roles when threads close/delete.
    """

    __author__ = "Sablinova"
    __version__ = "1.1.0"

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
            "threadgates": {},
            # channel_id_str -> {
            #     "role_id": int,
            #     "created_by": int,
            #     "created_at": str,
            # }
            "active_thread_users": {},
            # thread_id_str -> {
            #     "user_ids": [int],
            #     "role_id": int,
            #     "parent_channel_id": int,
            #     "created_at": str,
            # }
        }

        self.config.register_guild(**default_guild)

        # In-memory caches for high-performance event lookups
        self._invite_cache: Dict[int, Dict[str, int]] = {}
        self._threadgate_cache: Dict[int, Dict[int, int]] = {}  # guild_id -> {parent_channel_id: role_id}
        self._active_thread_users: Dict[int, Dict[int, List[int]]] = {}  # guild_id -> {thread_id: [user_ids]}
        self._cache_locks: Dict[int, asyncio.Lock] = {}
        self._init_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        """Start background initialization of invite and threadgate caches."""
        self._init_task = asyncio.create_task(self._initialize_cache())
        try:
            self.bot.register_rpc_handler(self._rpc_get_status)
            self.bot.register_rpc_handler(self._rpc_set_threadgate)
            self.bot.register_rpc_handler(self._rpc_create_invite)
            self.bot.register_rpc_handler(self._rpc_set_log_channel)
            self.bot.register_rpc_handler(self._rpc_inspect_channel)
            self.bot.register_rpc_handler(self._rpc_fix_channel_permissions)
            self.bot.register_rpc_handler(self._rpc_check_member)
        except Exception as rpc_err:
            logger.debug("Could not register InviteRole RPC handlers: %s", rpc_err)

    def cog_unload(self) -> None:
        """Cancel background tasks on unload."""
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
        try:
            self.bot.unregister_rpc_handler(self._rpc_get_status)
            self.bot.unregister_rpc_handler(self._rpc_set_threadgate)
            self.bot.unregister_rpc_handler(self._rpc_create_invite)
            self.bot.unregister_rpc_handler(self._rpc_set_log_channel)
            self.bot.unregister_rpc_handler(self._rpc_inspect_channel)
            self.bot.unregister_rpc_handler(self._rpc_fix_channel_permissions)
            self.bot.unregister_rpc_handler(self._rpc_check_member)
        except Exception:
            pass

    async def _check_oauth_verified(self, user_id: int) -> bool:
        """Query the verification backend to see if user has verified FMHY membership."""
        verify_url = "https://fmhy.pubslounge.xyz/api/is_verified/"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{verify_url}{user_id}", timeout=aiohttp.ClientTimeout(total=4)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return bool(data.get("verified", False))
        except Exception as exc:
            logger.debug("Failed checking verification status for %s: %s", user_id, exc)
        return False

    async def _rpc_get_status(self, guild_id: int = 1265271912037089312) -> Dict[str, Any]:
        """RPC handler to verify live in-memory state and config."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"error": f"Guild {guild_id} not found on bot"}
        conf = await self.config.guild(guild).all()
        return {
            "guild_id": guild_id,
            "guild_name": guild.name,
            "log_channel_id": conf.get("log_channel_id"),
            "in_memory_threadgate_cache": {str(k): v for k, v in self._threadgate_cache.get(guild_id, {}).items()},
            "in_memory_invite_cache_size": len(self._invite_cache.get(guild_id, {})),
            "config_threadgates": conf.get("threadgates", {}),
            "config_invites": conf.get("invites", {}),
        }

    async def _rpc_set_log_channel(self, guild_id: int, channel_id: Optional[int]) -> Dict[str, Any]:
        """RPC handler to configure the logging channel."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"error": f"Guild {guild_id} not found on bot"}
        channel = guild.get_channel(channel_id) if channel_id else None
        await self.config.guild(guild).log_channel_id.set(channel_id)
        return {
            "status": "ok",
            "guild_id": guild_id,
            "channel_id": channel_id,
            "channel_name": channel.name if channel else None,
        }

    async def _rpc_inspect_channel(self, channel_id: int = 1565423109584978071) -> Dict[str, Any]:
        """Inspect a channel, its type, and bot permissions."""
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return {"error": f"Channel {channel_id} not found by bot.get_channel"}
        guild = channel.guild
        perms = channel.permissions_for(guild.me)
        g_perms = guild.me.guild_permissions

        threads = []
        if hasattr(channel, "threads"):
            threads = [{"id": t.id, "name": t.name, "archived": t.archived, "locked": t.locked} for t in channel.threads]
        return {
            "name": channel.name,
            "id": channel.id,
            "type": str(type(channel)),
            "parent_id": getattr(channel, "parent_id", None),
            "channel_view_channel": perms.view_channel,
            "channel_manage_threads": perms.manage_threads,
            "channel_read_message_history": perms.read_message_history,
            "guild_admin": g_perms.administrator,
            "guild_manage_channels": g_perms.manage_channels,
            "guild_manage_roles": g_perms.manage_roles,
            "bot_top_role": guild.me.top_role.name,
            "active_threads_in_cache": threads,
        }

    async def _rpc_fix_channel_permissions(self, channel_id: int = 1565423109584978071) -> Dict[str, Any]:
        """Grant bot View Channel and Manage Threads on the ThreadGate channel."""
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return {"error": f"Channel {channel_id} not found"}
        guild = channel.guild
        me = guild.me
        try:
            await channel.set_permissions(
                me,
                view_channel=True,
                read_message_history=True,
                manage_threads=True,
                send_messages_in_threads=True,
                reason="InviteRole ThreadGate: Auto-grant bot visibility to monitor threads",
            )
            perms = channel.permissions_for(me)
            return {
                "status": "ok",
                "channel_name": channel.name,
                "channel_id": channel.id,
                "view_channel": perms.view_channel,
                "manage_threads": perms.manage_threads,
                "read_message_history": perms.read_message_history,
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def _rpc_check_member(
        self, guild_id: int = 1265271912037089312, user_id: int = 907142140298153994
    ) -> Dict[str, Any]:
        """Check member roles and status."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"error": f"Guild {guild_id} not found"}
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                return {"error": f"Member {user_id} not found in guild"}
        return {
            "user_id": member.id,
            "name": str(member),
            "display_name": member.display_name,
            "roles": [{"id": r.id, "name": r.name} for r in member.roles],
            "has_fmhy": any(r.id == 1565428384937287821 for r in member.roles),
        }

    async def _rpc_set_threadgate(self, guild_id: int, channel_id: int, role_id: int) -> Dict[str, Any]:
        """RPC handler to directly set a threadgate in live bot memory and config."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"error": f"Guild {guild_id} not found on bot"}
        async with self.config.guild(guild).threadgates() as gates:
            gates[str(channel_id)] = {
                "role_id": role_id,
                "created_by": 426878496468500493,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        self._threadgate_cache.setdefault(guild_id, {})[channel_id] = role_id
        return {
            "status": "ok",
            "guild_id": guild_id,
            "channel_id": channel_id,
            "role_id": role_id,
            "active_cache": {str(k): v for k, v in self._threadgate_cache.get(guild_id, {}).items()},
        }

    async def _rpc_create_invite(
        self,
        guild_id: int = 1265271912037089312,
        role_id: int = 1565428384937287821,
        channel_id: Optional[int] = None,
        max_uses: int = 0,
        max_age: int = 0,
        note: str = "FMHY Auto Access Link",
    ) -> Dict[str, Any]:
        """RPC handler to generate an invite role link directly."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"error": f"Guild {guild_id} not found"}

        role = guild.get_role(role_id)
        if not role:
            return {"error": f"Role {role_id} not found in guild"}

        target_channel = None
        if channel_id:
            target_channel = guild.get_channel(channel_id)
        
        if not target_channel:
            target_channel = guild.rules_channel or guild.system_channel
            if not target_channel:
                for ch in guild.text_channels:
                    if ch.permissions_for(guild.me).create_instant_invite:
                        target_channel = ch
                        break

        if not target_channel or not target_channel.permissions_for(guild.me).create_instant_invite:
            return {"error": "No valid channel with create_instant_invite permissions found"}

        invite = await target_channel.create_invite(
            max_age=max_age,
            max_uses=max_uses,
            unique=True,
            reason=f"InviteRole RPC generated for @{role.name}",
        )

        async with self.config.guild(guild).invites() as invites:
            invites[invite.code] = {
                "role_ids": [role.id],
                "channel_id": target_channel.id,
                "max_uses": max_uses,
                "max_age": max_age,
                "created_by": 426878496468500493,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "total_joined": 0,
                "note": note,
            }

        self._invite_cache.setdefault(guild.id, {})[invite.code] = 0

        return {
            "status": "ok",
            "code": invite.code,
            "url": f"https://discord.gg/{invite.code}",
            "role_name": role.name,
            "role_id": role.id,
            "channel_name": target_channel.name,
            "channel_id": target_channel.id,
            "max_uses": max_uses,
            "max_age": max_age,
            "note": note,
        }

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

    async def _load_guild_threadgates(self, guild: discord.Guild) -> None:
        """Load configured threadgates and active tracked thread users into memory."""
        conf = await self.config.guild(guild).all()
        tg_data = conf.get("threadgates", {})
        self._threadgate_cache[guild.id] = {
            int(ch_id): data["role_id"]
            for ch_id, data in tg_data.items()
            if isinstance(data, dict) and "role_id" in data
        }
        atu_data = conf.get("active_thread_users", {})
        self._active_thread_users[guild.id] = {
            int(th_id): data.get("user_ids", [])
            for th_id, data in atu_data.items()
            if isinstance(data, dict)
        }

    async def _initialize_cache(self) -> None:
        """Wait until bot is ready and cache invites & threadgates for all connected guilds."""
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self._fetch_and_cache_guild_invites(guild)
                await self._load_guild_threadgates(guild)
            except Exception as exc:
                logger.error("Error initialising cache for guild %s: %s", guild.id, exc)

    # -------------------------------------------------------------------------
    # Event Listeners - Invite Tracking
    # -------------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Refresh invite & threadgate caches when bot reconnections occur."""
        for guild in self.bot.guilds:
            try:
                await self._fetch_and_cache_guild_invites(guild)
                await self._load_guild_threadgates(guild)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Cache invites and threadgates for a newly joined guild."""
        await self._fetch_and_cache_guild_invites(guild)
        await self._load_guild_threadgates(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Clean up cache when leaving a guild."""
        self._invite_cache.pop(guild.id, None)
        self._threadgate_cache.pop(guild.id, None)
        self._active_thread_users.pop(guild.id, None)
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
                        if max_uses > 0 and (prev_uses == max_uses - 1 or max_uses == 1):
                            used_code = code
                            break
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
        bot_top_pos = getattr(guild.me.top_role, "position", 0)

        for rid in role_ids:
            role = guild.get_role(rid)
            if not role:
                continue
            if role == guild.default_role:
                continue
            if role.is_bot_managed() or role.is_integration():
                continue
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

        # Verification check: Role 1565428384937287821 requires prior FMHY OAuth2 verification
        FMHY_ROLE_ID = 1565428384937287821
        log_channel_id = guild_conf.get("log_channel_id")
        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None

        if any(r.id == FMHY_ROLE_ID for r in roles_to_assign):
            is_verified = await self._check_oauth_verified(member.id)
            if not is_verified:
                # Remove FMHY from immediate roles
                roles_to_assign = [r for r in roles_to_assign if r.id != FMHY_ROLE_ID]

                # Send verification DM to user
                verify_msg = (
                    f"Welcome to **Pub's Lounge**, {member.mention}! 👋\n\n"
                    f"To unlock the FMHY channels and receive your **@Fmhy** role, please verify your Discord account here:\n"
                    f"👉 https://fmhy.pubslounge.xyz/verify"
                )
                try:
                    await member.send(verify_msg)
                    logger.info("Sent verification DM to %s (%s)", member, member.id)
                except (discord.Forbidden, discord.HTTPException):
                    pass

                # Log to log_channel
                if log_channel and log_channel.permissions_for(guild.me).send_messages:
                    emb = discord.Embed(
                        title="🔗 Member Joined (Verification Required)",
                        description=f"{member.mention} (`{member.id}`) joined via `{used_code}`.\nSent DM link for **fmhy.pubslounge.xyz** verification.",
                        color=discord.Color.gold(),
                        timestamp=datetime.now(timezone.utc),
                    )
                    emb.set_thumbnail(url=member.display_avatar.url)
                    try:
                        await log_channel.send(embed=emb)
                    except discord.HTTPException:
                        pass

                if not roles_to_assign:
                    # No other roles to assign, update counter and finish
                    inv_data["total_joined"] = inv_data.get("total_joined", 0) + 1
                    await self.config.guild(guild).invites.set(configured_invites)
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
        if log_channel:
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
    # Event Listeners - ThreadGate
    # -------------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        """Track new threads created in monitored threadgate channels."""
        if not thread.guild:
            return
        guild = thread.guild
        gates = self._threadgate_cache.get(guild.id, {})
        if not gates or thread.parent_id not in gates:
            return

        role_id = gates[thread.parent_id]
        asyncio.create_task(self._process_new_thread_after_delay(thread, role_id))

    async def _process_new_thread_after_delay(
        self, thread: discord.Thread, role_id: int, delay: float = 2.5
    ) -> None:
        """Wait briefly for ticket/panel bot to populate thread members, then register candidates."""
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            guild = thread.guild
            tracked_members: Set[int] = set()

            if thread.owner_id:
                owner = guild.get_member(thread.owner_id)
                if owner and not owner.bot and any(r.id == role_id for r in owner.roles):
                    tracked_members.add(owner.id)

            try:
                t_members = await thread.fetch_members()
                for tm in t_members:
                    m = guild.get_member(tm.id)
                    if m and not m.bot and any(r.id == role_id for r in m.roles):
                        tracked_members.add(m.id)
            except Exception as exc:
                logger.debug("Could not fetch thread members for thread %s: %s", thread.id, exc)

            if tracked_members:
                await self._register_thread_users(
                    guild, thread.id, thread.parent_id, role_id, list(tracked_members)
                )
        except Exception as exc:
            logger.error("Error processing new thread %s in threadgate: %s", thread.id, exc)

    @commands.Cog.listener()
    async def on_thread_member_join(self, thread_member: discord.ThreadMember) -> None:
        """Track candidate member when added to a monitored thread."""
        thread = thread_member.thread
        if not thread or not thread.guild:
            return
        guild = thread.guild
        gates = self._threadgate_cache.get(guild.id, {})
        if not gates or thread.parent_id not in gates:
            return

        role_id = gates[thread.parent_id]
        member = guild.get_member(thread_member.id)
        if member and not member.bot and any(r.id == role_id for r in member.roles):
            await self._register_thread_users(guild, thread.id, thread.parent_id, role_id, [member.id])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Track participants and mentions in monitored threadgate threads."""
        if not message.guild or not isinstance(message.channel, discord.Thread):
            return

        thread = message.channel
        guild = message.guild
        gates = self._threadgate_cache.get(guild.id, {})
        if not gates or thread.parent_id not in gates:
            return

        role_id = gates[thread.parent_id]
        new_users: Set[int] = set()

        # 1. Author if human member with target role
        if not message.author.bot and isinstance(message.author, discord.Member):
            if any(r.id == role_id for r in message.author.roles):
                new_users.add(message.author.id)

        # 2. Mentions in message (e.g. ticket bot mentions user)
        for user in message.mentions:
            if not user.bot:
                m = guild.get_member(user.id)
                if m and any(r.id == role_id for r in m.roles):
                    new_users.add(m.id)

        if new_users:
            await self._register_thread_users(guild, thread.id, thread.parent_id, role_id, list(new_users))

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread) -> None:
        """Detect thread closure / archive / lock and revoke the gate role."""
        if not after.guild:
            return
        guild = after.guild
        gates = self._threadgate_cache.get(guild.id, {})
        if not gates or after.parent_id not in gates:
            return

        role_id = gates[after.parent_id]
        is_closing = (not before.archived and after.archived) or (not before.locked and after.locked)
        if is_closing:
            # If no users were tracked yet, inspect live members before closing
            guild_atu = self._active_thread_users.get(guild.id, {})
            if after.id not in guild_atu or not guild_atu[after.id]:
                try:
                    t_members = await after.fetch_members()
                    found_users = [
                        tm.id for tm in t_members
                        if (m := guild.get_member(tm.id)) and not m.bot and any(r.id == role_id for r in m.roles)
                    ]
                    if found_users:
                        await self._register_thread_users(guild, after.id, after.parent_id, role_id, found_users)
                except Exception:
                    pass

            await self._revoke_thread_roles(
                guild=guild,
                thread_id=after.id,
                default_role_id=role_id,
                reason=f"InviteRole ThreadGate: Thread #{after.name} ({after.id}) closed/archived in parent channel",
            )

    @commands.Cog.listener()
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        """Detect thread deletion and revoke the gate role from tracked users."""
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        gates = self._threadgate_cache.get(guild.id, {})
        role_id = gates.get(payload.parent_id)

        await self._revoke_thread_roles(
            guild=guild,
            thread_id=payload.thread_id,
            default_role_id=role_id,
            reason=f"InviteRole ThreadGate: Thread #{payload.thread_id} deleted in parent channel",
        )

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread) -> None:
        """Backup thread deletion listener."""
        if not thread.guild:
            return
        guild = thread.guild
        gates = self._threadgate_cache.get(guild.id, {})
        role_id = gates.get(thread.parent_id)

        await self._revoke_thread_roles(
            guild=guild,
            thread_id=thread.id,
            default_role_id=role_id,
            reason=f"InviteRole ThreadGate: Thread #{thread.name} ({thread.id}) deleted in parent channel",
        )

    async def _register_thread_users(
        self,
        guild: discord.Guild,
        thread_id: int,
        parent_id: Optional[int],
        role_id: int,
        user_ids: List[int],
    ) -> None:
        """Register candidate users to be revoked when thread closes or is deleted."""
        guild_atu = self._active_thread_users.setdefault(guild.id, {})
        existing = set(guild_atu.get(thread_id, []))
        new_set = existing.union(user_ids)
        guild_atu[thread_id] = list(new_set)

        async with self.config.guild(guild).active_thread_users() as atu:
            record = atu.setdefault(str(thread_id), {
                "user_ids": [],
                "role_id": role_id,
                "parent_channel_id": parent_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            record["user_ids"] = list(new_set)

    async def _revoke_thread_roles(
        self,
        guild: discord.Guild,
        thread_id: int,
        default_role_id: Optional[int],
        reason: str,
    ) -> None:
        """Remove target role from tracked thread users upon thread deletion or archive."""
        guild_atu = self._active_thread_users.get(guild.id, {})
        tracked_user_ids = list(guild_atu.pop(thread_id, []))

        async with self.config.guild(guild).active_thread_users() as atu:
            saved_record = atu.pop(str(thread_id), None)
            if saved_record:
                if not tracked_user_ids:
                    tracked_user_ids = saved_record.get("user_ids", [])
                if not default_role_id:
                    default_role_id = saved_record.get("role_id")

        if not tracked_user_ids or not default_role_id:
            return

        target_role = guild.get_role(default_role_id)
        if not target_role:
            logger.warning("Target role %s for ThreadGate does not exist in %s", default_role_id, guild.name)
            return

        bot_pos = getattr(guild.me.top_role, "position", 0)
        role_pos = getattr(target_role, "position", 0)
        if role_pos >= bot_pos:
            logger.warning("Cannot revoke role %s: higher or equal to bot top role", target_role.name)
            return

        conf = await self.config.guild(guild).all()
        log_channel_id = conf.get("log_channel_id")
        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None

        for uid in tracked_user_ids:
            member = guild.get_member(uid)
            if not member:
                try:
                    member = await guild.fetch_member(uid)
                except Exception:
                    member = None

            if not member or target_role not in member.roles:
                continue

            try:
                await member.remove_roles(target_role, reason=reason)
                logger.info(
                    "Revoked role %s from %s (%s) — %s",
                    target_role.name,
                    member,
                    member.id,
                    reason,
                )

                if log_channel and log_channel.permissions_for(guild.me).send_messages:
                    try:
                        embed = discord.Embed(
                            title="🚪 ThreadGate Role Revoked",
                            description=f"{member.mention} (`{member.id}`) lost the **{target_role.name}** role.",
                            color=discord.Color.orange(),
                            timestamp=datetime.now(timezone.utc),
                        )
                        embed.add_field(name="Revoked Role", value=target_role.mention, inline=True)
                        embed.add_field(name="Thread ID", value=f"`{thread_id}`", inline=True)
                        embed.add_field(name="Reason", value=reason, inline=False)
                        embed.set_thumbnail(url=member.display_avatar.url)
                        await log_channel.send(embed=embed)
                    except Exception as le:
                        logger.error("Failed to send log message for ThreadGate revoke: %s", le)
            except Exception as re_err:
                logger.error("Failed to revoke role %s from %s: %s", target_role.name, member, re_err)

    # -------------------------------------------------------------------------
    # Commands - Inviterole Group
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
        Set or clear the logging channel for invite role joins and ThreadGate revokes.

        **Arguments:**
        - `channel`: The text channel to send notifications to (omit to disable logging).

        **Example:**
        `[p]inviterole logchannel #member-logs`
        `[p]inviterole logchannel` (disables logging)
        """
        if channel:
            if not channel.permissions_for(ctx.guild.me).send_messages:
                await ctx.send(f"❌ I don't have permission to send messages in {channel.mention}.")
                return
            await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
            await ctx.send(f"✅ Invite role events will now be logged to {channel.mention}.")
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
        Force a refresh of the server's invite and threadgate caches.
        """
        async with ctx.typing():
            cache = await self._fetch_and_cache_guild_invites(ctx.guild)
            await self._load_guild_threadgates(ctx.guild)
        await ctx.send(
            f"✅ Successfully refreshed caches. Currently tracking **{len(cache)}** live invites "
            f"and **{len(self._threadgate_cache.get(ctx.guild.id, {}))}** thread gate channels."
        )

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
        gates_count = len(conf.get("threadgates", {}))
        active_th_count = len(conf.get("active_thread_users", {}))
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
                f"{'✅' if can_manage_roles else '❌'} Manage Roles (assign & revoke roles)"
            ),
            inline=False,
        )
        embed.add_field(name="Configured Links", value=str(inv_count), inline=True)
        embed.add_field(name="Cached Discord Invites", value=str(cached_count), inline=True)
        embed.add_field(name="ThreadGates", value=f"{gates_count} channels ({active_th_count} active threads)", inline=True)
        embed.add_field(name="Logging Channel", value=log_ch.mention if log_ch else "Disabled", inline=True)
        embed.add_field(name="Welcome DM", value="Enabled" if dm_enabled else "Disabled", inline=True)
        embed.add_field(name="Bot Top Role", value=me.top_role.mention, inline=True)

        await ctx.send(embed=embed)

    # -------------------------------------------------------------------------
    # Commands - ThreadGate Subgroup
    # -------------------------------------------------------------------------

    @inviterole.group(name="threadgate", aliases=["tgate", "gate"])
    async def threadgate(self, ctx: commands.Context) -> None:
        """
        Configure thread gates that revoke a role when threads close or are deleted.

        Ideal for panel/ticket access where users get access via an invite,
        open a thread in a panel channel, and must lose their role when the thread finishes.
        """
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @threadgate.command(name="add")
    async def threadgate_add(
        self,
        ctx: commands.Context,
        channel: Union[discord.TextChannel, discord.ForumChannel],
        role: discord.Role,
    ) -> None:
        """
        Add a channel to the thread gate system.

        **Arguments:**
        - `channel`: The text or forum channel containing the panel/threads.
        - `role`: The role that will be automatically revoked when a thread closes or is deleted.

        **Example:**
        `[p]inviterole threadgate add #fmhy-panel @fmhy`
        `[p]inviterole threadgate add 1565423109584978071 1565428384937287821`
        """
        val_err = self._validate_role_assignable(ctx, role)
        if val_err:
            await ctx.send(f"❌ {val_err}")
            return

        async with self.config.guild(ctx.guild).threadgates() as gates:
            gates[str(channel.id)] = {
                "role_id": role.id,
                "created_by": ctx.author.id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        self._threadgate_cache.setdefault(ctx.guild.id, {})[channel.id] = role.id

        # Verify and ensure bot has visibility into the channel and its threads
        perm_note = None
        ch_perms = channel.permissions_for(ctx.guild.me)
        if not ch_perms.view_channel or not ch_perms.manage_threads or not ch_perms.read_message_history:
            if ctx.guild.me.guild_permissions.manage_channels:
                try:
                    await channel.set_permissions(
                        ctx.guild.me,
                        view_channel=True,
                        read_message_history=True,
                        manage_threads=True,
                        send_messages_in_threads=True,
                        reason="InviteRole ThreadGate: Auto-grant bot visibility to monitor threads",
                    )
                    perm_note = "✅ Auto-granted bot **View Channel** and **Manage Threads** permissions on this channel."
                except Exception as pe:
                    perm_note = f"⚠️ Could not auto-grant bot permissions ({pe}). Please manually grant the bot **View Channel** and **Manage Threads** in channel settings."
            else:
                perm_note = "⚠️ **Warning:** The bot is currently missing **View Channel** or **Manage Threads** in this channel. Please grant them in Channel Settings so the bot can receive thread events."

        embed = discord.Embed(
            title="🚪 ThreadGate Configured",
            description=(
                f"Any member with {role.mention} who enters or creates a thread in {channel.mention} "
                f"will automatically **lose {role.mention}** when that thread is **closed, archived, or deleted**."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(name="📍 Monitored Channel", value=f"{channel.mention} (`{channel.id}`)", inline=True)
        embed.add_field(name="🎭 Role to Revoke", value=f"{role.mention} (`{role.id}`)", inline=True)
        if perm_note:
            embed.add_field(name="🔑 Permissions Status", value=perm_note, inline=False)
        embed.set_footer(text=f"Configured by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @threadgate.command(name="remove")
    async def threadgate_remove(
        self,
        ctx: commands.Context,
        channel: Union[discord.TextChannel, discord.ForumChannel],
    ) -> None:
        """
        Remove a channel from the thread gate system.

        **Example:**
        `[p]inviterole threadgate remove #fmhy-panel`
        """
        async with self.config.guild(ctx.guild).threadgates() as gates:
            if str(channel.id) not in gates:
                await ctx.send(f"❌ {channel.mention} is not configured as a thread gate channel.")
                return
            gates.pop(str(channel.id))

        if ctx.guild.id in self._threadgate_cache:
            self._threadgate_cache[ctx.guild.id].pop(channel.id, None)

        await ctx.send(f"✅ Successfully removed {channel.mention} from thread gate monitoring.")

    @threadgate.command(name="list")
    async def threadgate_list(self, ctx: commands.Context) -> None:
        """
        List all active thread gate channels and their target roles in this server.
        """
        gates = await self.config.guild(ctx.guild).threadgates()
        if not gates:
            await ctx.send("ℹ️ No thread gates configured in this server. Use `[p]inviterole threadgate add` to set one up.")
            return

        embed = discord.Embed(
            title=f"🚪 ThreadGates for {ctx.guild.name}",
            description=f"Total monitored channels: **{len(gates)}**",
            color=discord.Color.blurple(),
        )

        guild_atu = self._active_thread_users.get(ctx.guild.id, {})

        for ch_id_str, data in gates.items():
            ch = ctx.guild.get_channel(int(ch_id_str))
            ch_str = ch.mention if ch else f"`[Deleted Channel {ch_id_str}]`"
            role = ctx.guild.get_role(data.get("role_id", 0))
            role_str = role.mention if role else f"`[Deleted Role {data.get('role_id')}]`"

            # Count active threads for this channel
            active_count = sum(1 for tid in guild_atu.keys() if (th := ctx.guild.get_thread(tid)) and th.parent_id == int(ch_id_str))

            embed.add_field(
                name=f"Channel {ch_str}",
                value=f"**Revoke Role:** {role_str}\n**Active Tracked Threads:** `{active_count}`",
                inline=False,
            )

        await ctx.send(embed=embed)

    # -------------------------------------------------------------------------
    # Helper Validation
    # -------------------------------------------------------------------------

    def _validate_role_assignable(self, ctx: commands.Context, role: discord.Role) -> Optional[str]:
        """Verify role can be safely managed, assigned, or revoked."""
        guild = ctx.guild
        me = guild.me

        if role == guild.default_role:
            return "Cannot assign or revoke the `@everyone` role."

        if role.is_bot_managed() or role.is_integration():
            return f"Role {role.mention} is managed by an external bot or integration and cannot be modified."

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
