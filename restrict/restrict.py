"""
Restrict Cog for Red-DiscordBot.
Comprehensive user, channel, and role content restriction system.
Allows administrators to restrict GIFs, media, links, stickers, and reactions with
instant silent auto-deletion, configurable notices, temporary durations, mod-logging, and escalation controls.
"""

import asyncio
from datetime import datetime, timezone
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import bold, box, humanize_timedelta, inline, pagify

log = logging.getLogger("red.sablinova.restrict")

IDENTIFIER = 928471928471

# Regular expression definitions for content detection
TENOR_REGEX = re.compile(
    r"\bhttps?://(?:www\.)?(?:media\.)?(?:c\.)?tenor\.com/(?:view/|[a-zA-Z0-9_.-]+)",
    flags=re.IGNORECASE | re.UNICODE,
)
GIPHY_REGEX = re.compile(
    r"\bhttps?://(?:www\.)?(?:media\.)?(?:i\.)?giphy\.com/(?:gifs/|[a-zA-Z0-9_.-]+)",
    flags=re.IGNORECASE | re.UNICODE,
)
GIF_URL_REGEX = re.compile(
    r"\bhttps?://\S+?\.(?:gif|gifv)(?:\?\S*)?(?=\s|$|[>\"'])",
    flags=re.IGNORECASE | re.UNICODE,
)
IMAGE_URL_REGEX = re.compile(
    r"\bhttps?://\S+?\.(?:png|jpe?g|webp|bmp)(?:\?\S*)?(?=\s|$|[>\"'])",
    flags=re.IGNORECASE | re.UNICODE,
)
VIDEO_URL_REGEX = re.compile(
    r"\bhttps?://\S+?\.(?:mp4|webm|mov|mkv|avi)(?:\?\S*)?(?=\s|$|[>\"'])|\bhttps?://(?:www\.)?(?:youtube\.com|youtu\.be|tiktok\.com|streamable\.com|vimeo\.com)/\S+",
    flags=re.IGNORECASE | re.UNICODE,
)
LINK_REGEX = re.compile(
    r"\bhttps?://\S+",
    flags=re.IGNORECASE | re.UNICODE,
)
INVITE_REGEX = re.compile(
    r"\b(?:https?://)?(?:www\.)?(?:discord(?:\.gg|(?:app)?\.com/invite)/[a-zA-Z0-9_-]+)",
    flags=re.IGNORECASE | re.UNICODE,
)

VALID_TYPES = {
    "gif": "GIFs (Tenor, Giphy, direct .gif/.gifv links, attachments, embeds)",
    "image": "Images (PNG, JPG, WEBP attachments & links)",
    "video": "Videos (MP4, WEBM, MOV attachments & streaming links)",
    "file": "Files & Attachments of any type",
    "link": "External Web Links & URLs",
    "invite": "Discord Server Invites",
    "sticker": "Discord Stickers",
    "reaction": "Emoji Reactions",
    "all": "All media, links, invites, files, and stickers",
}


def parse_duration(duration_str: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
    """
    Parses a human-readable duration string into an expiration timestamp.
    Supports: 10s, 5m, 2h, 1d, 7d, 1w, or permanent ('perm', 'forever', None).
    Returns (expiration_timestamp, remaining_reason_text).
    """
    if not duration_str:
        return None, None

    raw = duration_str.strip()
    if raw.lower() in ("perm", "permanent", "forever", "none", "0"):
        return None, None

    # Check leading token for duration
    parts = raw.split(maxsplit=1)
    cand = parts[0]
    reason = parts[1] if len(parts) > 1 else None

    units = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }

    m = re.match(r"^(\d+)([smhdw])$", cand.lower())
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        seconds = amount * units[unit]
        expires_at = time.time() + seconds
        return expires_at, reason

    # If first token is not a duration, the entire string is the reason
    return None, raw


class Restrict(commands.Cog):
    """
    Modular content and user restriction moderation cog for Red-DiscordBot.
    Restrict specific users, channels, or roles from sending GIFs, media, links, stickers, and reactions.
    Features instant, silent auto-deletion by default, configurable notifications, temporary durations,
    audit mod-logging, and repeat-spam escalation controls.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=IDENTIFIER, force_registration=True)

        default_guild = {
            "modlog_channel": None,
            "notify_mode": "silent",  # Options: 'silent', 'channel', 'dm' (default: silent)
            "autodelete_delay": 5,    # Seconds before channel warning deletes itself
            "escalate_enabled": False,
            "escalate_attempts": 3,   # Offenses within window
            "escalate_window": 300,   # 5-minute sliding window (seconds)
            "escalate_timeout": 300,  # 5-minute timeout duration (seconds)
            "users": {},              # {user_id_str: {type: {expires: ts, reason: str, moderator: id}}}
            "channels": {},           # {channel_id_str: {type: {expires: ts, reason: str, moderator: id}}}
            "roles": {},              # {role_id_str: {type: {expires: ts, reason: str, moderator: id}}}
        }
        self.config.register_guild(**default_guild)

        # In-memory high-speed cache: guild_id -> {"users": {...}, "channels": {...}, "roles": {...}}
        self._cache: Dict[int, Dict[str, Dict[int, Dict[str, Any]]]] = {}
        # Tracking recent violations for escalation: guild_id -> user_id -> [timestamps]
        self._violations: Dict[int, Dict[int, List[float]]] = {}

    async def cog_load(self) -> None:
        """Populates in-memory cache from config on cog load and starts background loop."""
        await self._build_cache()
        if not self.cleanup_task.is_running():
            self.cleanup_task.start()

    def cog_unload(self) -> None:
        """Cancels background tasks on cog unload."""
        self.cleanup_task.cancel()

    async def _build_cache(self) -> None:
        """Reads all guild restriction data into fast in-memory dictionaries."""
        all_guilds = await self.config.all_guilds()
        new_cache = {}
        for gid, gdata in all_guilds.items():
            users_map: Dict[int, Dict[str, Any]] = {}
            for uid_str, types in gdata.get("users", {}).items():
                if types:
                    users_map[int(uid_str)] = types

            channels_map: Dict[int, Dict[str, Any]] = {}
            for cid_str, types in gdata.get("channels", {}).items():
                if types:
                    channels_map[int(cid_str)] = types

            roles_map: Dict[int, Dict[str, Any]] = {}
            for rid_str, types in gdata.get("roles", {}).items():
                if types:
                    roles_map[int(rid_str)] = types

            if users_map or channels_map or roles_map:
                new_cache[gid] = {
                    "users": users_map,
                    "channels": channels_map,
                    "roles": roles_map,
                }
        self._cache = new_cache
        log.info(f"Loaded Restrict cache for {len(self._cache)} guilds.")

    @tasks.loop(seconds=30)
    async def cleanup_task(self) -> None:
        """Periodically purges expired temporary restrictions from cache and persistent config."""
        now = time.time()
        for gid, gdata in list(self._cache.items()):
            for scope in ("users", "channels", "roles"):
                for entity_id, types_dict in list(gdata.get(scope, {}).items()):
                    expired_types = [
                        t_name for t_name, info in types_dict.items()
                        if info.get("expires") and info["expires"] <= now
                    ]
                    for t_name in expired_types:
                        del types_dict[t_name]
                        # Remove from persistent config
                        async with self.config.guild_from_id(gid).get_attr(scope)() as conf_scope:
                            ent_str = str(entity_id)
                            if ent_str in conf_scope and t_name in conf_scope[ent_str]:
                                del conf_scope[ent_str][t_name]
                                if not conf_scope[ent_str]:
                                    del conf_scope[ent_str]

                    if not types_dict and entity_id in gdata.get(scope, {}):
                        del gdata[scope][entity_id]

    @cleanup_task.before_loop
    async def before_cleanup_task(self) -> None:
        await self.bot.wait_until_red_ready()

    # =========================================================================
    # Content Detection Engine
    # =========================================================================

    @staticmethod
    def contains_gif(message: discord.Message) -> bool:
        """Detects Tenor, Giphy, direct .gif/.gifv links, attachments, or gifv embeds."""
        content = message.content or ""
        # 1. Tenor or Giphy URL links
        if TENOR_REGEX.search(content) or GIPHY_REGEX.search(content) or GIF_URL_REGEX.search(content):
            return True

        # 2. Attachments
        for att in message.attachments:
            fname = getattr(att, "filename", "").lower()
            ctype = getattr(att, "content_type", "") or ""
            if fname.endswith((".gif", ".gifv")) or ctype.startswith("image/gif"):
                return True

        # 3. Embeds
        for emb in message.embeds:
            if getattr(emb, "type", "") == "gifv":
                return True
            for url_attr in [
                getattr(emb.thumbnail, "url", None),
                getattr(emb.image, "url", None),
                getattr(emb.video, "url", None),
                getattr(emb, "url", None),
            ]:
                if url_attr and (GIF_URL_REGEX.search(url_attr) or TENOR_REGEX.search(url_attr) or GIPHY_REGEX.search(url_attr)):
                    return True

        return False

    @staticmethod
    def contains_image(message: discord.Message) -> bool:
        """Detects non-gif image attachments, image embeds, or direct image links."""
        content = message.content or ""
        if IMAGE_URL_REGEX.search(content):
            return True

        for att in message.attachments:
            fname = getattr(att, "filename", "").lower()
            ctype = getattr(att, "content_type", "") or ""
            if fname.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")) or (ctype.startswith("image/") and not ctype.startswith("image/gif")):
                return True

        for emb in message.embeds:
            if getattr(emb, "type", "") == "image":
                return True

        return False

    @staticmethod
    def contains_video(message: discord.Message) -> bool:
        """Detects video attachments or video streaming links."""
        content = message.content or ""
        if VIDEO_URL_REGEX.search(content):
            return True

        for att in message.attachments:
            fname = getattr(att, "filename", "").lower()
            ctype = getattr(att, "content_type", "") or ""
            if fname.endswith((".mp4", ".webm", ".mov", ".mkv", ".avi")) or ctype.startswith("video/"):
                return True

        for emb in message.embeds:
            if getattr(emb, "type", "") == "video":
                return True

        return False

    @staticmethod
    def contains_file(message: discord.Message) -> bool:
        """Detects any file attachment uploads."""
        return len(message.attachments) > 0

    @staticmethod
    def contains_link(message: discord.Message) -> bool:
        """Detects any external HTTP/HTTPS link."""
        return bool(LINK_REGEX.search(message.content or ""))

    @staticmethod
    def contains_invite(message: discord.Message) -> bool:
        """Detects Discord invite URLs."""
        return bool(INVITE_REGEX.search(message.content or ""))

    @staticmethod
    def contains_sticker(message: discord.Message) -> bool:
        """Detects Discord stickers."""
        return bool(getattr(message, "stickers", None))

    def evaluate_content_violation(self, r_type: str, message: discord.Message) -> bool:
        """Evaluates whether a message violates a specific restriction type."""
        t = r_type.lower()
        if t == "gif":
            return self.contains_gif(message)
        elif t == "image":
            return self.contains_image(message)
        elif t == "video":
            return self.contains_video(message)
        elif t in ("file", "attachment"):
            return self.contains_file(message)
        elif t in ("link", "url"):
            return self.contains_link(message)
        elif t == "invite":
            return self.contains_invite(message)
        elif t == "sticker":
            return self.contains_sticker(message)
        elif t == "all":
            return (
                self.contains_gif(message)
                or self.contains_image(message)
                or self.contains_video(message)
                or self.contains_file(message)
                or self.contains_link(message)
                or self.contains_invite(message)
                or self.contains_sticker(message)
            )
        return False

    # =========================================================================
    # Event Listeners
    # =========================================================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Monitors incoming guild messages and enforces active content restrictions."""
        if not message.guild or message.author.bot:
            return

        guild = message.guild
        g_cache = self._cache.get(guild.id)
        if not g_cache:
            return

        now = time.time()
        active_violations: List[Tuple[str, Dict[str, Any], str]] = []  # (scope, info, type)

        # 1. Check User-level restrictions (direct user restrictions apply even to admins/mods if explicitly targeted)
        user_restrictions = g_cache.get("users", {}).get(author.id, {})
        for r_type, info in list(user_restrictions.items()):
            if info.get("expires") and info["expires"] <= now:
                continue
            if self.evaluate_content_violation(r_type, message):
                active_violations.append(("User", info, r_type))
                break

        # If not directly restricted as a user, check moderator/admin exemption for Channel & Role restrictions
        if not active_violations:
            if hasattr(author, "guild_permissions"):
                perms = author.guild_permissions
                if perms.administrator or perms.manage_messages or perms.manage_guild:
                    return

            # 2. Check Channel-level restrictions
            channel_restrictions = g_cache.get("channels", {}).get(message.channel.id, {})
            for r_type, info in list(channel_restrictions.items()):
                if info.get("expires") and info["expires"] <= now:
                    continue
                if self.evaluate_content_violation(r_type, message):
                    active_violations.append(("Channel", info, r_type))
                    break

        # 3. Check Role-level restrictions
        if not active_violations and hasattr(author, "roles"):
            for role in author.roles:
                role_restrictions = g_cache.get("roles", {}).get(role.id, {})
                for r_type, info in list(role_restrictions.items()):
                    if info.get("expires") and info["expires"] <= now:
                        continue
                    if self.evaluate_content_violation(r_type, message):
                        active_violations.append(("Role", info, r_type))
                        break
                if active_violations:
                    break

        if not active_violations:
            return

        scope, r_info, matched_type = active_violations[0]

        # 1. Instant Message Deletion
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            return
        except Exception as e:
            log.warning(f"Failed deleting restricted message from {author} in {message.channel}: {e}")
            return

        # 2. Handle Notifications (Default: Silent)
        g_conf = await self.config.guild(guild).all()
        notify_mode = g_conf.get("notify_mode", "silent").lower()
        type_label = matched_type.upper()

        if notify_mode == "channel":
            delay = g_conf.get("autodelete_delay", 5)
            warn_text = f"⚠️ {author.mention}, you are restricted from sending **{type_label}** content in this server."
            try:
                await message.channel.send(warn_text, delete_after=delay)
            except Exception:
                pass
        elif notify_mode == "dm":
            try:
                embed = discord.Embed(
                    title="⚠️ Message Restricted",
                    description=(
                        f"Your message in **{guild.name}** ({message.channel.mention}) was deleted because you are "
                        f"restricted from sending **{type_label}** content."
                    ),
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                )
                if r_info.get("reason"):
                    embed.add_field(name="Reason", value=r_info["reason"], inline=False)
                if r_info.get("expires"):
                    rem = humanize_timedelta(seconds=int(r_info["expires"] - now))
                    embed.add_field(name="Remaining Duration", value=rem, inline=False)
                await author.send(embed=embed)
            except Exception:
                pass

        # 3. Mod-Log Audit Embed
        modlog_cid = g_conf.get("modlog_channel")
        if modlog_cid:
            modlog_chan = guild.get_channel(modlog_cid)
            if modlog_chan and hasattr(modlog_chan, "send"):
                try:
                    log_embed = discord.Embed(
                        title=f"🛡️ Restricted Content Deleted [{type_label}]",
                        color=discord.Color.orange(),
                        timestamp=datetime.now(timezone.utc),
                    )
                    log_embed.add_field(name="Offending User", value=f"{author} (`{author.id}`)", inline=True)
                    log_embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                    log_embed.add_field(name="Restriction Scope", value=f"{scope}-Level", inline=True)
                    if message.content:
                        clean_c = message.clean_content[:500] if len(message.clean_content) > 500 else message.clean_content
                        log_embed.add_field(name="Content Preview", value=clean_c or "[No Text]", inline=False)
                    if message.attachments:
                        att_names = ", ".join(getattr(a, "filename", "file") for a in message.attachments)
                        log_embed.add_field(name="Attachments", value=att_names, inline=False)
                    await modlog_chan.send(embed=log_embed)
                except Exception as log_err:
                    log.debug(f"Failed sending modlog embed: {log_err}")

        # 4. Repeat Offender Escalation (Auto-Timeout)
        if g_conf.get("escalate_enabled", False):
            await self._handle_escalation(guild, author, g_conf)

    async def _handle_escalation(
        self,
        guild: discord.Guild,
        author: Union[discord.Member, discord.User],
        g_conf: Dict[str, Any],
    ) -> None:
        """Applies automatic timeout if a restricted user repeatedly spams restricted content."""
        if not isinstance(author, discord.Member):
            return

        uid = author.id
        gid = guild.id
        now = time.time()
        window = g_conf.get("escalate_window", 300)
        max_attempts = g_conf.get("escalate_attempts", 3)
        timeout_secs = g_conf.get("escalate_timeout", 300)

        if gid not in self._violations:
            self._violations[gid] = {}
        if uid not in self._violations[gid]:
            self._violations[gid][uid] = []

        # Filter timestamps outside sliding window
        self._violations[gid][uid] = [t for t in self._violations[gid][uid] if (now - t) <= window]
        self._violations[gid][uid].append(now)

        if len(self._violations[gid][uid]) >= max_attempts:
            # Clear violation tracker and apply timeout
            self._violations[gid][uid] = []
            try:
                until = discord.utils.utcnow() + datetime.timedelta(seconds=timeout_secs)
                await author.timeout(until, reason=f"Automatic Restrict Escalation: {max_attempts} offenses in {window}s")
                log.info(f"Escalated user {author} ({author.id}) in {guild.name}: Timed out for {timeout_secs}s.")
            except Exception as esc_err:
                log.warning(f"Failed applying escalation timeout to {author}: {esc_err}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Enforces reaction restrictions when restricted members attempt to react."""
        if not payload.guild_id or not payload.user_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        g_cache = self._cache.get(guild.id)
        if not g_cache:
            return

        member = payload.member or guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        now = time.time()
        is_restricted = False

        # User check (direct user restriction applies even to admins)
        u_res = g_cache.get("users", {}).get(member.id, {})
        for r_type in ("reaction", "all"):
            if r_type in u_res:
                info = u_res[r_type]
                if not info.get("expires") or info["expires"] > now:
                    is_restricted = True
                    break

        if not is_restricted:
            if hasattr(member, "guild_permissions"):
                perms = member.guild_permissions
                if perms.administrator or perms.manage_messages or perms.manage_guild:
                    return

            # Channel check
            c_res = g_cache.get("channels", {}).get(payload.channel_id, {})
            for r_type in ("reaction", "all"):
                if r_type in c_res:
                    info = c_res[r_type]
                    if not info.get("expires") or info["expires"] > now:
                        is_restricted = True
                        break

        # Role check
        if not is_restricted and hasattr(member, "roles"):
            for role in member.roles:
                r_res = g_cache.get("roles", {}).get(role.id, {})
                for r_type in ("reaction", "all"):
                    if r_type in r_res:
                        info = r_res[r_type]
                        if not info.get("expires") or info["expires"] > now:
                            is_restricted = True
                            break
                if is_restricted:
                    break

        if is_restricted:
            channel = guild.get_channel(payload.channel_id)
            if channel and hasattr(channel, "get_partial_message"):
                try:
                    partial_msg = channel.get_partial_message(payload.message_id)
                    await partial_msg.remove_reaction(payload.emoji, member)
                except Exception:
                    pass

    # =========================================================================
    # Restriction Management Commands
    # =========================================================================

    async def _apply_restriction(
        self,
        ctx: commands.Context,
        r_type: str,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
        duration_and_reason: Optional[str] = None,
    ) -> None:
        """Helper applying restriction to User, Channel, or Role."""
        t_key = r_type.lower()
        if t_key not in VALID_TYPES:
            valid_list = ", ".join(f"`{k}`" for k in sorted(VALID_TYPES.keys()))
            await ctx.send(f"❌ Invalid restriction type `{r_type}`. Valid options: {valid_list}")
            return

        expires_at, reason = parse_duration(duration_and_reason)
        reason_str = reason or "No reason provided."

        guild = ctx.guild
        gid = guild.id
        now = time.time()

        if isinstance(target, discord.Member):
            scope = "users"
            target_name = f"User **{target.display_name}** (`{target.id}`)"
        elif isinstance(target, discord.TextChannel):
            scope = "channels"
            target_name = f"Channel {target.mention}"
        elif isinstance(target, discord.Role):
            scope = "roles"
            target_name = f"Role **{target.name}**"
        else:
            await ctx.send("❌ Unsupported target. Must be a member, channel, or role.")
            return

        info_dict = {
            "expires": expires_at,
            "reason": reason_str,
            "moderator": ctx.author.id,
            "set_at": now,
        }

        # Update persistent config
        async with self.config.guild(guild).get_attr(scope)() as conf_scope:
            tid_str = str(target.id)
            if tid_str not in conf_scope:
                conf_scope[tid_str] = {}
            conf_scope[tid_str][t_key] = info_dict

        # Update in-memory cache
        if gid not in self._cache:
            self._cache[gid] = {"users": {}, "channels": {}, "roles": {}}
        if scope not in self._cache[gid]:
            self._cache[gid][scope] = {}
        if target.id not in self._cache[gid][scope]:
            self._cache[gid][scope][target.id] = {}
        self._cache[gid][scope][target.id][t_key] = info_dict

        dur_text = f"for {humanize_timedelta(seconds=int(expires_at - now))}" if expires_at else "permanently"
        await ctx.send(f"🛡️ Restricted **{t_key.upper()}** on {target_name} {dur_text}.\nReason: *{reason_str}*")

    async def _remove_restriction(
        self,
        ctx: commands.Context,
        r_type: str,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
    ) -> None:
        """Helper removing restriction from User, Channel, or Role."""
        t_key = r_type.lower()
        guild = ctx.guild
        gid = guild.id

        if isinstance(target, discord.Member):
            scope = "users"
            target_name = f"User **{target.display_name}**"
        elif isinstance(target, discord.TextChannel):
            scope = "channels"
            target_name = f"Channel {target.mention}"
        elif isinstance(target, discord.Role):
            scope = "roles"
            target_name = f"Role **{target.name}**"
        else:
            await ctx.send("❌ Unsupported target.")
            return

        async with self.config.guild(guild).get_attr(scope)() as conf_scope:
            tid_str = str(target.id)
            if tid_str in conf_scope:
                if t_key == "all":
                    del conf_scope[tid_str]
                elif t_key in conf_scope[tid_str]:
                    del conf_scope[tid_str][t_key]
                    if not conf_scope[tid_str]:
                        del conf_scope[tid_str]
                else:
                    await ctx.send(f"ℹ️ {target_name} does not have an active **{t_key.upper()}** restriction.")
                    return
            else:
                await ctx.send(f"ℹ️ {target_name} has no active restrictions.")
                return

        # Update in-memory cache
        if gid in self._cache and scope in self._cache[gid] and target.id in self._cache[gid][scope]:
            if t_key == "all":
                del self._cache[gid][scope][target.id]
            elif t_key in self._cache[gid][scope][target.id]:
                del self._cache[gid][scope][target.id][t_key]
                if not self._cache[gid][scope][target.id]:
                    del self._cache[gid][scope][target.id]

        label = "ALL" if t_key == "all" else t_key.upper()
        await ctx.send(f"✅ Removed **{label}** restriction from {target_name}.")

    # -------------------------------------------------------------------------
    # Restrict Command Group
    # -------------------------------------------------------------------------

    @commands.group(name="restrict", aliases=["restriction"], invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_messages=True)
    async def restrict_group(
        self,
        ctx: commands.Context,
        r_type: Optional[str] = None,
        target: Optional[Union[discord.Member, discord.TextChannel, discord.Role]] = None,
        *,
        duration_and_reason: Optional[str] = None,
    ) -> None:
        """
        Restrict users, channels, or roles from sending GIFs, media, links, or stickers.
        
        Examples:
          [p]restrict gif @user 2h Posting spam
          [p]restrict gif @user (permanent silent restriction)
          [p]restrict gif #general
          [p]restrict image @user 1d
          [p]restrict list
        """
        if r_type and target:
            await self._apply_restriction(ctx, r_type, target, duration_and_reason)
            return
        await ctx.send_help(ctx.command)

    @restrict_group.command(name="gif")
    async def restrict_gif(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
        *,
        duration_and_reason: Optional[str] = None,
    ) -> None:
        """
        Restrict a user, channel, or role from sending GIFs.
        Matches Tenor, Giphy, direct .gif/.gifv URLs, attachments, and embeds.
        
        Example: [p]restrict gif @spammer 2h
        """
        await self._apply_restriction(ctx, "gif", target, duration_and_reason)

    @restrict_group.command(name="image")
    async def restrict_image(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
        *,
        duration_and_reason: Optional[str] = None,
    ) -> None:
        """Restrict a user, channel, or role from sending non-GIF images (PNG, JPG, WEBP)."""
        await self._apply_restriction(ctx, "image", target, duration_and_reason)

    @restrict_group.command(name="video")
    async def restrict_video(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
        *,
        duration_and_reason: Optional[str] = None,
    ) -> None:
        """Restrict a user, channel, or role from sending video files or video links."""
        await self._apply_restriction(ctx, "video", target, duration_and_reason)

    @restrict_group.command(name="file", aliases=["attachment"])
    async def restrict_file(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
        *,
        duration_and_reason: Optional[str] = None,
    ) -> None:
        """Restrict a user, channel, or role from sending any file attachments."""
        await self._apply_restriction(ctx, "file", target, duration_and_reason)

    @restrict_group.command(name="link", aliases=["url"])
    async def restrict_link(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
        *,
        duration_and_reason: Optional[str] = None,
    ) -> None:
        """Restrict a user, channel, or role from posting external web links."""
        await self._apply_restriction(ctx, "link", target, duration_and_reason)

    @restrict_group.command(name="invite")
    async def restrict_invite(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
        *,
        duration_and_reason: Optional[str] = None,
    ) -> None:
        """Restrict a user, channel, or role from posting Discord invite links."""
        await self._apply_restriction(ctx, "invite", target, duration_and_reason)

    @restrict_group.command(name="sticker")
    async def restrict_sticker(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
        *,
        duration_and_reason: Optional[str] = None,
    ) -> None:
        """Restrict a user, channel, or role from sending Discord stickers."""
        await self._apply_restriction(ctx, "sticker", target, duration_and_reason)

    @restrict_group.command(name="reaction")
    async def restrict_reaction(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
        *,
        duration_and_reason: Optional[str] = None,
    ) -> None:
        """Restrict a user, channel, or role from adding emoji reactions."""
        await self._apply_restriction(ctx, "reaction", target, duration_and_reason)

    @restrict_group.command(name="all")
    async def restrict_all(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
        *,
        duration_and_reason: Optional[str] = None,
    ) -> None:
        """Restrict a user, channel, or role from sending all media, links, invites, files, and stickers."""
        await self._apply_restriction(ctx, "all", target, duration_and_reason)

    # -------------------------------------------------------------------------
    # Unrestrict Command Group
    # -------------------------------------------------------------------------

    @commands.group(name="unrestrict", aliases=["unrestriction"], invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_or_permissions(manage_messages=True)
    async def unrestrict_group(
        self,
        ctx: commands.Context,
        r_type: Optional[str] = None,
        target: Optional[Union[discord.Member, discord.TextChannel, discord.Role]] = None,
    ) -> None:
        """
        Remove restrictions from a user, channel, or role.
        
        Examples:
          [p]unrestrict gif @user
          [p]unrestrict all @user
          [p]unrestrict gif #general
        """
        if r_type and target:
            await self._remove_restriction(ctx, r_type, target)
            return
        await ctx.send_help(ctx.command)

    @unrestrict_group.command(name="gif")
    async def unrestrict_gif(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
    ) -> None:
        """Remove GIF restriction from a user, channel, or role."""
        await self._remove_restriction(ctx, "gif", target)

    @unrestrict_group.command(name="image")
    async def unrestrict_image(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
    ) -> None:
        """Remove image restriction from a user, channel, or role."""
        await self._remove_restriction(ctx, "image", target)

    @unrestrict_group.command(name="video")
    async def unrestrict_video(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
    ) -> None:
        """Remove video restriction from a user, channel, or role."""
        await self._remove_restriction(ctx, "video", target)

    @unrestrict_group.command(name="file", aliases=["attachment"])
    async def unrestrict_file(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
    ) -> None:
        """Remove file attachment restriction from a user, channel, or role."""
        await self._remove_restriction(ctx, "file", target)

    @unrestrict_group.command(name="link", aliases=["url"])
    async def unrestrict_link(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
    ) -> None:
        """Remove link restriction from a user, channel, or role."""
        await self._remove_restriction(ctx, "link", target)

    @unrestrict_group.command(name="invite")
    async def unrestrict_invite(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
    ) -> None:
        """Remove invite restriction from a user, channel, or role."""
        await self._remove_restriction(ctx, "invite", target)

    @unrestrict_group.command(name="sticker")
    async def unrestrict_sticker(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
    ) -> None:
        """Remove sticker restriction from a user, channel, or role."""
        await self._remove_restriction(ctx, "sticker", target)

    @unrestrict_group.command(name="reaction")
    async def unrestrict_reaction(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
    ) -> None:
        """Remove reaction restriction from a user, channel, or role."""
        await self._remove_restriction(ctx, "reaction", target)

    @unrestrict_group.command(name="all")
    async def unrestrict_all_cmd(
        self,
        ctx: commands.Context,
        target: Union[discord.Member, discord.TextChannel, discord.Role],
    ) -> None:
        """Remove all active restrictions from a user, channel, or role."""
        await self._remove_restriction(ctx, "all", target)

    # -------------------------------------------------------------------------
    # List & Check Inspection Commands
    # -------------------------------------------------------------------------

    @restrict_group.command(name="list")
    async def restrict_list(self, ctx: commands.Context, scope: Optional[str] = "all") -> None:
        """Lists active restrictions across users, channels, and roles in this server."""
        guild = ctx.guild
        g_cache = self._cache.get(guild.id, {})
        now = time.time()

        entries: List[str] = []
        scope_filter = scope.lower() if scope else "all"

        if scope_filter in ("all", "user", "users"):
            for uid, types_dict in g_cache.get("users", {}).items():
                m = guild.get_member(uid)
                name_str = f"User: {m.display_name} (`{uid}`)" if m else f"User ID: `{uid}`"
                for t_name, info in types_dict.items():
                    exp = info.get("expires")
                    dur_str = f"Expires in {humanize_timedelta(seconds=int(exp - now))}" if exp else "Permanent"
                    entries.append(f"• {name_str} | **{t_name.upper()}** | {dur_str} | Reason: *{info.get('reason', 'None')}*")

        if scope_filter in ("all", "channel", "channels"):
            for cid, types_dict in g_cache.get("channels", {}).items():
                c = guild.get_channel(cid)
                c_str = f"Channel: #{c.name} (`{cid}`)" if c else f"Channel ID: `{cid}`"
                for t_name, info in types_dict.items():
                    exp = info.get("expires")
                    dur_str = f"Expires in {humanize_timedelta(seconds=int(exp - now))}" if exp else "Permanent"
                    entries.append(f"• {c_str} | **{t_name.upper()}** | {dur_str} | Reason: *{info.get('reason', 'None')}*")

        if scope_filter in ("all", "role", "roles"):
            for rid, types_dict in g_cache.get("roles", {}).items():
                r = guild.get_role(rid)
                r_str = f"Role: @{r.name} (`{rid}`)" if r else f"Role ID: `{rid}`"
                for t_name, info in types_dict.items():
                    exp = info.get("expires")
                    dur_str = f"Expires in {humanize_timedelta(seconds=int(exp - now))}" if exp else "Permanent"
                    entries.append(f"• {r_str} | **{t_name.upper()}** | {dur_str} | Reason: *{info.get('reason', 'None')}*")

        if not entries:
            await ctx.send("ℹ️ No active restrictions found for this server.")
            return

        text = "\n".join(entries)
        for page in pagify(text, page_length=1900):
            await ctx.send(box(page, lang="yaml"))

    @restrict_group.command(name="check")
    async def restrict_check(self, ctx: commands.Context, member: discord.Member) -> None:
        """Checks all active restrictions impacting a specific server member."""
        guild = ctx.guild
        g_cache = self._cache.get(guild.id, {})
        now = time.time()

        user_res = g_cache.get("users", {}).get(member.id, {})
        role_res = {}
        for role in getattr(member, "roles", []):
            if role.id in g_cache.get("roles", {}):
                role_res[role.name] = g_cache["roles"][role.id]

        embed = discord.Embed(
            title=f"🛡️ Restriction Status: {member.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        if user_res:
            lines = []
            for t_name, info in user_res.items():
                exp = info.get("expires")
                dur = f"Expires in {humanize_timedelta(seconds=int(exp - now))}" if exp else "Permanent"
                lines.append(f"• **{t_name.upper()}**: {dur} (Reason: *{info.get('reason', 'None')}*)")
            embed.add_field(name="User-Direct Restrictions", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="User-Direct Restrictions", value="None", inline=False)

        if role_res:
            lines = []
            for r_name, types_dict in role_res.items():
                for t_name, info in types_dict.items():
                    lines.append(f"• Role **{r_name}** $\\rightarrow$ **{t_name.upper()}**")
            embed.add_field(name="Role-Inherited Restrictions", value="\n".join(lines), inline=False)

        await ctx.send(embed=embed)

    # -------------------------------------------------------------------------
    # Configuration Subcommands
    # -------------------------------------------------------------------------

    @restrict_group.group(name="set")
    @checks.admin_or_permissions(administrator=True)
    async def set_group(self, ctx: commands.Context) -> None:
        """Configure Restrict settings (notification mode, mod-log, escalation)."""
        pass

    @set_group.command(name="notify")
    async def set_notify(self, ctx: commands.Context, mode: str) -> None:
        """
        Set notification feedback mode when a restricted message is deleted.
        Options:
          • `silent` (Default): Message deleted immediately with zero notices or chat clutter.
          • `channel`: Sends an auto-deleting channel warning (deleted after 5 seconds).
          • `dm`: Sends a direct message to the user explaining why their message was deleted.
        """
        clean_mode = mode.lower()
        if clean_mode not in ("silent", "channel", "dm", "none"):
            await ctx.send("❌ Invalid mode. Choose between `silent`, `channel`, or `dm`.")
            return

        if clean_mode == "none":
            clean_mode = "silent"

        await self.config.guild(ctx.guild).notify_mode.set(clean_mode)
        await ctx.send(f"✅ Notification mode set to **{clean_mode.upper()}**.")

    @set_group.command(name="modlog")
    async def set_modlog(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        """Set or disable the audit mod-log channel for deleted restricted messages."""
        if channel:
            await self.config.guild(ctx.guild).modlog_channel.set(channel.id)
            await ctx.send(f"✅ Mod-log channel set to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).modlog_channel.set(None)
            await ctx.send("✅ Mod-log channel disabled.")

    @set_group.command(name="autodelete")
    async def set_autodelete(self, ctx: commands.Context, seconds: int) -> None:
        """Set how many seconds before channel warning messages delete themselves (default: 5)."""
        if seconds < 1 or seconds > 60:
            await ctx.send("❌ Duration must be between 1 and 60 seconds.")
            return

        await self.config.guild(ctx.guild).autodelete_delay.set(seconds)
        await ctx.send(f"✅ Auto-delete delay set to `{seconds}` seconds.")

    @set_group.command(name="escalate")
    async def set_escalate(
        self,
        ctx: commands.Context,
        enabled: bool,
        attempts: int = 3,
        window_minutes: int = 5,
        timeout_minutes: int = 5,
    ) -> None:
        """
        Configure repeat spam escalation (auto-timeout).
        Example: [p]restrict set escalate true 3 5 10
        (Times out the user for 10 minutes if they attempt 3 restricted posts within 5 minutes)
        """
        await self.config.guild(ctx.guild).escalate_enabled.set(enabled)
        await self.config.guild(ctx.guild).escalate_attempts.set(attempts)
        await self.config.guild(ctx.guild).escalate_window.set(window_minutes * 60)
        await self.config.guild(ctx.guild).escalate_timeout.set(timeout_minutes * 60)

        status_str = "ENABLED" if enabled else "DISABLED"
        await ctx.send(
            f"✅ Spam escalation is now **{status_str}**.\n"
            f"Rule: `{attempts}` violations in `{window_minutes}m` $\\rightarrow$ `{timeout_minutes}m` timeout."
        )

    @set_group.command(name="show", aliases=["settings"])
    async def set_show(self, ctx: commands.Context) -> None:
        """Displays current Restrict settings for this server."""
        g_conf = await self.config.guild(ctx.guild).all()
        log_ch = ctx.guild.get_channel(g_conf.get("modlog_channel") or 0)
        log_str = log_ch.mention if log_ch else "Disabled"

        embed = discord.Embed(
            title=f"⚙️ Restrict Configuration: {ctx.guild.name}",
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Notification Mode", value=f"**{g_conf.get('notify_mode', 'silent').upper()}** (Default: silent)", inline=True)
        embed.add_field(name="Channel Warn Delay", value=f"`{g_conf.get('autodelete_delay', 5)}s`", inline=True)
        embed.add_field(name="Audit Mod-Log", value=log_str, inline=True)

        esc_status = "Enabled" if g_conf.get("escalate_enabled") else "Disabled"
        esc_detail = (
            f"Status: **{esc_status}**\n"
            f"Threshold: `{g_conf.get('escalate_attempts', 3)}` violations in `{g_conf.get('escalate_window', 300) // 60}m`\n"
            f"Action: `{g_conf.get('escalate_timeout', 300) // 60}m` Discord Timeout"
        )
        embed.add_field(name="Spam Escalation", value=esc_detail, inline=False)

        await ctx.send(embed=embed)
