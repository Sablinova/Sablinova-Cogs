import asyncio
from datetime import datetime, timezone
import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
import discord
from discord.ext import tasks
import sqlite3
from redbot.core import Config, commands
from redbot.core.bot import Red

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError:
    Image = ImageDraw = ImageFont = ImageFilter = None

log = logging.getLogger("red.sabbyinvitecog")

IDENTIFIER = 847291048201
DISCORD_INVITE_CAP = 1000


class InvitedPaginationView(discord.ui.View):
    """
    Pagination view for navigating through large invite record lists.
    Features First, Previous, Page Indicator, Next, and Last buttons.
    """

    def __init__(
        self,
        cog: "SabbyInviteCog",
        guild: discord.Guild,
        target: Any,
        author_id: int,
        prefix: str = "[p]",
        include_privacy_hint: bool = False,
        page: int = 1,
        total_pages: int = 1,
        per_page: int = 10,
        timeout: float = 180.0,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.guild = guild
        self.target = target
        self.author_id = author_id
        self.prefix = prefix
        self.include_privacy_hint = include_privacy_hint
        self.page = page
        self.total_pages = total_pages
        self.per_page = per_page
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self):
        self.first_button.disabled = (self.page <= 1)
        self.prev_button.disabled = (self.page <= 1)
        self.page_indicator.label = f"Page {self.page} / {self.total_pages}"
        self.page_indicator.disabled = True
        self.next_button.disabled = (self.page >= self.total_pages)
        self.last_button.disabled = (self.page >= self.total_pages)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        is_owner = await self.cog.bot.is_owner(interaction.user)
        if is_owner:
            return True
        if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild:
            return True

        await interaction.response.send_message(
            "Only the person who requested this invite list (or server staff) can use these buttons.",
            ephemeral=True
        )
        return False

    async def _goto_page(self, interaction: discord.Interaction, new_page: int):
        self.page = max(1, min(new_page, self.total_pages))
        self._update_buttons()
        embed, _ = await self.cog._build_invite_info_embed(
            self.guild,
            self.target,
            prefix=self.prefix,
            include_privacy_hint=self.include_privacy_hint,
            page=self.page,
            per_page=self.per_page,
        )
        if not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto_page(interaction, 1)

    @discord.ui.button(emoji="◀", label="Prev", style=discord.ButtonStyle.primary, row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto_page(interaction, self.page - 1)

    @discord.ui.button(label="Page 1 / 1", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(emoji="▶", label="Next", style=discord.ButtonStyle.primary, row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto_page(interaction, self.page + 1)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary, row=0)
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto_page(interaction, self.total_pages)

    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass


class LeaderboardLiveView(discord.ui.View):
    """
    Persistent Discord UI view with Get Link, My Stats, and Refresh buttons.
    Operates without timeout and responds cleanly to user interactions.
    """

    def __init__(self, cog: "SabbyInviteCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Get My Invite Link",
        style=discord.ButtonStyle.primary,
        emoji="🔗",
        custom_id="sabbyinvite:getlink"
    )
    async def get_link_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        if not guild or not isinstance(member, discord.Member):
            await interaction.response.send_message("This button can only be used inside a server.", ephemeral=True)
            return

        allowed, remaining = self.cog._check_cooldown(self.cog.getlink_cooldowns, member.id, 30.0)
        if not allowed:
            await interaction.response.send_message(
                f"⏳ Cooldown active. You can request your invite link again in **{remaining}s**.",
                ephemeral=True
            )
            return

        code, err = await self.cog._get_or_create_contest_link(guild, member)
        if err:
            await interaction.response.send_message(f"⚠️ {err}", ephemeral=True)
            return

        data = await self.cog.config.member(member).all()
        real = data.get("real", 0)
        left = data.get("left", 0)
        fake = data.get("fake", 0)
        bonus = data.get("bonus", 0)
        net = real - left - fake + bonus

        all_members = await self.cog.config.all_members(guild)
        scores = []
        for mid, d in all_members.items():
            if not d.get("disqualified", False):
                n = d.get("real", 0) - d.get("left", 0) - d.get("fake", 0) + d.get("bonus", 0)
                scores.append((mid, n))
        scores.sort(key=lambda x: x[1], reverse=True)

        rank_str = "Unranked"
        for idx, (mid, sc) in enumerate(scores, start=1):
            if mid == member.id:
                rank_str = f"#{idx} of {len(scores)}"
                break

        pts_label = "point" if abs(net) == 1 else "points"
        embed = discord.Embed(
            title="Your Dedicated Invite Link",
            description=f"Share this link with others to climb the leaderboard:\n**https://discord.gg/{code}**",
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Your Score", value=f"**{net} {pts_label}**", inline=True)
        embed.add_field(name="Your Rank", value=f"**{rank_str}**", inline=True)

        parts = [f"✅ Real: **{real}**", f"🚪 Left: **{left}**", f"⚠️ Fake: **{fake}**"]
        if bonus != 0:
            parts.append(f"⭐ Bonus: **{bonus:+d}**")
        pending_count = sum(
            1 for mid, d in all_members.items()
            if d.get("invited_by") == member.id and (d.get("pending_onboarding", False) or d.get("pending_cross_verify", False))
        )
        if pending_count > 0:
            parts.append(f"⏳ Pending: **{pending_count}**")
        rejoin_count = sum(
            1 for mid, d in all_members.items()
            if d.get("invited_by") == member.id and d.get("is_rejoin", False)
        )
        if rejoin_count > 0:
            parts.append(f"🔄 Re-join: **{rejoin_count}**")
        embed.add_field(name="Score Breakdown", value=" • ".join(parts), inline=False)
        cv_enabled, cv_server, _ = await self.cog._get_cross_verify_names(guild)
        if cv_enabled:
            embed.add_field(
                name="⚠️ Verification Requirement",
                value=f"For invites to count as **Real (+1)**, they must join **{cv_server}** and get verified! Until verified, they remain held in **Pending (0)**.",
                inline=False
            )
        embed.set_footer(text=f"Dedicated permanent link for {member.display_name}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="My Stats",
        style=discord.ButtonStyle.success,
        emoji="📊",
        custom_id="sabbyinvite:mystats"
    )
    async def my_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        if not guild or not isinstance(member, discord.Member):
            await interaction.response.send_message("This button can only be used inside a server.", ephemeral=True)
            return

        allowed, remaining = self.cog._check_cooldown(self.cog.mystats_cooldowns, member.id, 15.0)
        if not allowed:
            await interaction.response.send_message(
                f"⏳ Cooldown active. You can check your stats again in **{remaining}s**.",
                ephemeral=True
            )
            return

        embed, total_pages = await self.cog._build_invite_info_embed(
            guild, member, include_privacy_hint=True, page=1, per_page=10
        )
        if total_pages > 1:
            view = InvitedPaginationView(
                cog=self.cog,
                guild=guild,
                target=member,
                author_id=member.id,
                prefix="[p]",
                include_privacy_hint=True,
                page=1,
                total_pages=total_pages,
                per_page=10,
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Refresh",
        style=discord.ButtonStyle.secondary,
        emoji="🔄",
        custom_id="sabbyinvite:refresh"
    )
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user
        if not guild or not isinstance(member, discord.Member):
            await interaction.response.send_message("This button can only be used inside a server.", ephemeral=True)
            return

        allowed, remaining = self.cog._check_cooldown(self.cog.refresh_cooldowns, member.id, 60.0)
        if not allowed:
            rank_embed = await self.cog._build_user_rank_embed(guild, member)
            await interaction.response.send_message(
                content=f"⏳ Cooldown active. You can refresh the public board again in **{remaining}s**.",
                embed=rank_embed,
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        guild_allowed, _ = self.cog._check_cooldown(self.cog.guild_refresh_cooldowns, guild.id, 20.0)
        if guild_allowed:
            try:
                await self.cog._update_liveboard_message(guild)
            except Exception as exc:
                log.debug(f"Manual liveboard refresh error: {exc}")

        rank_embed = await self.cog._build_user_rank_embed(guild, member)
        await interaction.followup.send(
            content="🔄 **Leaderboard updated with latest scores!**",
            embed=rank_embed,
            ephemeral=True
        )


class SabbyInviteCog(commands.Cog):
    """
    High-precision invite tracking and contest leaderboard cog.

    Features:
    - 1,000 Discord invite cap pruner with rate-limited safe cleanup.
    - 4-metric anti-cheat scoring: Real - Left - Fake + Bonus.
    - Account age anti-alt detection (configurable min age).
    - Re-join duplicate fraud prevention.
    - Dedicated permanent contest link per participant.
    - Persistent live leaderboard auto-updating every 60 seconds with buttons.
    - Real-time contest leaderboard with cash prize tiers ($100 / $50 / $30).
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=IDENTIFIER, force_registration=True)

        default_guild = {
            "log_channel_id": None,
            "min_age_days": 7,
            "require_onboarding": True,
            "event_channel_id": None,
            "prizes": ["$100", "$50", "$30"],
            "contest_active": True,
            "contest_links": {},
            "liveboard_enabled": False,
            "live_channel_id": None,
            "live_message_id": None,
            "webhook_url": None,
            "webhook_name": "Invite Leaderboard",
            "webhook_avatar": None,
            "cross_verify_enabled": False,
            "cross_verify_guild_id": None,
            "cross_verify_role_id": None,
        }

        default_member = {
            "real": 0,
            "left": 0,
            "fake": 0,
            "bonus": 0,
            "contest_code": None,
            "disqualified": False,
            "invited_by": None,
            "join_code": None,
            "joined_at": None,
            "is_fake": False,
            "is_rejoin": False,
            "pending_onboarding": False,
            "pending_cross_verify": False,
            "cross_verified": False,
        }

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

        self.invite_cache: Dict[int, Dict[str, int]] = {}
        self.vanity_cache: Dict[int, int] = {}
        self._sync_task: Optional[asyncio.Task] = None
        self.refresh_cooldowns: Dict[int, float] = {}
        self.mystats_cooldowns: Dict[int, float] = {}
        self.getlink_cooldowns: Dict[int, float] = {}
        self.guild_refresh_cooldowns: Dict[int, float] = {}
        self._liveboard_cache: Dict[int, Dict[str, Any]] = {}

    def _check_cooldown(self, bucket: Dict[int, float], key: int, cooldown_seconds: float) -> Tuple[bool, int]:
        """Thread-safe cooldown checker with automatic TTL memory cleanup."""
        now = datetime.now(timezone.utc).timestamp()
        last = bucket.get(key, 0.0)
        elapsed = now - last
        if elapsed < cooldown_seconds:
            return False, int(cooldown_seconds - elapsed) + 1
        bucket[key] = now
        if len(bucket) > 5000:
            cutoff = now - 300.0
            expired = [k for k, v in bucket.items() if v < cutoff]
            for k in expired:
                bucket.pop(k, None)
        return True, 0

    def _is_member_awaiting_onboarding(self, member: discord.Member) -> bool:
        """Check if a member is still waiting to pass rule screening or server onboarding."""
        if getattr(member, "pending", False):
            return True
        if "GUILD_ONBOARDING" in member.guild.features:
            if hasattr(member, "flags") and hasattr(member.flags, "completed_onboarding"):
                return not member.flags.completed_onboarding
        return False

    def _get_historical_join_time(self, user_id: int) -> Optional[float]:
        """Check if user previously joined the server in historical in-out SQLite DB."""
        db_path = Path("/home/sablinova/.local/share/Red-DiscordBot/data/Sablinova/cogs/SabbyInviteCog/in_out_history.db")
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(db_path), timeout=3.0)
            cur = conn.cursor()
            cur.execute("SELECT first_joined_at, last_joined_at FROM members WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            conn.close()
            if row:
                return row[0] or row[1]
            return None
        except Exception as exc:
            log.debug(f"Historical DB lookup error: {exc}")
            return None

    def _get_historical_member_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Fetch historical join/leave record from in_out_history.db."""
        db_path = Path("/home/sablinova/.local/share/Red-DiscordBot/data/Sablinova/cogs/SabbyInviteCog/in_out_history.db")
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            cur = conn.cursor()
            cur.execute("SELECT first_joined_at, last_joined_at, last_left_at, total_joins, total_leaves FROM members WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            conn.close()
            if row:
                return {
                    "first_joined_at": row[0],
                    "last_joined_at": row[1],
                    "last_left_at": row[2],
                    "total_joins": row[3],
                    "total_leaves": row[4]
                }
            return None
        except Exception as exc:
            log.debug(f"Historical DB member info lookup error: {exc}")
            return None

    async def _is_cross_server_verified(self, member_id: int, cross_guild_id: Optional[int], cross_role_id: Optional[int]) -> bool:
        """Check if a member is in the required partner server and has the required role."""
        if not cross_guild_id:
            return True
        target_guild = self.bot.get_guild(cross_guild_id)
        if not target_guild:
            return False
        target_member = target_guild.get_member(member_id)
        if not target_member:
            try:
                target_member = await target_guild.fetch_member(member_id)
            except (discord.NotFound, discord.HTTPException):
                return False
        if not target_member:
            return False
        if cross_role_id:
            return any(r.id == cross_role_id for r in target_member.roles)
        return True

    async def _get_cross_verify_names(self, guild: discord.Guild) -> Tuple[bool, str, str]:
        """Return (is_enabled, server_name, role_name) for partner server verification."""
        enabled = await self.config.guild(guild).cross_verify_enabled()
        if not enabled:
            return False, "", ""
        server_id = await self.config.guild(guild).cross_verify_guild_id()
        role_id = await self.config.guild(guild).cross_verify_role_id()
        t_guild = self.bot.get_guild(server_id) if server_id else None
        s_name = t_guild.name if t_guild else "The Free Pub"
        t_role = t_guild.get_role(role_id) if (t_guild and role_id) else None
        r_name = t_role.name if t_role else "verified"
        return True, s_name, r_name

    async def _log_cross_verify_event(
        self,
        contest_guild: discord.Guild,
        member: discord.Member,
        inviter_id: int,
        status_text: str,
        is_positive: bool
    ):
        """Send a notification embed to the contest guild log channel for cross-server events."""
        log_channel_id = await self.config.guild(contest_guild).log_channel_id()
        if not log_channel_id:
            return
        log_chan = contest_guild.get_channel(log_channel_id)
        if not log_chan or not log_chan.permissions_for(contest_guild.me).send_messages:
            return

        inviter_user = contest_guild.get_member(inviter_id) or await self.bot.get_or_fetch_user(inviter_id)
        inv_data = await self.config.member_from_ids(contest_guild.id, inviter_id).all()
        net_score = inv_data["real"] - inv_data["left"] - inv_data["fake"] + inv_data["bonus"]

        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="Partner Server Verification Event",
            color=discord.Color.green() if is_positive else discord.Color.dark_red(),
            timestamp=now
        )
        embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)
        if inviter_user:
            embed.add_field(
                name="Invited By",
                value=f"{inviter_user.mention} (`{inviter_user.id}`)\nScore: **{net_score}** (Real: {inv_data['real']} | Left: {inv_data['left']} | Fake: {inv_data['fake']} | Bonus: {inv_data['bonus']})",
                inline=False
            )
        embed.add_field(name="Outcome", value=status_text, inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await log_chan.send(embed=embed)
        except discord.HTTPException:
            pass

    async def cog_load(self):
        self._sync_task = asyncio.create_task(self._init_cache())
        try:
            self.bot.register_rpc_handler(self._rpc_get_status)
            self.bot.register_rpc_handler(self._rpc_audit_guild)
            self.bot.register_rpc_handler(self._rpc_refresh_liveboard)
            self.bot.register_rpc_handler(self._rpc_remove_liveboard)
            self.bot.register_rpc_handler(self._rpc_check_member)
            self.bot.register_rpc_handler(self._rpc_cross_verify_sync)
            self.bot.register_rpc_handler(self._rpc_set_member_invite)
        except Exception as exc:
            log.debug(f"Could not register RPC handler: {exc}")

        self.bot.add_view(LeaderboardLiveView(self))

        if not self.live_updater.is_running():
            self.live_updater.start()

    def cog_unload(self):
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
        if self.live_updater.is_running():
            self.live_updater.cancel()
        try:
            self.bot.unregister_rpc_handler(self._rpc_get_status)
            self.bot.unregister_rpc_handler(self._rpc_audit_guild)
            self.bot.unregister_rpc_handler(self._rpc_refresh_liveboard)
            self.bot.unregister_rpc_handler(self._rpc_remove_liveboard)
            self.bot.unregister_rpc_handler(self._rpc_check_member)
            self.bot.unregister_rpc_handler(self._rpc_cross_verify_sync)
            self.bot.unregister_rpc_handler(self._rpc_set_member_invite)
        except Exception:
            pass

    @tasks.loop(seconds=60)
    async def live_updater(self):
        for guild in self.bot.guilds:
            try:
                enabled = await self.config.guild(guild).liveboard_enabled()
                channel_id = await self.config.guild(guild).live_channel_id()
                if not enabled or not channel_id:
                    continue
                await self._update_liveboard_message(guild)
            except Exception as exc:
                log.debug(f"Live updater error for guild {guild.id}: {exc}")

    @live_updater.before_loop
    async def before_live_updater(self):
        await self.bot.wait_until_ready()

    async def _get_or_create_contest_link(
        self,
        guild: discord.Guild,
        member: discord.Member
    ) -> Tuple[Optional[str], Optional[str]]:
        """Retrieve existing dedicated contest link or generate a new one."""
        existing_code = await self.config.member(member).contest_code()
        if existing_code:
            try:
                invites = await guild.invites()
                if any(inv.code == existing_code for inv in invites):
                    return (existing_code, None)
            except Exception:
                pass

        if not guild.me.guild_permissions.manage_guild:
            return (None, "Bot is missing Manage Server permissions to create invite links.")

        try:
            invites = await guild.invites()
        except Exception as exc:
            return (None, f"Failed to fetch guild invites: {exc}")

        if len(invites) >= 995:
            return (None, f"The server is at max invite capacity ({len(invites)}/{DISCORD_INVITE_CAP}). An admin must run inviteprune first.")

        event_channel_id = await self.config.guild(guild).event_channel_id()
        target_channel = None
        if event_channel_id:
            target_channel = guild.get_channel(event_channel_id)
        if not target_channel:
            target_channel = guild.rules_channel or guild.system_channel

        if not target_channel or not target_channel.permissions_for(guild.me).create_instant_invite:
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).create_instant_invite:
                    target_channel = ch
                    break

        if not target_channel:
            return (None, "Could not find a suitable text channel with Create Invite permission.")

        try:
            new_invite = await target_channel.create_invite(
                max_age=0,
                max_uses=0,
                unique=True,
                reason=f"SabbyInviteCog: Dedicated contest link for {member.name} ({member.id})"
            )
        except Exception as exc:
            return (None, f"Failed to generate contest link: {exc}")

        await self.config.member(member).contest_code.set(new_invite.code)
        async with self.config.guild(guild).contest_links() as cl:
            cl[new_invite.code] = member.id

        if guild.id not in self.invite_cache:
            self.invite_cache[guild.id] = {}
        self.invite_cache[guild.id][new_invite.code] = 0

        return (new_invite.code, None)

    async def _get_ranked_scores(self, guild: discord.Guild) -> List[Dict[str, Any]]:
        """Retrieve and rank all non-disqualified guild participants by net score."""
        all_members = await self.config.all_members(guild)
        scores = []
        for member_id, data in all_members.items():
            if data.get("disqualified", False):
                continue
            real = data.get("real", 0)
            left = data.get("left", 0)
            fake = data.get("fake", 0)
            bonus = data.get("bonus", 0)
            code = data.get("contest_code")
            net = real - left - fake + bonus

            if net != 0 or real != 0 or bonus != 0 or code is not None:
                scores.append({
                    "id": member_id,
                    "net": net,
                    "real": real,
                    "left": left,
                    "fake": fake,
                    "bonus": bonus,
                    "code": code,
                })

        scores.sort(key=lambda x: (x["net"], x["real"], -x["left"], -x["fake"]), reverse=True)
        return scores

    def _render_podium_sync(self, contestants: List[Dict[str, Any]]) -> bytes:
        cutout_path = Path(__file__).parent / "assets" / "podium_cutout.png"
        font_bold_path = None
        for p in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        ]:
            if Path(p).exists():
                font_bold_path = p
                break

        if cutout_path.exists():
            cutout = Image.open(str(cutout_path)).convert("RGBA")
            W, H = cutout.size
            canvas = Image.new("RGBA", (W, H), (14, 15, 19, 255))

            if font_bold_path:
                f_name = ImageFont.truetype(font_bold_path, 17)
                f_pts = ImageFont.truetype(font_bold_path, 14)
                f_initial = ImageFont.truetype(font_bold_path, 72)
            else:
                f_name = f_pts = f_initial = ImageFont.load_default()

            slots = [
                {"idx": 1, "cx": 400, "cy": 175, "size": 160, "rect_x": 398, "y_name": 258, "y_pts": 278, "pts_col": (255, 215, 0, 255)},
                {"idx": 2, "cx": 638, "cy": 185, "size": 150, "rect_x": 638, "y_name": 264, "y_pts": 282, "pts_col": (255, 120, 200, 255)},
                {"idx": 3, "cx": 868, "cy": 205, "size": 140, "rect_x": 867, "y_name": 270, "y_pts": 288, "pts_col": (255, 160, 40, 255)},
            ]

            for slot in slots:
                if slot["idx"] <= len(contestants):
                    c = contestants[slot["idx"] - 1]
                    sz = slot["size"]
                    src_av = None
                    if c.get("avatar_bytes"):
                        try:
                            src_av = Image.open(io.BytesIO(c["avatar_bytes"])).convert("RGBA")
                            src_av = src_av.resize((sz, sz), Image.Resampling.LANCZOS)
                        except Exception:
                            src_av = None

                    if not src_av:
                        src_av = Image.new("RGBA", (sz, sz), (45, 52, 64, 255))
                        sd = ImageDraw.Draw(src_av)
                        let = c.get("initial", "?")
                        bb = sd.textbbox((0, 0), let, font=f_initial)
                        sd.text(((sz - (bb[2] - bb[0])) // 2, (sz - (bb[3] - bb[1])) // 2 - 4), let, fill=(255, 255, 255, 255), font=f_initial)

                    canvas.paste(src_av, (slot["cx"] - sz // 2, slot["cy"] - sz // 2))

            canvas = Image.alpha_composite(canvas, cutout)

            draw = ImageDraw.Draw(canvas)
            def draw_centered(d, text, font, fill, cx, y):
                bb = d.textbbox((0, 0), text, font=font)
                w = bb[2] - bb[0]
                d.text((cx - w // 2, y), text, fill=fill, font=font)

            for slot in slots:
                if slot["idx"] <= len(contestants):
                    c = contestants[slot["idx"] - 1]
                    pts_val = c["pts"]
                    pts_str = f"{pts_val} pt" if abs(pts_val) == 1 else f"{pts_val} pts"
                    name_str = c["name"]
                    if len(name_str) > 16:
                        name_str = name_str[:14] + "..."

                    draw_centered(draw, name_str, f_name, (255, 255, 255, 255), slot["rect_x"], slot["y_name"])
                    draw_centered(draw, pts_str, f_pts, slot["pts_col"], slot["rect_x"], slot["y_pts"])
                else:
                    draw_centered(draw, "Unclaimed", f_name, (120, 125, 135, 255), slot["rect_x"], slot["y_name"] + 6)

            buf = io.BytesIO()
            canvas.save(buf, format="PNG", optimize=True)
            return buf.getvalue()

        # Fallback procedural renderer if cutout is missing
        W, H = 900, 280
        canvas = Image.new("RGBA", (W, H), (20, 21, 26, 255))
        card_draw = ImageDraw.Draw(canvas)
        card_draw.rounded_rectangle([(0, 0), (W - 1, H - 1)], radius=16, outline=(255, 255, 255, 35), width=1)
        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    async def _generate_podium_image(
        self,
        guild: discord.Guild,
        top_scores: List[Dict[str, Any]]
    ) -> Optional[bytes]:
        """Fetch top contestant avatars asynchronously and render the podium graphic in a thread."""
        if not top_scores or Image is None:
            return None

        contestants = []
        for rank_idx, entry in enumerate(top_scores[:3], start=1):
            user = guild.get_member(entry["id"]) or self.bot.get_user(entry["id"])
            raw_name = user.display_name if user else f"User {entry['id']}"
            if len(raw_name) > 16:
                raw_name = raw_name[:14] + "..."

            avatar_bytes = None
            if user:
                try:
                    avatar_bytes = await user.display_avatar.with_format("png").with_size(128).read()
                except Exception:
                    avatar_bytes = None

            contestants.append({
                "rank": rank_idx,
                "name": raw_name,
                "pts": entry["net"],
                "avatar_bytes": avatar_bytes,
                "initial": raw_name[0].upper() if raw_name else "?",
            })

        try:
            return await asyncio.to_thread(self._render_podium_sync, contestants)
        except Exception as exc:
            log.warning(f"Podium image generation failed: {exc}")
            return None

    async def _build_leaderboard_embed(
        self,
        guild: discord.Guild,
        author_id: Optional[int] = None,
        is_live: bool = False,
        scores: Optional[List[Dict[str, Any]]] = None
    ) -> discord.Embed:
        """Construct a high-information yet compact invite leaderboard embed."""
        if scores is None:
            scores = await self._get_ranked_scores(guild)

        total_real = sum(e["real"] for e in scores)
        total_left = sum(e["left"] for e in scores)
        total_fake = sum(e["fake"] for e in scores)
        all_guild_members = await self.config.all_members(guild)
        total_pending = sum(1 for d in all_guild_members.values() if d.get("pending_onboarding", False) or d.get("pending_cross_verify", False))

        title = "Live Invite Leaderboard" if is_live else "Invite Leaderboard"
        embed = discord.Embed(
            title=title,
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )

        if not scores:
            embed.description = "*No invites recorded yet. Click **Get My Invite Link** below to start!*"
        else:
            total_joined = total_real + total_left
            retention = (total_real / total_joined * 100.0) if total_joined > 0 else 100.0

            stat_items = [
                f"**{len(scores)}** members",
                f"**{total_real}** real",
                f"**{total_left}** left",
            ]
            if total_fake > 0:
                stat_items.append(f"**{total_fake}** fake")
            if total_pending > 0:
                stat_items.append(f"**{total_pending}** pending")
            stat_items.append(f"**{retention:.0f}%** retention")

            stats_bar = "> 📊 **Overview:** " + " • ".join(stat_items)
            cv_enabled, cv_server, _ = await self._get_cross_verify_names(guild)
            rule_callout = f"> ℹ️ **Rule:** Invites count as **Real (+1)** only after joining **{cv_server}** and getting verified!" if cv_enabled else ""

            lines = [stats_bar]
            if rule_callout:
                lines.append(rule_callout)
            lines.append("")
            author_rank = None
            author_entry = None

            for idx, entry in enumerate(scores, start=1):
                if author_id and entry["id"] == author_id:
                    author_rank = idx
                    author_entry = entry

                if idx <= 10:
                    user = guild.get_member(entry["id"]) or self.bot.get_user(entry["id"])
                    raw_name = user.display_name if user else f"User {entry['id']}"
                    if len(raw_name) > 22:
                        raw_name = raw_name[:19] + "..."
                    name = discord.utils.escape_markdown(raw_name)

                    if idx == 1:
                        prefix = "🥇"
                    elif idx == 2:
                        prefix = "🥈"
                    elif idx == 3:
                        prefix = "🥉"
                    else:
                        prefix = f"**#{idx}**"

                    pts = entry["net"]
                    pts_label = "pt" if abs(pts) == 1 else "pts"

                    stat_parts = [
                        f"{entry['real']} real",
                        f"{entry['left']} left",
                        f"{entry['fake']} fake"
                    ]
                    if entry["bonus"] != 0:
                        stat_parts.append(f"{entry['bonus']:+d} bonus")

                    detail_str = " · ".join(stat_parts)
                    lines.append(f"{prefix} **{name}** • **{pts} {pts_label}**")
                    lines.append(f"-# ID: {entry['id']} • {detail_str}")

            embed.description = "\n".join(lines)

            if author_id:
                if author_rank and author_rank > 10 and author_entry:
                    a_pts = author_entry["net"]
                    a_label = "pt" if abs(a_pts) == 1 else "pts"
                    a_parts = [
                        f"{author_entry['real']} real",
                        f"{author_entry['left']} left",
                        f"{author_entry['fake']} fake"
                    ]
                    if author_entry["bonus"] != 0:
                        a_parts.append(f"{author_entry['bonus']:+d} bonus")
                    embed.add_field(
                        name="Your Rank",
                        value=f"**#{author_rank}** • **{a_pts} {a_label}**\n-# ID: {author_id} • {' · '.join(a_parts)}",
                        inline=False
                    )
                elif not author_rank:
                    embed.add_field(
                        name="Your Rank",
                        value="Unranked • Click **Get My Invite Link** below to join!",
                        inline=False
                    )

        cv_enabled, cv_server, _ = await self._get_cross_verify_names(guild)
        if cv_enabled and is_live:
            footer_text = f"Live (updates every 1m) • Must join {cv_server} & get verified to count as Real"
        elif is_live:
            footer_text = "Live (updates every 1m) • Anti-cheat active • Formula: Real - Left - Fake"
        else:
            footer_text = f"{len(scores)} participants • Anti-cheat active • Real - Left - Fake"
        embed.set_footer(text=footer_text)
        return embed

    def _compute_top3_sig(self, guild: discord.Guild, top_scores: List[Dict[str, Any]]) -> tuple:
        sig = []
        for entry in top_scores[:3]:
            uid = entry["id"]
            pts = entry["net"]
            user = guild.get_member(uid) or self.bot.get_user(uid)
            name = user.display_name if user else f"User {uid}"
            avatar_key = str(user.display_avatar.url) if user else ""
            sig.append((uid, pts, name, avatar_key))
        return tuple(sig)

    def _compute_embed_sig(self, scores: List[Dict[str, Any]], total_pending: int) -> tuple:
        top_tuple = tuple((s["id"], s["net"], s["real"], s["left"], s["fake"], s["bonus"]) for s in scores[:15])
        return (len(scores), total_pending, top_tuple)

    async def _update_liveboard_message(self, guild: discord.Guild, force_render: bool = False) -> Optional[discord.Message]:
        """Update or post the liveboard embed using webhook or standard channel message."""
        if not await self.config.guild(guild).liveboard_enabled():
            return None
        channel_id = await self.config.guild(guild).live_channel_id()
        if not channel_id:
            return None
        channel = guild.get_channel(channel_id)
        if not channel:
            return None

        message_id = await self.config.guild(guild).live_message_id()
        webhook_url = await self.config.guild(guild).webhook_url()
        name = await self.config.guild(guild).webhook_name() or "Invite Leaderboard"
        avatar = await self.config.guild(guild).webhook_avatar()

        scores = await self._get_ranked_scores(guild)
        all_guild_members = await self.config.all_members(guild)
        total_pending = sum(
            1 for d in all_guild_members.values()
            if d.get("pending_onboarding", False) or d.get("pending_cross_verify", False)
        )

        current_top3_sig = self._compute_top3_sig(guild, scores[:3])
        current_embed_sig = self._compute_embed_sig(scores, total_pending)

        cache = self._liveboard_cache.get(guild.id, {})
        last_top3_sig = cache.get("top3_sig")
        last_embed_sig = cache.get("embed_sig")
        cached_msg_id = cache.get("message_id")
        has_image = cache.get("has_image", False)

        is_new_message = (not message_id) or (cached_msg_id != message_id) or (not has_image)
        top3_changed = is_new_message or force_render or (current_top3_sig != last_top3_sig)
        embed_changed = is_new_message or force_render or (current_embed_sig != last_embed_sig)

        if not is_new_message and not top3_changed and not embed_changed and not force_render:
            return None

        embed = await self._build_leaderboard_embed(guild, is_live=True, scores=scores)
        has_podium = bool(scores)
        if has_podium:
            embed.set_image(url="attachment://podium.png")
        view = LeaderboardLiveView(self)

        file = None
        if top3_changed and has_podium:
            podium_bytes = await self._generate_podium_image(guild, scores[:3])
            if podium_bytes:
                file = discord.File(io.BytesIO(podium_bytes), filename="podium.png")

        webhook = None
        if webhook_url:
            try:
                wh_candidate = discord.Webhook.from_url(webhook_url, client=self.bot)
                wh_obj = await wh_candidate.fetch()
                if wh_obj.channel_id == channel.id:
                    webhook = wh_candidate
                else:
                    moved = False
                    try:
                        await wh_obj.edit(channel=channel, reason="SabbyInvite liveboard moved")
                        webhook = wh_obj
                        moved = True
                    except Exception:
                        pass
                    if not moved and channel.permissions_for(guild.me).manage_webhooks:
                        try:
                            avatar_bytes = None
                            if avatar:
                                try:
                                    async with aiohttp.ClientSession() as s:
                                        async with s.get(avatar) as r:
                                            if r.status == 200:
                                                avatar_bytes = await r.read()
                                except Exception:
                                    pass
                            new_wh = await channel.create_webhook(
                                name=name,
                                avatar=avatar_bytes,
                                reason="SabbyInvite liveboard webhook"
                            )
                            webhook_url = new_wh.url
                            await self.config.guild(guild).webhook_url.set(webhook_url)
                            webhook = new_wh
                        except Exception as exc:
                            log.debug(f"Webhook recreation in channel {channel.id} failed: {exc}")
                            webhook = None
            except Exception as exc:
                log.debug(f"Webhook fetch failed for guild {guild.id}: {exc}")
                webhook = None

        if webhook:
            try:
                if message_id:
                    try:
                        kwargs = {"embed": embed, "view": view}
                        if file:
                            kwargs["attachments"] = [file]
                        edited_msg = await webhook.edit_message(message_id, **kwargs)
                        self._liveboard_cache[guild.id] = {
                            "top3_sig": current_top3_sig,
                            "embed_sig": current_embed_sig,
                            "message_id": message_id,
                            "has_image": True if (file or has_image) else False,
                        }
                        return edited_msg or True
                    except (discord.NotFound, discord.HTTPException):
                        pass

                if not file and has_podium:
                    podium_bytes = await self._generate_podium_image(guild, scores[:3])
                    if podium_bytes:
                        file = discord.File(io.BytesIO(podium_bytes), filename="podium.png")

                send_kwargs = {
                    "embed": embed,
                    "view": view,
                    "username": name,
                    "avatar_url": avatar,
                    "wait": True,
                }
                if file:
                    send_kwargs["file"] = file
                msg = await webhook.send(**send_kwargs)
                await self.config.guild(guild).live_message_id.set(msg.id)
                self._liveboard_cache[guild.id] = {
                    "top3_sig": current_top3_sig,
                    "embed_sig": current_embed_sig,
                    "message_id": msg.id,
                    "has_image": (file is not None),
                }
                return msg
            except Exception as exc:
                log.warning(f"Webhook update failed for guild {guild.id}: {exc}")

        if message_id:
            try:
                msg = await channel.fetch_message(message_id)
                edit_kwargs = {"embed": embed, "view": view}
                if file:
                    edit_kwargs["attachments"] = [file]
                await msg.edit(**edit_kwargs)
                self._liveboard_cache[guild.id] = {
                    "top3_sig": current_top3_sig,
                    "embed_sig": current_embed_sig,
                    "message_id": message_id,
                    "has_image": True if (file or has_image) else False,
                }
                return msg
            except (discord.NotFound, discord.Forbidden):
                pass

        try:
            if not file and has_podium:
                podium_bytes = await self._generate_podium_image(guild, scores[:3])
                if podium_bytes:
                    file = discord.File(io.BytesIO(podium_bytes), filename="podium.png")
            send_kwargs = {"embed": embed, "view": view}
            if file:
                send_kwargs["file"] = file
            msg = await channel.send(**send_kwargs)
            await self.config.guild(guild).live_message_id.set(msg.id)
            self._liveboard_cache[guild.id] = {
                "top3_sig": current_top3_sig,
                "embed_sig": current_embed_sig,
                "message_id": msg.id,
                "has_image": (file is not None),
            }
            return msg
        except Exception as exc:
            log.warning(f"Liveboard channel update failed for guild {guild.id}: {exc}")
            return None

    async def _delete_all_liveboard_messages(self, guild: discord.Guild) -> int:
        """Thoroughly delete liveboard messages from channel and webhook."""
        deleted_count = 0
        channel_id = await self.config.guild(guild).live_channel_id()
        message_id = await self.config.guild(guild).live_message_id()
        webhook_url = await self.config.guild(guild).webhook_url()

        if webhook_url and message_id:
            try:
                webhook = discord.Webhook.from_url(webhook_url, client=self.bot)
                await webhook.delete_message(message_id)
                deleted_count += 1
            except Exception as exc:
                log.debug(f"Webhook message deletion error: {exc}")

        channels_to_scan = set()
        if channel_id:
            c = guild.get_channel(channel_id)
            if c:
                channels_to_scan.add(c)
        if webhook_url:
            try:
                webhook = discord.Webhook.from_url(webhook_url, client=self.bot)
                wh_obj = await webhook.fetch()
                if wh_obj.channel_id:
                    wh_chan = guild.get_channel(wh_obj.channel_id)
                    if wh_chan:
                        channels_to_scan.add(wh_chan)
            except Exception:
                pass

        for target_channel in channels_to_scan:
            can_manage = target_channel.permissions_for(guild.me).manage_messages
            try:
                async for msg in target_channel.history(limit=50):
                    is_lb = False
                    for emb in msg.embeds:
                        if emb.title in ["Live Invite Leaderboard", "Invite Leaderboard"]:
                            is_lb = True
                            break
                    if is_lb:
                        if can_manage or msg.author.id == guild.me.id:
                            try:
                                await msg.delete()
                                deleted_count += 1
                                await asyncio.sleep(0.3)
                            except Exception as exc:
                                log.debug(f"Residual liveboard cleanup error: {exc}")
            except Exception as exc:
                log.debug(f"Channel history scan error: {exc}")

        await self.config.guild(guild).live_message_id.set(None)
        self._liveboard_cache.pop(guild.id, None)
        return deleted_count

    async def _build_user_rank_embed(self, guild: discord.Guild, member: discord.Member) -> discord.Embed:
        """Construct the private rank embed returned when clicking Refresh or stats."""
        data = await self.config.member(member).all()
        real = data.get("real", 0)
        left = data.get("left", 0)
        fake = data.get("fake", 0)
        bonus = data.get("bonus", 0)
        code = data.get("contest_code")
        disqualified = data.get("disqualified", False)
        net = real - left - fake + bonus

        all_members = await self.config.all_members(guild)
        scores = []
        for mid, d in all_members.items():
            if not d.get("disqualified", False):
                n = d.get("real", 0) - d.get("left", 0) - d.get("fake", 0) + d.get("bonus", 0)
                scores.append((mid, n))
        scores.sort(key=lambda x: x[1], reverse=True)

        rank_num = None
        for idx, (mid, sc) in enumerate(scores, start=1):
            if mid == member.id:
                rank_num = idx
                break

        if rank_num == 1:
            rank_display = f"🥇 Rank #1 of {len(scores)}"
        elif rank_num == 2:
            rank_display = f"🥈 Rank #2 of {len(scores)}"
        elif rank_num == 3:
            rank_display = f"🥉 Rank #3 of {len(scores)}"
        elif rank_num:
            rank_display = f"Rank #{rank_num} of {len(scores)}"
        else:
            rank_display = "Unranked"

        pts_label = "point" if abs(net) == 1 else "points"
        embed = discord.Embed(
            title=f"Your Current Standings : {member.display_name}",
            color=discord.Color.red() if disqualified else discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        if disqualified:
            embed.description = "⚠️ **Disqualified by an administrator.**"

        embed.add_field(name="Current Rank", value=f"**{rank_display}**", inline=True)
        embed.add_field(name="Net Score", value=f"**{net} {pts_label}**", inline=True)

        parts = [f"✅ Real: **{real}**", f"🚪 Left: **{left}**", f"⚠️ Fake: **{fake}**"]
        if bonus != 0:
            parts.append(f"⭐ Bonus: **{bonus:+d}**")
        pending_count = sum(
            1 for mid, d in all_members.items()
            if d.get("invited_by") == member.id and (d.get("pending_onboarding", False) or d.get("pending_cross_verify", False))
        )
        if pending_count > 0:
            parts.append(f"⏳ Pending: **{pending_count}**")
        rejoin_count = sum(
            1 for mid, d in all_members.items()
            if d.get("invited_by") == member.id and d.get("is_rejoin", False)
        )
        if rejoin_count > 0:
            parts.append(f"🔄 Re-join: **{rejoin_count}**")
        embed.add_field(name="Score Breakdown", value=" • ".join(parts), inline=False)

        if code:
            embed.add_field(name="Your Dedicated Link", value=f"https://discord.gg/{code}", inline=False)
        else:
            embed.add_field(
                name="Your Dedicated Link",
                value="No dedicated link yet. Click **Get My Invite Link** below to create one.",
                inline=False
            )

        embed.set_footer(text="Only you can see this message • 1m refresh cooldown")
        return embed

    async def _build_invite_info_embed(
        self,
        guild: discord.Guild,
        target: Union[discord.Member, discord.User, int, Any],
        prefix: str = "[p]",
        include_privacy_hint: bool = False,
        page: int = 1,
        per_page: int = 10,
    ) -> Tuple[discord.Embed, int]:
        """Construct the comprehensive invite breakdown embed for a member."""
        if isinstance(target, int):
            resolved = guild.get_member(target) or self.bot.get_user(target)
            if not resolved:
                try:
                    resolved = await self.bot.fetch_user(target)
                except Exception:
                    resolved = None
            if resolved:
                target = resolved
            else:
                class _FallbackAvatar:
                    url = "https://cdn.discordapp.com/embed/avatars/0.png"

                class _FallbackUser:
                    def __init__(self, uid: int):
                        self.id = uid
                        self.display_name = f"User {uid}"
                        self.mention = f"<@{uid}>"
                        self.display_avatar = _FallbackAvatar()

                target = _FallbackUser(target)

        data = await self.config.member_from_ids(guild.id, target.id).all()
        real = data.get("real", 0)
        left = data.get("left", 0)
        fake = data.get("fake", 0)
        bonus = data.get("bonus", 0)
        code = data.get("contest_code")
        disqualified = data.get("disqualified", False)
        net = real - left - fake + bonus

        all_members = await self.config.all_members(guild)
        scores = []
        for mid, d in all_members.items():
            if not d.get("disqualified", False):
                n = d.get("real", 0) - d.get("left", 0) - d.get("fake", 0) + d.get("bonus", 0)
                scores.append((mid, n))
        scores.sort(key=lambda x: x[1], reverse=True)

        rank_str = "Unranked"
        for idx, (mid, sc) in enumerate(scores, start=1):
            if mid == target.id:
                rank_str = f"#{idx} of {len(scores)}"
                break

        invited_records = []
        for mid, d in all_members.items():
            if d.get("invited_by") == target.id:
                invited_records.append((int(mid), d))

        invited_records.sort(
            key=lambda x: (x[1].get("joined_at") or 0, x[0]),
            reverse=True
        )

        total_records = len(invited_records)
        per_page = max(1, per_page)
        total_pages = max(1, (total_records + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        pts_label = "point" if abs(net) == 1 else "points"
        embed = discord.Embed(
            title=f"Invite Records : {target.display_name}",
            color=discord.Color.red() if disqualified else (discord.Color.green() if net > 0 else discord.Color.blue()),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        overview_lines = [
            f"**Target:** {target.mention} (`{target.id}`)",
            f"**Net Score:** **{net} {pts_label}** • **Rank:** {rank_str}",
        ]
        breakdown_parts = [
            f"✅ **{real}** Real",
            f"🚪 **{left}** Left",
            f"⚠️ **{fake}** Fake"
        ]
        if bonus != 0:
            breakdown_parts.append(f"⭐ **{bonus:+d}** Bonus")

        pending_count = sum(
            1 for _, d in invited_records
            if d.get("pending_onboarding", False) or d.get("pending_cross_verify", False)
        )
        if pending_count > 0:
            breakdown_parts.append(f"⏳ **{pending_count}** Pending")
        rejoin_count = sum(
            1 for _, d in invited_records
            if d.get("is_rejoin", False)
        )
        if rejoin_count > 0:
            breakdown_parts.append(f"🔄 **{rejoin_count}** Re-join")

        overview_lines.append(f"**Breakdown:** {' • '.join(breakdown_parts)}")

        cv_enabled, cv_server, _ = await self._get_cross_verify_names(guild)
        if cv_enabled:
            overview_lines.append(f"> ℹ️ **Notice:** Invites count as **Real (+1)** only after joining **{cv_server}** and getting verified.")

        if code:
            overview_lines.append(f"**Dedicated Link:** https://discord.gg/{code}")
        else:
            overview_lines.append(f"**Dedicated Link:** None (Click 'Get My Invite Link' or `{prefix}eventlink`)")

        if disqualified:
            overview_lines.append("⚠️ **Disqualified by Administrator**")

        overview_lines.append("")
        overview_lines.append(f"__**Invited Members ({total_records})**__")

        if not invited_records:
            overview_lines.append("*No recorded invites found for this user.*")
            embed.description = "\n".join(overview_lines)
        else:
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            page_records = invited_records[start_idx:end_idx]

            desc_lines = list(overview_lines)
            max_desc_length = 3800

            for idx, (mid, d) in enumerate(page_records, start=start_idx + 1):
                m_obj = guild.get_member(mid)
                is_fake = d.get("is_fake", False)
                is_rejoin = d.get("is_rejoin", False)
                pending_ob = d.get("pending_onboarding", False)
                pending_cv = d.get("pending_cross_verify", False)
                join_code = d.get("join_code") or "Unknown"
                joined_at = d.get("joined_at")
                created_ts = int(discord.utils.snowflake_time(mid).timestamp())

                if is_fake:
                    status_badge = "⚠️ Fake / Alt (-1) • Left" if m_obj is None else "⚠️ Fake / Alt (-1)"
                elif is_rejoin:
                    status_badge = "🔄 Re-join (0) • Left" if m_obj is None else "🔄 Re-join (0)"
                elif pending_ob:
                    status_badge = "⏳ Left Before Screening (0)" if m_obj is None else "⏳ Pending Screening (0)"
                elif pending_cv:
                    server_label = cv_server if cv_enabled else "Partner"
                    status_badge = f"⏳ Left Before {server_label} Role (0)" if m_obj is None else f"⏳ Pending {server_label} Role (0)"
                elif m_obj is None:
                    status_badge = "🚪 Left (-1)"
                else:
                    status_badge = "✅ Real (+1)"

                joined_str = f"<t:{int(joined_at)}:R>" if joined_at else "Unknown"
                created_str = f"<t:{created_ts}:d>"

                left_str = None
                if is_rejoin or m_obj is None:
                    hist_info = self._get_historical_member_info(mid)
                    if hist_info and hist_info.get("last_left_at"):
                        left_str = f"<t:{int(hist_info['last_left_at'])}:R>"

                detail_parts = [f"Code: `{join_code}`", f"Joined: {joined_str}"]
                if left_str:
                    label = "Prev Left" if is_rejoin else "Left"
                    detail_parts.append(f"{label}: {left_str}")
                detail_parts.append(f"Created: {created_str}")

                entry_lines = [
                    f"**{idx}.** <@{mid}> (`{mid}`) • {status_badge}",
                    f"     {' • '.join(detail_parts)}"
                ]
                entry_text = "\n".join(entry_lines)

                current_len = sum(len(line) + 1 for line in desc_lines)
                if current_len + len(entry_text) + 80 > max_desc_length:
                    break

                desc_lines.append(entry_text)

            embed.description = "\n".join(desc_lines)

        if total_pages > 1:
            footer_text = f"{guild.name} • Page {page} of {total_pages} • Total Recorded: {total_records}"
        else:
            footer_text = f"{guild.name} • Total Recorded: {total_records}"

        if include_privacy_hint:
            footer_text += " • Only you can see this message"
        embed.set_footer(text=footer_text)
        return embed, total_pages

    async def _rpc_get_status(self, guild_id: Optional[int] = None) -> Dict[str, Any]:
        """RPC handler returning invite tracking and capacity status for all guilds."""
        results = []
        target_guilds = [self.bot.get_guild(guild_id)] if guild_id else self.bot.guilds
        for guild in target_guilds:
            if not guild:
                continue
            cached = self.invite_cache.get(guild.id, {})
            cached_count = len(cached)
            can_manage = guild.me.guild_permissions.manage_guild
            zero_uses = sum(1 for uses in cached.values() if uses == 0)
            live_chan = await self.config.guild(guild).live_channel_id()
            live_msg = await self.config.guild(guild).live_message_id()
            results.append({
                "guild_id": guild.id,
                "guild_name": guild.name,
                "can_manage": can_manage,
                "cached_invites": cached_count,
                "zero_uses": zero_uses,
                "invite_cap": DISCORD_INVITE_CAP,
                "is_near_cap": cached_count >= 950,
                "liveboard_configured": bool(live_chan and live_msg),
            })
        return {"guilds": results}

    async def _rpc_audit_guild(self, guild_id: int) -> Dict[str, Any]:
        """RPC helper to run dry-run audit for a specific guild."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"error": "Guild not found"}
        if not guild.me.guild_permissions.manage_guild:
            return {"error": "Missing Manage Server permission"}

        invites = await guild.invites()
        contest_links = await self.config.guild(guild).contest_links()
        protected_codes = set(contest_links.keys())

        now = datetime.now(timezone.utc)
        zero_uses = 0
        departed_creator = 0
        expired_or_max = 0
        stale_single_use = 0
        pruneable = 0

        for inv in invites:
            if inv.code in protected_codes:
                continue
            is_zero = (inv.uses == 0)
            creator_left = (inv.inviter is None or guild.get_member(inv.inviter.id) is None)
            is_maxed = (inv.max_uses > 0 and inv.uses >= inv.max_uses)
            is_expired = False
            created = inv.created_at or now
            age_seconds = (now - created).total_seconds()
            if inv.max_age > 0 and age_seconds >= inv.max_age:
                is_expired = True

            # Stale threshold: single use and older than 7 days (604,800s)
            is_stale_single = (inv.uses == 1 and age_seconds >= 604800)

            if is_zero:
                zero_uses += 1
            if creator_left:
                departed_creator += 1
            if is_maxed or is_expired:
                expired_or_max += 1
            if is_stale_single:
                stale_single_use += 1

            if is_zero or creator_left or is_maxed or is_expired or is_stale_single:
                pruneable += 1

        return {
            "guild_id": guild.id,
            "guild_name": guild.name,
            "total_invites": len(invites),
            "invite_cap": DISCORD_INVITE_CAP,
            "zero_uses": zero_uses,
            "departed_creator": departed_creator,
            "expired_or_max": expired_or_max,
            "stale_single_use": stale_single_use,
            "protected_contest_links": len(protected_codes),
            "pruneable_count": pruneable,
            "recoverable_slots": pruneable,
            "forecast_after_prune": len(invites) - pruneable,
        }

    async def _rpc_refresh_liveboard(self, guild_id: int) -> Dict[str, Any]:
        """RPC helper to force-refresh liveboard and return its content."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"error": "Guild not found"}
        channel_id = await self.config.guild(guild).live_channel_id()
        webhook_url = await self.config.guild(guild).webhook_url()
        if not channel_id and not webhook_url:
            return {"error": "Liveboard not configured"}
        await self._update_liveboard_message(guild, force_render=True)
        scores = await self._get_ranked_scores(guild)
        embed = await self._build_leaderboard_embed(guild, is_live=True, scores=scores)
        podium_bytes = await self._generate_podium_image(guild, scores[:3])
        if podium_bytes:
            try:
                with open("/home/sablinova/final_podium_live.png", "wb") as f:
                    f.write(podium_bytes)
            except Exception:
                pass
        return {
            "success": True,
            "title": embed.title,
            "description": embed.description,
            "footer": embed.footer.text if embed.footer else None,
            "has_podium": bool(podium_bytes),
            "podium_size": len(podium_bytes) if podium_bytes else 0,
            "fields": [f.to_dict() for f in embed.fields]
        }

    async def _rpc_remove_liveboard(self, guild_id: int) -> Dict[str, Any]:
        """RPC helper to forcefully delete all liveboard messages and disable liveboard."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"error": "Guild not found"}
        await self.config.guild(guild).liveboard_enabled.set(False)
        deleted_count = await self._delete_all_liveboard_messages(guild)
        await self.config.guild(guild).live_channel_id.set(None)
        await self.config.guild(guild).live_message_id.set(None)
        return {"success": True, "deleted_count": deleted_count}

    async def _rpc_check_member(self, member_id: int, cross_guild_id: int, cross_role_id: int) -> Dict[str, Any]:
        """RPC helper to inspect member presence and roles in a partner guild."""
        tg = self.bot.get_guild(cross_guild_id)
        if not tg:
            return {"error": "Guild not found"}
        tm = tg.get_member(member_id)
        fetched = False
        if not tm:
            try:
                tm = await tg.fetch_member(member_id)
                fetched = True
            except Exception as e:
                return {"in_guild": False, "error": str(e)}
        roles = [r.id for r in tm.roles]
        role_map = {r.id: r.name for r in tm.roles}
        has_role = cross_role_id in roles
        return {
            "in_guild": True,
            "cached": not fetched,
            "roles": roles,
            "role_names": role_map,
            "has_role": has_role,
            "cross_role_id": cross_role_id,
            "member_name": str(tm),
            "joined_at": tm.joined_at.isoformat() if tm.joined_at else None,
            "pending": getattr(tm, "pending", False),
            "completed_onboarding": getattr(getattr(tm, "flags", None), "completed_onboarding", None),
            "is_awaiting_onboarding": self._is_member_awaiting_onboarding(tm),
            "guild_features": list(tg.features),
            "bot_permissions": {
                "manage_guild": tg.me.guild_permissions.manage_guild,
                "administrator": tg.me.guild_permissions.administrator
            }
        }

    async def _rpc_cross_verify_sync(self, guild_id: int) -> Dict[str, Any]:
        """RPC handler to audit and sync cross-server verification statuses for a guild."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"error": "Guild not found"}

        server_id = await self.config.guild(guild).cross_verify_guild_id()
        role_id = await self.config.guild(guild).cross_verify_role_id()
        if not server_id:
            return {"error": "No partner server configured"}

        target_guild = self.bot.get_guild(server_id)
        if not target_guild:
            return {"error": f"Bot is not in partner server {server_id}"}

        all_members = await self.config.all_members(guild)
        newly_verified = 0
        reverted_to_pending = 0
        still_pending = 0
        details = []

        for mid_str, d in all_members.items():
            mid = int(mid_str)
            if d.get("is_fake", False):
                continue
            inviter_id = d.get("invited_by")
            if not inviter_id:
                continue

            m_obj = guild.get_member(mid)
            if not m_obj:
                continue

            is_cv = await self._is_cross_server_verified(mid, server_id, role_id)
            pending_cv = d.get("pending_cross_verify", False)
            pending_ob = d.get("pending_onboarding", False)
            was_cv = d.get("cross_verified", False)

            if is_cv:
                if pending_cv or pending_ob or not was_cv:
                    await self.config.member(m_obj).pending_cross_verify.set(False)
                    await self.config.member(m_obj).pending_onboarding.set(False)
                    await self.config.member(m_obj).cross_verified.set(True)
                    cur_real = await self.config.member_from_ids(guild.id, inviter_id).real()
                    await self.config.member_from_ids(guild.id, inviter_id).real.set(cur_real + 1)
                    newly_verified += 1
                    details.append(f"{mid}: verified (+1 to {inviter_id})")
            else:
                if was_cv or not pending_cv:
                    await self.config.member(m_obj).pending_cross_verify.set(True)
                    await self.config.member(m_obj).cross_verified.set(False)
                    cur_real = await self.config.member_from_ids(guild.id, inviter_id).real()
                    if cur_real > 0:
                        await self.config.member_from_ids(guild.id, inviter_id).real.set(cur_real - 1)
                    reverted_to_pending += 1
                    details.append(f"{mid}: moved to pending (-1 from {inviter_id})")
                else:
                    still_pending += 1

        if newly_verified > 0 or reverted_to_pending > 0:
            await self._update_liveboard_message(guild)

        return {
            "success": True,
            "newly_verified": newly_verified,
            "reverted_to_pending": reverted_to_pending,
            "still_pending": still_pending,
            "details": details
        }

    async def _rpc_set_member_invite(
        self,
        guild_id: int,
        member_id: int,
        inviter_id: Optional[int] = None,
        code: Optional[str] = None,
        is_rejoin: bool = False
    ) -> Dict[str, Any]:
        """RPC helper to link an invited member to an inviter and mark re-join status."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return {"error": "Guild not found"}
        m_conf = self.config.member_from_ids(guild_id, member_id)
        if inviter_id is not None:
            await m_conf.invited_by.set(inviter_id)
        if code is not None:
            await m_conf.join_code.set(code)
        if is_rejoin:
            await m_conf.is_rejoin.set(True)
        return {
            "success": True,
            "guild_id": guild_id,
            "member_id": member_id,
            "inviter_id": inviter_id,
            "code": code,
            "is_rejoin": is_rejoin
        }

    async def _init_cache(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self._cache_guild_invites(guild)
            except Exception as exc:
                log.warning(f"Failed to cache invites for guild {guild.id}: {exc}")

    async def _cache_guild_invites(self, guild: discord.Guild) -> int:
        if not guild.me.guild_permissions.manage_guild:
            return 0
        try:
            invites = await guild.invites()
            self.invite_cache[guild.id] = {inv.code: inv.uses or 0 for inv in invites}
            if guild.vanity_url_code:
                try:
                    vanity = await guild.vanity_invite()
                    if vanity:
                        self.vanity_cache[guild.id] = vanity.uses or 0
                except Exception:
                    pass
            return len(invites)
        except discord.Forbidden:
            log.warning(f"Missing Manage Guild permission in {guild.name} ({guild.id})")
            return 0
        except Exception as exc:
            log.error(f"Error fetching invites for {guild.id}: {exc}")
            return 0

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        guild = invite.guild
        if not guild:
            return
        if guild.id not in self.invite_cache:
            self.invite_cache[guild.id] = {}
        self.invite_cache[guild.id][invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        guild = invite.guild
        if not guild:
            return
        if guild.id in self.invite_cache:
            self.invite_cache[guild.id].pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        guild = member.guild
        if not guild.me.guild_permissions.manage_guild:
            return

        cached_invites = self.invite_cache.get(guild.id, {})
        matched_invite: Optional[discord.Invite] = None
        matched_code: Optional[str] = None
        is_vanity = False

        try:
            current_invites = await guild.invites()
        except Exception as exc:
            log.error(f"Error fetching invites on member join in {guild.id}: {exc}")
            return

        for inv in current_invites:
            old_uses = cached_invites.get(inv.code, 0)
            if (inv.uses or 0) > old_uses:
                matched_invite = inv
                matched_code = inv.code
                break

        if not matched_invite and guild.vanity_url_code:
            try:
                vanity = await guild.vanity_invite()
                if vanity:
                    old_vanity_uses = self.vanity_cache.get(guild.id, 0)
                    if (vanity.uses or 0) > old_vanity_uses:
                        is_vanity = True
                        matched_code = vanity.code
                        self.vanity_cache[guild.id] = vanity.uses or 0
            except Exception:
                pass

        self.invite_cache[guild.id] = {inv.code: inv.uses or 0 for inv in current_invites}

        inviter_id: Optional[int] = None
        inviter_user: Optional[discord.User] = None

        if matched_invite:
            contest_links = await self.config.guild(guild).contest_links()
            # Dedicated contest invite code maps to original owner
            if matched_invite.code in contest_links:
                inviter_id = contest_links[matched_invite.code]
                inviter_user = guild.get_member(inviter_id) or await self.bot.get_or_fetch_user(inviter_id)
            elif matched_invite.inviter:
                inviter_id = matched_invite.inviter.id
                inviter_user = matched_invite.inviter
        elif is_vanity:
            matched_code = f"vanity/{matched_code}"

        prev_joined_at = await self.config.member(member).joined_at()
        if not prev_joined_at:
            hist_ts = self._get_historical_join_time(member.id)
            if hist_ts:
                prev_joined_at = hist_ts
                await self.config.member(member).joined_at.set(hist_ts)

        is_rejoin = prev_joined_at is not None

        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        account_age_days = (now - member.created_at).total_seconds() / 86400.0
        min_age_days = await self.config.guild(guild).min_age_days()
        is_fake = account_age_days < min_age_days

        status_text = ""

        require_onboarding = await self.config.guild(guild).require_onboarding()
        awaiting_onboarding = require_onboarding and self._is_member_awaiting_onboarding(member)

        cross_verify_enabled = await self.config.guild(guild).cross_verify_enabled()
        cross_guild_id = await self.config.guild(guild).cross_verify_guild_id()
        cross_role_id = await self.config.guild(guild).cross_verify_role_id()
        is_cv = not cross_verify_enabled or await self._is_cross_server_verified(member.id, cross_guild_id, cross_role_id)

        if inviter_id and inviter_id == member.id:
            status_text = "Self-invite detected. No points awarded."
            inviter_id = None
            inviter_user = None

        if inviter_id and inviter_user:
            if is_rejoin:
                status_text = "Re-join detected. Member previously joined. No duplicate points awarded."
                await self.config.member(member).invited_by.set(inviter_id)
                await self.config.member(member).join_code.set(matched_code)
                await self.config.member(member).joined_at.set(now_ts)
                await self.config.member(member).is_rejoin.set(True)
            elif is_fake:
                current_fake = await self.config.member_from_ids(guild.id, inviter_id).fake()
                await self.config.member_from_ids(guild.id, inviter_id).fake.set(current_fake + 1)
                await self.config.member(member).invited_by.set(inviter_id)
                await self.config.member(member).join_code.set(matched_code)
                await self.config.member(member).joined_at.set(now_ts)
                await self.config.member(member).is_fake.set(True)
                status_text = f"Alt/Fake detected (< {min_age_days}d old). -1 Fake penalty applied."
            elif is_cv:
                current_real = await self.config.member_from_ids(guild.id, inviter_id).real()
                await self.config.member_from_ids(guild.id, inviter_id).real.set(current_real + 1)
                await self.config.member(member).invited_by.set(inviter_id)
                await self.config.member(member).join_code.set(matched_code)
                await self.config.member(member).joined_at.set(now_ts)
                await self.config.member(member).is_fake.set(False)
                await self.config.member(member).cross_verified.set(True)
                await self.config.member(member).pending_cross_verify.set(False)
                await self.config.member(member).pending_onboarding.set(False)
                status_text = "Verified partner server member. +1 Real point awarded."
            elif not is_cv:
                await self.config.member(member).pending_cross_verify.set(True)
                if awaiting_onboarding:
                    await self.config.member(member).pending_onboarding.set(True)
                await self.config.member(member).invited_by.set(inviter_id)
                await self.config.member(member).join_code.set(matched_code)
                await self.config.member(member).joined_at.set(now_ts)
                status_text = "Pending partner server verification. Points held in escrow until member joins required server and gets role."
            else:
                current_real = await self.config.member_from_ids(guild.id, inviter_id).real()
                await self.config.member_from_ids(guild.id, inviter_id).real.set(current_real + 1)
                await self.config.member(member).invited_by.set(inviter_id)
                await self.config.member(member).join_code.set(matched_code)
                await self.config.member(member).joined_at.set(now_ts)
                await self.config.member(member).is_fake.set(False)
                await self.config.member(member).cross_verified.set(True)
                status_text = "Legitimate join verified. +1 Real point awarded."
        else:
            await self.config.member(member).joined_at.set(now_ts)
            if is_vanity:
                status_text = "Joined using server Vanity URL."
            else:
                status_text = "Unknown invite or direct integration."

        log_channel_id = await self.config.guild(guild).log_channel_id()
        if log_channel_id:
            log_chan = guild.get_channel(log_channel_id)
            if log_chan and log_chan.permissions_for(guild.me).send_messages:
                embed = discord.Embed(
                    title="Member Joined: Invite Event",
                    color=(
                        discord.Color.gold() if (awaiting_onboarding or (inviter_id and not is_cv and not is_fake))
                        else (discord.Color.red() if is_fake else (discord.Color.green() if inviter_id and not is_rejoin else discord.Color.blue()))
                    ),
                    timestamp=now
                )
                embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)
                embed.add_field(
                    name="Account Age",
                    value=f"{account_age_days:.1f} days (Created <t:{int(member.created_at.timestamp())}:R>)",
                    inline=True
                )
                embed.add_field(name="Invite Code", value=f"`{matched_code or 'Unknown'}`", inline=True)
                if inviter_user:
                    inv_data = await self.config.member_from_ids(guild.id, inviter_id).all()
                    net_score = inv_data["real"] - inv_data["left"] - inv_data["fake"] + inv_data["bonus"]
                    embed.add_field(
                        name="Invited By",
                        value=f"{inviter_user.mention} (`{inviter_user.id}`)\nScore: **{net_score}** (Real: {inv_data['real']} | Left: {inv_data['left']} | Fake: {inv_data['fake']} | Bonus: {inv_data['bonus']})",
                        inline=False
                    )
                embed.add_field(name="Outcome", value=status_text, inline=False)
                embed.set_thumbnail(url=member.display_avatar.url)
                try:
                    await log_chan.send(embed=embed)
                except discord.HTTPException:
                    pass

        if not awaiting_onboarding and is_cv and inviter_id and not is_rejoin and not is_fake:
            try:
                await self._update_liveboard_message(guild)
            except Exception as exc:
                log.debug(f"Liveboard update error on member join: {exc}")

        # Check if member joined a designated partner verification server
        all_guild_configs = await self.config.all_guilds()
        for g_id, g_cfg in all_guild_configs.items():
            if not g_cfg.get("cross_verify_enabled", False):
                continue
            if g_cfg.get("cross_verify_guild_id") != guild.id:
                continue

            contest_guild = self.bot.get_guild(g_id)
            if not contest_guild:
                continue

            contest_member = contest_guild.get_member(member.id)
            if not contest_member:
                continue

            req_role_id = g_cfg.get("cross_verify_role_id")
            has_role = not req_role_id or any(r.id == req_role_id for r in member.roles)
            if has_role:
                m_data = await self.config.member(contest_member).all()
                inv_id = m_data.get("invited_by")
                if not inv_id or m_data.get("is_fake", False):
                    continue

                p_ob = m_data.get("pending_onboarding", False)
                p_cv = m_data.get("pending_cross_verify", False)
                w_cv = m_data.get("cross_verified", False)

                if (p_cv or p_ob) and not w_cv:
                    await self.config.member(contest_member).pending_cross_verify.set(False)
                    await self.config.member(contest_member).pending_onboarding.set(False)
                    await self.config.member(contest_member).cross_verified.set(True)
                    c_real = await self.config.member_from_ids(contest_guild.id, inv_id).real()
                    await self.config.member_from_ids(contest_guild.id, inv_id).real.set(c_real + 1)
                    await self._log_cross_verify_event(
                        contest_guild, contest_member, inv_id,
                        f"Member joined partner server (**{guild.name}**) and acquired verified role! +1 Real point awarded.",
                        is_positive=True
                    )
                    await self._update_liveboard_message(contest_guild)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.bot:
            return
        guild = after.guild

        # 1. Check if member completed onboarding in this guild
        is_pending_db = await self.config.member(after).pending_onboarding()
        if is_pending_db and not self._is_member_awaiting_onboarding(after):
            await self.config.member(after).pending_onboarding.set(False)
            inviter_id = await self.config.member(after).invited_by()
            if inviter_id:
                inviter_user = guild.get_member(inviter_id) or await self.bot.get_or_fetch_user(inviter_id)
                now = datetime.now(timezone.utc)
                account_age_days = (now - after.created_at).total_seconds() / 86400.0
                min_age_days = await self.config.guild(guild).min_age_days()
                is_fake = account_age_days < min_age_days
                matched_code = await self.config.member(after).join_code()

                cross_verify_enabled = await self.config.guild(guild).cross_verify_enabled()
                cross_guild_id = await self.config.guild(guild).cross_verify_guild_id()
                cross_role_id = await self.config.guild(guild).cross_verify_role_id()
                is_cv = not cross_verify_enabled or await self._is_cross_server_verified(after.id, cross_guild_id, cross_role_id)

                if is_fake:
                    current_fake = await self.config.member_from_ids(guild.id, inviter_id).fake()
                    await self.config.member_from_ids(guild.id, inviter_id).fake.set(current_fake + 1)
                    await self.config.member(after).is_fake.set(True)
                    status_text = f"Completed onboarding, but Alt/Fake detected (< {min_age_days}d old). -1 Fake penalty applied."
                elif not is_cv:
                    await self.config.member(after).pending_cross_verify.set(True)
                    status_text = "Completed onboarding, but still pending partner server verification. Points held in escrow."
                else:
                    current_real = await self.config.member_from_ids(guild.id, inviter_id).real()
                    await self.config.member_from_ids(guild.id, inviter_id).real.set(current_real + 1)
                    await self.config.member(after).is_fake.set(False)
                    await self.config.member(after).cross_verified.set(True)
                    await self.config.member(after).pending_cross_verify.set(False)
                    status_text = "Onboarding and partner verification completed. +1 Real point awarded."

                log_channel_id = await self.config.guild(guild).log_channel_id()
                if log_channel_id:
                    log_chan = guild.get_channel(log_channel_id)
                    if log_chan and log_chan.permissions_for(guild.me).send_messages:
                        embed = discord.Embed(
                            title="Member Verified: Onboarding Completed",
                            color=discord.Color.red() if is_fake else (discord.Color.gold() if not is_cv else discord.Color.green()),
                            timestamp=now
                        )
                        embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=False)
                        embed.add_field(
                            name="Account Age",
                            value=f"{account_age_days:.1f} days (Created <t:{int(after.created_at.timestamp())}:R>)",
                            inline=True
                        )
                        embed.add_field(name="Invite Code", value=f"`{matched_code or 'Unknown'}`", inline=True)
                        if inviter_user:
                            inv_data = await self.config.member_from_ids(guild.id, inviter_id).all()
                            net_score = inv_data["real"] - inv_data["left"] - inv_data["fake"] + inv_data["bonus"]
                            embed.add_field(
                                name="Invited By",
                                value=f"{inviter_user.mention} (`{inviter_user.id}`)\nScore: **{net_score}** (Real: {inv_data['real']} | Left: {inv_data['left']} | Fake: {inv_data['fake']} | Bonus: {inv_data['bonus']})",
                                inline=False
                            )
                        embed.add_field(name="Outcome", value=status_text, inline=False)
                        embed.set_thumbnail(url=after.display_avatar.url)
                        try:
                            await log_chan.send(embed=embed)
                        except discord.HTTPException:
                            pass

                if is_cv and not is_fake:
                    try:
                        await self._update_liveboard_message(guild)
                    except Exception as exc:
                        log.debug(f"Liveboard update error on onboarding completion: {exc}")

        # 2. Check if after.guild is a partner verification guild for any contest guild
        all_guild_configs = await self.config.all_guilds()
        for g_id, g_cfg in all_guild_configs.items():
            if not g_cfg.get("cross_verify_enabled", False):
                continue
            if g_cfg.get("cross_verify_guild_id") != guild.id:
                continue

            contest_guild = self.bot.get_guild(g_id)
            if not contest_guild:
                continue

            contest_member = contest_guild.get_member(after.id)
            if not contest_member:
                continue

            req_role_id = g_cfg.get("cross_verify_role_id")
            had_role = not req_role_id or any(r.id == req_role_id for r in before.roles)
            has_role = not req_role_id or any(r.id == req_role_id for r in after.roles)

            m_data = await self.config.member(contest_member).all()
            inviter_id = m_data.get("invited_by")
            if not inviter_id or m_data.get("is_fake", False):
                continue

            # Role gained in partner server
            if has_role and not had_role:
                pending_ob = m_data.get("pending_onboarding", False)
                pending_cv = m_data.get("pending_cross_verify", False)
                was_cv = m_data.get("cross_verified", False)

                if (pending_cv or pending_ob) and not was_cv:
                    await self.config.member(contest_member).pending_cross_verify.set(False)
                    await self.config.member(contest_member).pending_onboarding.set(False)
                    await self.config.member(contest_member).cross_verified.set(True)
                    cur_real = await self.config.member_from_ids(contest_guild.id, inviter_id).real()
                    await self.config.member_from_ids(contest_guild.id, inviter_id).real.set(cur_real + 1)
                    await self._log_cross_verify_event(
                        contest_guild, contest_member, inviter_id,
                        f"Member acquired verified role in partner server (**{guild.name}**)! +1 Real point awarded.",
                        is_positive=True
                    )
                    await self._update_liveboard_message(contest_guild)
                elif was_cv:
                    cur_left = await self.config.member_from_ids(contest_guild.id, inviter_id).left()
                    if cur_left > 0:
                        await self.config.member_from_ids(contest_guild.id, inviter_id).left.set(cur_left - 1)
                        await self._log_cross_verify_event(
                            contest_guild, contest_member, inviter_id,
                            f"Member re-acquired required role in partner server (**{guild.name}**). -1 leave penalty reversed!",
                            is_positive=True
                        )
                        await self._update_liveboard_message(contest_guild)

            # Role lost in partner server
            elif had_role and not has_role:
                was_cv = m_data.get("cross_verified", False)
                if was_cv:
                    cur_left = await self.config.member_from_ids(contest_guild.id, inviter_id).left()
                    await self.config.member_from_ids(contest_guild.id, inviter_id).left.set(cur_left + 1)
                    await self.config.member(contest_member).cross_verified.set(False)
                    await self.config.member(contest_member).pending_cross_verify.set(True)
                    await self._log_cross_verify_event(
                        contest_guild, contest_member, inviter_id,
                        f"Member lost required verification role in partner server (**{guild.name}**). -1 point deducted.",
                        is_positive=False
                    )
                    await self._update_liveboard_message(contest_guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        guild = member.guild

        # 1. Handle leave from the current guild
        inviter_id = await self.config.member(member).invited_by()
        is_fake = await self.config.member(member).is_fake()
        is_rejoin = await self.config.member(member).is_rejoin()
        was_pending_ob = await self.config.member(member).pending_onboarding()
        was_pending_cv = await self.config.member(member).pending_cross_verify()
        was_cv = await self.config.member(member).cross_verified()
        was_pending = (was_pending_ob or was_pending_cv) and not was_cv

        if inviter_id:
            inviter_user = guild.get_member(inviter_id) or await self.bot.get_or_fetch_user(inviter_id)

            if was_pending:
                await self.config.member(member).pending_onboarding.set(False)
                await self.config.member(member).pending_cross_verify.set(False)
                status_text = "Departed before completing verification. No points deducted."
            elif is_rejoin:
                status_text = "No change (was a re-join, no initial points awarded)."
            elif not is_fake:
                current_left = await self.config.member_from_ids(guild.id, inviter_id).left()
                await self.config.member_from_ids(guild.id, inviter_id).left.set(current_left + 1)
                status_text = "-1 point deducted (Member left server)."
            else:
                status_text = "No change (was flagged alt)."

            log_channel_id = await self.config.guild(guild).log_channel_id()
            if log_channel_id:
                log_chan = guild.get_channel(log_channel_id)
                if log_chan and log_chan.permissions_for(guild.me).send_messages:
                    inv_data = await self.config.member_from_ids(guild.id, inviter_id).all()
                    net_score = inv_data["real"] - inv_data["left"] - inv_data["fake"] + inv_data["bonus"]
                    now = datetime.now(timezone.utc)
                    embed = discord.Embed(
                        title="Member Left: Score Adjusted",
                        color=discord.Color.dark_red() if (not was_pending and not is_rejoin and not is_fake) else discord.Color.greyple(),
                        timestamp=now
                    )
                    embed.add_field(name="Departed Member", value=f"{member.mention} (`{member.id}`)", inline=False)
                    if inviter_user:
                        embed.add_field(
                            name="Original Inviter",
                            value=f"{inviter_user.mention} (`{inviter_user.id}`)\nUpdated Score: **{net_score}** (Real: {inv_data['real']} | Left: {inv_data['left']} | Fake: {inv_data['fake']} | Bonus: {inv_data['bonus']})",
                            inline=False
                        )
                    embed.add_field(name="Deduction", value=status_text, inline=False)
                    embed.set_thumbnail(url=member.display_avatar.url)
                    try:
                        await log_chan.send(embed=embed)
                    except discord.HTTPException:
                        pass

            if not was_pending and not is_rejoin and not is_fake:
                try:
                    await self._update_liveboard_message(guild)
                except Exception as exc:
                    log.debug(f"Liveboard update error on member remove: {exc}")

        # 2. Check if member left a partner verification server
        all_guild_configs = await self.config.all_guilds()
        for g_id, g_cfg in all_guild_configs.items():
            if not g_cfg.get("cross_verify_enabled", False):
                continue
            if g_cfg.get("cross_verify_guild_id") != guild.id:
                continue

            contest_guild = self.bot.get_guild(g_id)
            if not contest_guild:
                continue

            contest_member = contest_guild.get_member(member.id)
            if not contest_member:
                continue

            m_data = await self.config.member(contest_member).all()
            inv_id = m_data.get("invited_by")
            if not inv_id or m_data.get("is_fake", False):
                continue

            was_cv = m_data.get("cross_verified", False)
            if was_cv:
                cur_left = await self.config.member_from_ids(contest_guild.id, inv_id).left()
                await self.config.member_from_ids(contest_guild.id, inv_id).left.set(cur_left + 1)
                await self.config.member(contest_member).cross_verified.set(False)
                await self.config.member(contest_member).pending_cross_verify.set(True)
                await self._log_cross_verify_event(
                    contest_guild, contest_member, inv_id,
                    f"Member departed partner server (**{guild.name}**). -1 point deducted.",
                    is_positive=False
                )
                await self._update_liveboard_message(contest_guild)

    @commands.command(name="inviteprune")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def inviteprune(self, ctx: commands.Context, confirm: Optional[str] = None):
        """
        Audit and prune dead invites to fix Discord's 1,000 server invite limit.

        Usage:
          [p]inviteprune          - Dry-run audit breakdown of zero-use and dead invites.
          [p]inviteprune confirm  - Safely batch-delete dead invites with rate limiting.
        """
        guild = ctx.guild
        if not guild.me.guild_permissions.manage_guild:
            await ctx.send("Error: I need the 'Manage Server' permission to inspect and delete invites.")
            return

        async with ctx.typing():
            try:
                invites = await guild.invites()
            except Exception as exc:
                await ctx.send(f"Failed to fetch invites: {exc}")
                return

            contest_links = await self.config.guild(guild).contest_links()
            protected_codes = set(contest_links.keys())

            now = datetime.now(timezone.utc)
            zero_uses: List[discord.Invite] = []
            departed_creator: List[discord.Invite] = []
            expired_or_max: List[discord.Invite] = []
            stale_single_use: List[discord.Invite] = []
            pruneable: List[discord.Invite] = []

            for inv in invites:
                if inv.code in protected_codes:
                    continue

                is_zero = (inv.uses == 0)
                creator_left = (inv.inviter is None or guild.get_member(inv.inviter.id) is None)
                is_maxed = (inv.max_uses > 0 and inv.uses >= inv.max_uses)
                is_expired = False
                created = inv.created_at or now
                age_seconds = (now - created).total_seconds()
                if inv.max_age > 0 and age_seconds >= inv.max_age:
                    is_expired = True

                # Stale threshold: single use and older than 7 days (604,800s)
                is_stale_single = (inv.uses == 1 and age_seconds >= 604800)

                if is_zero:
                    zero_uses.append(inv)
                if creator_left:
                    departed_creator.append(inv)
                if is_maxed or is_expired:
                    expired_or_max.append(inv)
                if is_stale_single:
                    stale_single_use.append(inv)

                if is_zero or creator_left or is_maxed or is_expired or is_stale_single:
                    pruneable.append(inv)

            total_invites = len(invites)
            prune_count = len(pruneable)
            pct_used = min(100.0, (total_invites / DISCORD_INVITE_CAP) * 100.0)

            filled_blocks = int(round(pct_used / 10))
            empty_blocks = 10 - filled_blocks
            bar = "█" * filled_blocks + "░" * empty_blocks

        if confirm and confirm.lower() == "confirm":
            if prune_count == 0:
                await ctx.send("No dead or 0-use invites found to prune. Server invite capacity is healthy.")
                return

            progress_embed = discord.Embed(
                title="Pruning Dead Invites",
                description=f"Batch deleting **{prune_count}** dead invites to free slots... This takes about {int(prune_count * 0.35)} seconds.",
                color=discord.Color.gold()
            )
            status_msg = await ctx.send(embed=progress_embed)

            deleted = 0
            failed = 0

            for inv in pruneable:
                try:
                    await inv.delete(reason=f"SabbyInviteCog: Pruning dead invite to free 1,000 cap (uses: {inv.uses})")
                    deleted += 1
                    if guild.id in self.invite_cache:
                        self.invite_cache[guild.id].pop(inv.code, None)
                    await asyncio.sleep(0.35)
                except discord.HTTPException:
                    failed += 1

            new_total = len(await guild.invites())
            slots_free = DISCORD_INVITE_CAP - new_total

            success_embed = discord.Embed(
                title="Invite Prune Complete",
                description="Successfully freed server invite slots from Discord's 1,000 limit.",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            success_embed.add_field(name="Invites Deleted", value=f"**{deleted}**", inline=True)
            success_embed.add_field(name="Failed / Skipped", value=f"**{failed}**", inline=True)
            success_embed.add_field(name="Available Slots", value=f"**{slots_free} / {DISCORD_INVITE_CAP}**", inline=True)
            success_embed.add_field(name="Active Invites", value=f"**{new_total} / {DISCORD_INVITE_CAP}**", inline=True)
            success_embed.set_footer(text="Contest dedicated links were protected and preserved.")
            await status_msg.edit(embed=success_embed)
        else:
            audit_embed = discord.Embed(
                title="Server Invite Capacity Audit (Dry Run)",
                description=f"Status: **{total_invites} / {DISCORD_INVITE_CAP}** invites registered.\n`[{bar}]` **{pct_used:.1f}%** full.",
                color=discord.Color.red() if pct_used >= 95 else (discord.Color.orange() if pct_used >= 75 else discord.Color.blue()),
                timestamp=datetime.now(timezone.utc)
            )
            audit_embed.add_field(name="0-Use Invites", value=f"`{len(zero_uses)}`", inline=True)
            audit_embed.add_field(name="Departed Member Invites", value=f"`{len(departed_creator)}`", inline=True)
            audit_embed.add_field(name="Expired or Maxed Invites", value=f"`{len(expired_or_max)}`", inline=True)
            audit_embed.add_field(name="1-Use Invites (>7d old)", value=f"`{len(stale_single_use)}`", inline=True)
            audit_embed.add_field(name="Protected Contest Links", value=f"`{len(protected_codes)}`", inline=True)
            audit_embed.add_field(name="Total Pruneable Invites", value=f"**{prune_count}** slots can be recovered", inline=True)
            audit_embed.add_field(
                name="Post-Prune Forecast",
                value=f"**{total_invites - prune_count} / {DISCORD_INVITE_CAP}** invites ({DISCORD_INVITE_CAP - (total_invites - prune_count)} slots free)",
                inline=True
            )
            audit_embed.add_field(
                name="Execute Prune",
                value=f"Run `{ctx.clean_prefix}inviteprune confirm` to permanently delete the {prune_count} dead invites.",
                inline=False
            )
            await ctx.send(embed=audit_embed)

    @commands.command(name="eventlink", aliases=["contestlink", "mylink", "invitelink"])
    @commands.guild_only()
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def eventlink(self, ctx: commands.Context):
        """
        Get or generate your dedicated permanent invite link.
        Each participant receives exactly one permanent link to prevent reaching the invite cap.
        """
        code, err = await self._get_or_create_contest_link(ctx.guild, ctx.author)
        if err:
            await ctx.send(f"⚠️ {err}")
            return

        embed = discord.Embed(
            title="Your Dedicated Invite Link",
            description=f"Share your link to climb the leaderboard:\n**https://discord.gg/{code}**",
            color=discord.Color.purple(),
            timestamp=datetime.now(timezone.utc)
        )
        cv_enabled, cv_server, _ = await self._get_cross_verify_names(ctx.guild)
        if cv_enabled:
            rules_text = (
                f"• Joins count as Real (+1) once they join **{cv_server}** and get verified.\n"
                f"• Invites awaiting verification remain held in **Pending (0)**.\n"
                "• Members who leave deduct 1 point (-1).\n"
                "• Accounts under minimum age deduct 1 point (-1).\n"
                f"• Check your stats anytime with `{ctx.clean_prefix}invites`."
            )
        else:
            rules_text = (
                "• Active joins count as Real points (+1).\n"
                "• Members who leave deduct 1 point (-1).\n"
                "• Accounts under minimum age deduct 1 point (-1).\n"
                f"• Check your stats anytime with `{ctx.clean_prefix}invites`."
            )
        embed.add_field(
            name="Rules",
            value=rules_text,
            inline=False
        )
        embed.set_footer(text=f"Dedicated permanent link for {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @commands.command(name="anticheat", aliases=["howitworks", "inviterules", "inviteanticheat"])
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def anticheat(self, ctx: commands.Context):
        """
        Explain the invite anti-cheat engine, verification rules, and scoring formula.
        """
        min_age_days = await self.config.guild(ctx.guild).min_age_days()
        require_onboarding = await self.config.guild(ctx.guild).require_onboarding()

        embed = discord.Embed(
            title="Anti-Cheat and Verification System",
            description=(
                "Our invite tracking engine enforces automated fraud detection and real-time ledger accounting "
                "to ensure fair and transparent rankings for all participants."
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="Scoring Formula",
            value=(
                "**Net Points = Real - Left - Fake + Bonus**\n"
                "• **Real (+1)** : A verified, legitimate member who joins and remains in the server.\n"
                "• **Left (-1)** : A previously invited member who departs from the server.\n"
                "• **Fake (-1)** : An account detected as an alt, bot, or fraud attempt.\n"
                "• **Bonus (+/-)** : Discretionary points added or deducted by administrators."
            ),
            inline=False
        )
        embed.add_field(
            name="Account Age Threshold",
            value=(
                f"Accounts created fewer than **{min_age_days} days** before joining are flagged as Fake. "
                "The inviter receives a -1 point penalty to deter farming alts or burner accounts."
            ),
            inline=False
        )

        onboarding_status = "Enabled" if require_onboarding else "Disabled"
        onboarding_detail = (
            "New joins must pass server onboarding and rule screening before points are released. "
            "If an unverified account leaves before completing setup, points are neither awarded nor deducted, "
            "preventing ghost-join abuse."
            if require_onboarding
            else "Points are awarded immediately upon joining."
        )
        embed.add_field(
            name=f"Onboarding and Rule Screening ({onboarding_status})",
            value=onboarding_detail,
            inline=False
        )
        cross_verify_enabled = await self.config.guild(ctx.guild).cross_verify_enabled()
        if cross_verify_enabled:
            server_id = await self.config.guild(ctx.guild).cross_verify_guild_id()
            role_id = await self.config.guild(ctx.guild).cross_verify_role_id()
            t_guild = self.bot.get_guild(server_id) if server_id else None
            t_role = t_guild.get_role(role_id) if (t_guild and role_id) else None
            s_name = t_guild.name if t_guild else f"Server {server_id}"
            r_name = f"@{t_role.name}" if t_role else "member"
            embed.add_field(
                name="Partner Server Verification (Active)",
                value=(
                    f"To count as a verified Real invite, new members must also join **{s_name}** "
                    f"and acquire the **{r_name}** role. Points remain held in escrow until all requirements are met. "
                    "If the invited member leaves the partner server or loses the required role, 1 point is deducted."
                ),
                inline=False
            )
        embed.add_field(
            name="Fraud Prevention and Abuse Controls",
            value=(
                "• **Self-Invites** : Joining the server with your own link yields 0 points.\n"
                "• **Re-joins** : Returning members who previously joined award 0 additional points.\n"
                "• **Vanity Links** : Server vanity URL joins belong to the community and cannot be claimed.\n"
                "• **Bot Exclusions** : Automated bot accounts joining the server are completely ignored."
            ),
            inline=False
        )
        embed.add_field(
            name="Departure Deductions",
            value=(
                "When an invited member leaves, 1 point is deducted from the original inviter. "
                "However, if that member was already flagged as a Fake account on arrival, "
                "no secondary departure penalty is applied."
            ),
            inline=False
        )
        embed.add_field(
            name="Tie-Breaker Hierarchy",
            value=(
                "When two or more participants share the same Net Score, positions are determined strictly by:\n"
                "1. Highest Real Invites (most legitimate members brought)\n"
                "2. Fewest Departures (highest member retention rate)\n"
                "3. Fewest Fake Flags (cleanest invite record)"
            ),
            inline=False
        )
        embed.add_field(
            name="How to Participate",
            value=(
                f"Each participant receives one permanent, dedicated invite link to avoid hitting Discord limits. "
                f"Run `{ctx.clean_prefix}eventlink` to generate your link, or view your live standing anytime with `{ctx.clean_prefix}invites`."
            ),
            inline=False
        )
        embed.set_footer(text=f"{ctx.guild.name} | Formula: Real - Left - Fake + Bonus")
        await ctx.send(embed=embed)

    @commands.command(name="inviteleaderboard", aliases=["invlb", "inviteslb", "invboard"])
    @commands.guild_only()
    @commands.cooldown(1, 20, commands.BucketType.guild)
    async def inviteleaderboard(self, ctx: commands.Context):
        """
        Display the real-time invite leaderboard with Top 3 podium banner.
        Ranks participants by Net Score: Real - Left - Fake + Bonus.
        """
        scores = await self._get_ranked_scores(ctx.guild)
        embed = await self._build_leaderboard_embed(ctx.guild, author_id=ctx.author.id, is_live=False, scores=scores)
        podium_bytes = await self._generate_podium_image(ctx.guild, scores[:3])
        file = None
        if podium_bytes:
            embed.set_image(url="attachment://podium.png")
            file = discord.File(io.BytesIO(podium_bytes), filename="podium.png")
        view = LeaderboardLiveView(self)
        if file:
            await ctx.send(embed=embed, view=view, file=file)
        else:
            await ctx.send(embed=embed, view=view)

    @commands.command(name="invites", aliases=["myinvites", "invitestats"])
    @commands.guild_only()
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def invites(self, ctx: commands.Context, *, member: Optional[discord.Member] = None):
        """
        Check personal or another member's invite stats, score breakdown, and rank.
        """
        target = member or ctx.author
        guild = ctx.guild

        data = await self.config.member(target).all()
        real = data.get("real", 0)
        left = data.get("left", 0)
        fake = data.get("fake", 0)
        bonus = data.get("bonus", 0)
        code = data.get("contest_code")
        disqualified = data.get("disqualified", False)
        net = real - left - fake + bonus

        all_members = await self.config.all_members(guild)
        scores = []
        for mid, d in all_members.items():
            if not d.get("disqualified", False):
                n = d.get("real", 0) - d.get("left", 0) - d.get("fake", 0) + d.get("bonus", 0)
                scores.append((mid, n))
        scores.sort(key=lambda x: x[1], reverse=True)

        rank_str = "Unranked"
        for idx, (mid, sc) in enumerate(scores, start=1):
            if mid == target.id:
                rank_str = f"#{idx} of {len(scores)}"
                break

        pts_label = "point" if abs(net) == 1 else "points"
        embed = discord.Embed(
            title=f"Invite Stats : {target.display_name}",
            color=discord.Color.red() if disqualified else discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        if disqualified:
            embed.add_field(name="Status", value="⚠️ **Disqualified by Administrator**", inline=False)

        embed.add_field(name="Net Score", value=f"**{net} {pts_label}**", inline=True)
        embed.add_field(name="Rank", value=f"**{rank_str}**", inline=True)

        parts = [f"✅ **Real:** **{real}**", f"🚪 **Left:** **{left}**", f"⚠️ **Fake:** **{fake}**"]
        if bonus != 0:
            parts.append(f"⭐ **Bonus:** **{bonus:+d}**")
        pending_count = sum(
            1 for mid, d in all_members.items()
            if d.get("invited_by") == target.id and (d.get("pending_onboarding", False) or d.get("pending_cross_verify", False))
        )
        if pending_count > 0:
            parts.append(f"⏳ **Pending:** **{pending_count}**")
        rejoin_count = sum(
            1 for mid, d in all_members.items()
            if d.get("invited_by") == target.id and d.get("is_rejoin", False)
        )
        if rejoin_count > 0:
            parts.append(f"🔄 **Re-join:** **{rejoin_count}**")
        embed.add_field(name="Score Breakdown", value=" • ".join(parts), inline=False)
        cv_enabled, cv_server, _ = await self._get_cross_verify_names(guild)
        if cv_enabled:
            embed.add_field(
                name="Verification Requirement",
                value=f"Invites count as **Real (+1)** only after joining **{cv_server}** and getting verified. Unverified invites are held in **Pending (0)**.",
                inline=False
            )

        if code:
            embed.add_field(name="Dedicated Invite Link", value=f"https://discord.gg/{code}", inline=False)
        else:
            embed.add_field(
                name="Dedicated Invite Link",
                value=f"None yet. Run `{ctx.clean_prefix}eventlink` to generate one.",
                inline=False
            )

        await ctx.send(embed=embed)

    @commands.command(name="inviteinfo", aliases=["invited", "invitelist", "whoinvited"])
    @commands.guild_only()
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def inviteinfo(
        self,
        ctx: commands.Context,
        *,
        member: Optional[Union[discord.Member, discord.User, int]] = None
    ):
        """
        Inspect all members invited by a user, including IDs, codes, join dates, and statuses.
        """
        target = member or ctx.author
        if isinstance(target, int):
            resolved = ctx.guild.get_member(target) or self.bot.get_user(target)
            if not resolved:
                try:
                    resolved = await self.bot.fetch_user(target)
                except Exception:
                    resolved = None
            if resolved:
                target = resolved

        embed, total_pages = await self._build_invite_info_embed(
            ctx.guild,
            target,
            prefix=ctx.clean_prefix,
            page=1,
            per_page=10
        )
        if total_pages > 1:
            view = InvitedPaginationView(
                cog=self,
                guild=ctx.guild,
                target=target,
                author_id=ctx.author.id,
                prefix=ctx.clean_prefix,
                include_privacy_hint=False,
                page=1,
                total_pages=total_pages,
                per_page=10,
            )
            msg = await ctx.send(embed=embed, view=view)
            view.message = msg
        else:
            await ctx.send(embed=embed)

    async def cog_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"⏳ Cooldown active. You can run `{ctx.command.name}` again in **{error.retry_after:.1f}s**.",
                delete_after=10
            )
            return
        raise error

    @commands.command(name="removeliveboard", aliases=["removeboard", "clearliveboard", "deleteliveboard"])
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def removeliveboard(self, ctx: commands.Context):
        """
        Delete the live leaderboard message and disable auto-refreshing.
        """
        await self.config.guild(ctx.guild).liveboard_enabled.set(False)
        deleted_count = await self._delete_all_liveboard_messages(ctx.guild)
        await self.config.guild(ctx.guild).live_channel_id.set(None)
        await self.config.guild(ctx.guild).live_message_id.set(None)

        if deleted_count > 0:
            await ctx.send(f"Live leaderboard removed! Deleted **{deleted_count}** message(s) and disabled auto-refresh.")
        else:
            await ctx.send("Live leaderboard auto-refresh disabled (no active leaderboard messages found).")

    @commands.group(name="sabbyinviteset", aliases=["invitesettings", "contestset"])
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def sabbyinviteset(self, ctx: commands.Context):
        """Configure invite tracking, anti-cheat, logging, live leaderboard, and webhooks."""
        pass

    @sabbyinviteset.command(name="removeboard", aliases=["remove", "delete", "clear", "off"])
    async def inviteset_removeboard(self, ctx: commands.Context):
        """Delete the live leaderboard message and disable auto-refreshing."""
        await self.removeliveboard(ctx)

    @sabbyinviteset.command(name="liveboard", aliases=["liveleaderboard", "livechannel"])
    async def inviteset_liveboard(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """
        Deploy or remove a persistent auto-updating live leaderboard in a channel.
        Updates every 1 minute and includes interactive Refresh and Get Link buttons.
        """
        if not channel:
            await self.config.guild(ctx.guild).liveboard_enabled.set(False)
            deleted_count = await self._delete_all_liveboard_messages(ctx.guild)
            await self.config.guild(ctx.guild).live_channel_id.set(None)
            await self.config.guild(ctx.guild).live_message_id.set(None)
            msg_text = f"Live message deleted ({deleted_count} removed) and auto-updates disabled." if deleted_count > 0 else "Live leaderboard auto-updates disabled."
            await ctx.send(f"Live leaderboard removed! {msg_text}")
            return

        perms = channel.permissions_for(ctx.guild.me)
        if not perms.send_messages or not perms.embed_links:
            await ctx.send(f"Error: I do not have permission to send embeds in {channel.mention}.")
            return

        old_channel_id = await self.config.guild(ctx.guild).live_channel_id()
        deleted_count = await self._delete_all_liveboard_messages(ctx.guild)

        webhook_url = await self.config.guild(ctx.guild).webhook_url()
        if webhook_url:
            try:
                wh_candidate = discord.Webhook.from_url(webhook_url, client=self.bot)
                wh_obj = await wh_candidate.fetch()
                if wh_obj.channel_id != channel.id:
                    moved = False
                    try:
                        await wh_obj.edit(channel=channel, reason="SabbyInvite liveboard moved")
                        moved = True
                    except Exception as exc:
                        log.debug(f"Webhook edit channel failed: {exc}")
                    if not moved and channel.permissions_for(ctx.guild.me).manage_webhooks:
                        name = await self.config.guild(ctx.guild).webhook_name() or "Invite Leaderboard"
                        avatar = await self.config.guild(ctx.guild).webhook_avatar()
                        avatar_bytes = None
                        if avatar:
                            try:
                                async with aiohttp.ClientSession() as s:
                                    async with s.get(avatar) as r:
                                        if r.status == 200:
                                            avatar_bytes = await r.read()
                            except Exception:
                                pass
                        new_wh = await channel.create_webhook(
                            name=name,
                            avatar=avatar_bytes,
                            reason="SabbyInvite liveboard webhook"
                        )
                        await self.config.guild(ctx.guild).webhook_url.set(new_wh.url)
                        try:
                            await wh_obj.delete(reason="SabbyInvite liveboard moved to new channel")
                        except Exception:
                            pass
            except Exception as exc:
                log.debug(f"Webhook check in inviteset_liveboard failed: {exc}")

        await self.config.guild(ctx.guild).liveboard_enabled.set(True)
        await self.config.guild(ctx.guild).live_channel_id.set(channel.id)
        await self.config.guild(ctx.guild).live_message_id.set(None)
        msg = await self._update_liveboard_message(ctx.guild, force_render=True)
        if msg:
            await ctx.send(f"Live leaderboard deployed in {channel.mention}! It will automatically refresh every 1 minute.")
        else:
            await ctx.send(f"Failed to post live leaderboard in {channel.mention}.")

    @sabbyinviteset.command(name="webhook")
    async def inviteset_webhook(self, ctx: commands.Context, target: Optional[str] = None):
        """
        Configure a webhook for the live leaderboard to allow custom name and avatar.
        Pass 'auto' or #channel to auto-create a webhook in that channel,
        pass a Discord webhook URL to use an existing one,
        or pass 'off' to disable webhook mode and use standard bot messages.
        """
        if not target:
            url = await self.config.guild(ctx.guild).webhook_url()
            name = await self.config.guild(ctx.guild).webhook_name() or "Invite Leaderboard"
            avatar = await self.config.guild(ctx.guild).webhook_avatar() or "Default"
            status = f"Enabled (`{url[:35]}...`)" if url else "Disabled (Standard Bot Messages)"
            embed = discord.Embed(
                title="Liveboard Webhook Configuration",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Webhook Status", value=status, inline=False)
            embed.add_field(name="Display Name", value=name, inline=True)
            embed.add_field(name="Avatar URL", value=avatar, inline=True)
            embed.set_footer(text="Use [p]sabbyinviteset webhook <auto|#channel|url|off> to configure")
            await ctx.send(embed=embed)
            return

        target_clean = target.strip()

        if target_clean.lower() in ["off", "disable", "none", "remove", "clear"]:
            await self.config.guild(ctx.guild).webhook_url.set(None)
            await ctx.send("Webhook mode disabled. The liveboard will now update using standard bot messages.")
            await self._update_liveboard_message(ctx.guild, force_render=True)
            return

        name = await self.config.guild(ctx.guild).webhook_name() or "Invite Leaderboard"

        if target_clean.lower() in ["auto", "create"]:
            channel_id = await self.config.guild(ctx.guild).live_channel_id()
            if not channel_id:
                await ctx.send("No liveboard channel configured yet. Run `[p]sabbyinviteset liveboard #channel` first or mention a channel.")
                return
            channel = ctx.guild.get_channel(channel_id)
            if not channel:
                await ctx.send("Configured liveboard channel not found.")
                return
            try:
                webhook = await channel.create_webhook(name=name, reason="SabbyInvite liveboard webhook")
                await self.config.guild(ctx.guild).webhook_url.set(webhook.url)
                await ctx.send(f"Auto-created webhook in {channel.mention}!\nLiveboard will now post with custom name and avatar.")
                await self._update_liveboard_message(ctx.guild, force_render=True)
                return
            except Exception as exc:
                await ctx.send(f"Failed to create webhook: {exc}")
                return

        channel_converter = commands.TextChannelConverter()
        try:
            target_chan = await channel_converter.convert(ctx, target_clean)
            try:
                webhook = await target_chan.create_webhook(name=name, reason="SabbyInvite liveboard webhook")
                await self.config.guild(ctx.guild).webhook_url.set(webhook.url)
                await self.config.guild(ctx.guild).live_channel_id.set(target_chan.id)
                await ctx.send(f"Created webhook in {target_chan.mention} and saved as liveboard!")
                await self._update_liveboard_message(ctx.guild, force_render=True)
                return
            except Exception as exc:
                await ctx.send(f"Failed to create webhook in {target_chan.mention}: {exc}")
                return
        except commands.BadArgument:
            pass

        if target_clean.startswith("https://discord.com/api/webhooks/") or target_clean.startswith("https://discordapp.com/api/webhooks/"):
            try:
                webhook = discord.Webhook.from_url(target_clean, client=self.bot)
                await self.config.guild(ctx.guild).webhook_url.set(target_clean)
                await ctx.send("Liveboard webhook URL updated successfully! Testing update...")
                await self._update_liveboard_message(ctx.guild, force_render=True)
            except Exception as exc:
                await ctx.send(f"Invalid webhook URL: {exc}")
        else:
            await ctx.send("Invalid parameter. Provide 'auto', a #channel mention, a valid Discord webhook URL, or 'off'.")

    @sabbyinviteset.command(name="webhookname")
    async def inviteset_webhookname(self, ctx: commands.Context, *, name: str):
        """Set the custom display name for the live leaderboard webhook."""
        clean_name = name.strip()
        if not clean_name:
            await ctx.send("Please specify a valid name.")
            return
        await self.config.guild(ctx.guild).webhook_name.set(clean_name)
        await ctx.send(f"Liveboard webhook display name set to: **{clean_name}**.")
        await self._update_liveboard_message(ctx.guild, force_render=True)

    @sabbyinviteset.command(name="webhookavatar")
    async def inviteset_webhookavatar(self, ctx: commands.Context, url: Optional[str] = None):
        """Set or clear the custom avatar image URL for the live leaderboard webhook."""
        if url and url.lower() in ["clear", "none", "off", "reset", "default"]:
            url = None
        await self.config.guild(ctx.guild).webhook_avatar.set(url)
        if url:
            await ctx.send(f"Liveboard webhook avatar updated to: {url}")
        else:
            await ctx.send("Liveboard webhook avatar reset to default.")
        await self._update_liveboard_message(ctx.guild, force_render=True)

    @sabbyinviteset.command(name="logchannel")
    async def inviteset_logchannel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Set or clear the channel for invite join/leave and anti-cheat audit logs."""
        if channel:
            await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
            await ctx.send(f"Invite tracking log channel set to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel_id.set(None)
            await ctx.send("Invite tracking log channel has been disabled.")

    @sabbyinviteset.command(name="channel")
    async def inviteset_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Set the landing channel where dedicated contest invite links point to."""
        if channel:
            await self.config.guild(ctx.guild).event_channel_id.set(channel.id)
            await ctx.send(f"Contest invite landing channel set to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).event_channel_id.set(None)
            await ctx.send("Contest invite landing channel reset to server default.")

    @sabbyinviteset.command(name="minage")
    async def inviteset_minage(self, ctx: commands.Context, days: int):
        """Set minimum account age in days to prevent alt account fraud (default: 7)."""
        if days < 0 or days > 365:
            await ctx.send("Please specify an age between 0 and 365 days.")
            return
        await self.config.guild(ctx.guild).min_age_days.set(days)
        await ctx.send(f"Anti-alt threshold updated: accounts younger than **{days} days** will be flagged as Fake (-1 penalty).")

    @sabbyinviteset.command(name="requireonboarding", aliases=["onboarding", "screening"])
    async def inviteset_requireonboarding(self, ctx: commands.Context, enabled: Optional[bool] = None):
        """
        Toggle whether new members must complete server onboarding / rule screening
        before their inviter is credited with a real point.
        """
        if enabled is None:
            current = await self.config.guild(ctx.guild).require_onboarding()
            status = "enabled" if current else "disabled"
            await ctx.send(f"Onboarding gating is currently **{status}**.\nUse `{ctx.clean_prefix}sabbyinviteset requireonboarding <true/false>` to toggle.")
            return

        await self.config.guild(ctx.guild).require_onboarding.set(enabled)
        state = "enabled (points held until member finishes onboarding/screening)" if enabled else "disabled (points awarded immediately on join)"
        await ctx.send(f"Onboarding gating has been **{state}**.")

    @sabbyinviteset.command(name="prizes")
    async def inviteset_prizes(self, ctx: commands.Context, first: str, second: str, third: str):
        """Configure the cash prize descriptions for 1st, 2nd, and 3rd place."""
        await self.config.guild(ctx.guild).prizes.set([first, second, third])
        await ctx.send(f"Prize tiers updated: 🥇 1st: **{first}** | 🥈 2nd: **{second}** | 🥉 3rd: **{third}**.")

    @sabbyinviteset.command(name="bonus")
    async def inviteset_bonus(self, ctx: commands.Context, member: discord.Member, amount: int, *, reason: Optional[str] = None):
        """Add or deduct bonus points for a contestant."""
        current = await self.config.member(member).bonus()
        new_val = current + amount
        await self.config.member(member).bonus.set(new_val)
        reason_text = f" Reason: {reason}" if reason else ""
        await ctx.send(f"Adjusted bonus points for {member.mention}: `{amount:+d}` (New bonus total: `{new_val}`).{reason_text}")

    @sabbyinviteset.command(name="disqualify")
    async def inviteset_disqualify(self, ctx: commands.Context, member: discord.Member):
        """Toggle disqualification status for a contestant."""
        current = await self.config.member(member).disqualified()
        new_state = not current
        await self.config.member(member).disqualified.set(new_state)
        status_msg = "disqualified and hidden from the leaderboard" if new_state else "reinstated"
        await ctx.send(f"{member.mention} has been {status_msg}.")

    @sabbyinviteset.command(name="reset")
    async def inviteset_reset(self, ctx: commands.Context, confirm: Optional[str] = None):
        """Reset all contest scores and member invite data for a fresh contest."""
        if confirm != "confirm":
            await ctx.send(f"⚠️ Are you sure? This will wipe all contest scores in this guild.\nRun `{ctx.clean_prefix}sabbyinviteset reset confirm` to proceed.")
            return

        all_members = await self.config.all_members(ctx.guild)
        for mid in all_members.keys():
            await self.config.member_from_ids(ctx.guild.id, mid).clear()

        await ctx.send("All invite contest scores and member tracking data have been reset.")

    @sabbyinviteset.command(name="sync")
    async def inviteset_sync(self, ctx: commands.Context):
        """Manually re-synchronize the in-memory invite cache with Discord's API."""
        count = await self._cache_guild_invites(ctx.guild)
        slots_free = DISCORD_INVITE_CAP - count
        await ctx.send(f"Cache synchronized. Tracked **{count}** active invites. **{slots_free}** slots remaining before 1,000 cap.")

    @sabbyinviteset.command(name="dbstatus", aliases=["logdb", "inoutdb"])
    async def inviteset_dbstatus(self, ctx: commands.Context, user: Optional[Union[discord.Member, discord.User]] = None):
        """
        Inspect the status of the historical join/leave SQLite database from in-out logs.
        Optionally pass a user to check their historical records.
        """
        db_path = Path("/home/sablinova/.local/share/Red-DiscordBot/data/Sablinova/cogs/SabbyInviteCog/in_out_history.db")
        if not db_path.exists():
            await ctx.send("Historical database not initialized yet.")
            return

        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM members")
            total_members = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM events")
            total_events = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM members WHERE total_joins > 1")
            rejoiners = cur.fetchone()[0]
            cur.execute("SELECT val FROM checkpoints WHERE key = 'total_messages'")
            row_tot = cur.fetchone()
            tot_scanned = int(row_tot[0]) if row_tot and row_tot[0] else 0
            cur.execute("SELECT val FROM checkpoints WHERE key = 'is_complete'")
            row_comp = cur.fetchone()
            is_complete = bool(row_comp and row_comp[0] == "1")

            embed = discord.Embed(
                title="Historical In-Out Log Database Status",
                color=discord.Color.green() if is_complete else discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="Scraper Status", value="✅ Complete" if is_complete else "⏳ In Progress / Scanned", inline=True)
            embed.add_field(name="Messages Scanned", value=f"**{tot_scanned:,}**", inline=True)
            embed.add_field(name="Events Cataloged", value=f"**{total_events:,}**", inline=True)
            embed.add_field(name="Unique Members", value=f"**{total_members:,}**", inline=True)
            embed.add_field(name="Re-joiners Detected", value=f"**{rejoiners:,}**", inline=True)

            if user:
                cur.execute("SELECT first_joined_at, last_joined_at, last_left_at, total_joins, total_leaves FROM members WHERE user_id = ?", (user.id,))
                u_row = cur.fetchone()
                if u_row:
                    fj = f"<t:{int(u_row[0])}:f>" if u_row[0] else "N/A"
                    lj = f"<t:{int(u_row[1])}:f>" if u_row[1] else "N/A"
                    ll = f"<t:{int(u_row[2])}:f>" if u_row[2] else "N/A"
                    u_val = f"• **Total Joins:** {u_row[3]}\n• **Total Leaves:** {u_row[4]}\n• **First Joined:** {fj}\n• **Last Joined:** {lj}\n• **Last Left:** {ll}"
                    embed.add_field(name=f"Lookup: {user.display_name} (`{user.id}`)", value=u_val, inline=False)
                else:
                    embed.add_field(name=f"Lookup: {user.display_name} (`{user.id}`)", value="Not found in historical records.", inline=False)

            conn.close()
            embed.set_footer(text="Anti-cheat re-join detection powered by in_out_history.db")
            await ctx.send(embed=embed)
        except Exception as exc:
            await ctx.send(f"Error querying historical database: {exc}")

    @sabbyinviteset.group(name="crossverify", aliases=["verify", "requirejoin"])
    async def inviteset_crossverify(self, ctx: commands.Context):
        """Configure partner server join and role verification requirements."""
        pass

    @inviteset_crossverify.command(name="set")
    async def crossverify_set(self, ctx: commands.Context, server_id: int, role_id: Optional[int] = None):
        """
        Configure required partner server and role for invite verification.
        Example: [p]sabbyinviteset crossverify set 1265025550485950634 126502888999888777
        """
        target_guild = self.bot.get_guild(server_id)
        if not target_guild:
            await ctx.send(f"⚠️ Bot is not in a server with ID `{server_id}`. Please invite the bot to that server first.")
            return

        target_role = None
        if role_id:
            target_role = target_guild.get_role(role_id)
            if not target_role:
                await ctx.send(f"⚠️ Role ID `{role_id}` was not found in server **{target_guild.name}**. Please verify the role ID.")
                return

        await self.config.guild(ctx.guild).cross_verify_guild_id.set(server_id)
        await self.config.guild(ctx.guild).cross_verify_role_id.set(role_id)
        await self.config.guild(ctx.guild).cross_verify_enabled.set(True)

        role_label = f"**@{target_role.name}** (`{target_role.id}`)" if target_role else "None (Join only)"
        embed = discord.Embed(
            title="Cross-Server Verification Enabled",
            description=(
                f"Invited members must now join the partner server and obtain the required role to be counted as **Real**.\n\n"
                f"• **Partner Server:** **{target_guild.name}** (`{target_guild.id}`)\n"
                f"• **Required Role:** {role_label}\n"
                f"• **Departure / Role Loss Policy:** -1 point deducted from inviter\n"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        await ctx.send(embed=embed)

    @inviteset_crossverify.command(name="disable", aliases=["off"])
    async def crossverify_disable(self, ctx: commands.Context):
        """Disable cross-server verification requirement."""
        await self.config.guild(ctx.guild).cross_verify_enabled.set(False)
        await ctx.send("Cross-server verification requirement disabled.")

    @inviteset_crossverify.command(name="status", aliases=["info"])
    async def crossverify_status(self, ctx: commands.Context):
        """Check current cross-server verification settings."""
        enabled = await self.config.guild(ctx.guild).cross_verify_enabled()
        server_id = await self.config.guild(ctx.guild).cross_verify_guild_id()
        role_id = await self.config.guild(ctx.guild).cross_verify_role_id()

        target_guild = self.bot.get_guild(server_id) if server_id else None
        target_role = target_guild.get_role(role_id) if (target_guild and role_id) else None

        embed = discord.Embed(
            title="Cross-Server Verification Settings",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Status", value="✅ **Enabled**" if enabled else "❌ **Disabled**", inline=True)
        embed.add_field(
            name="Partner Server",
            value=f"**{target_guild.name}** (`{server_id}`)" if target_guild else (f"`{server_id}`" if server_id else "None"),
            inline=True
        )
        embed.add_field(
            name="Required Role",
            value=f"**@{target_role.name}** (`{role_id}`)" if target_role else (f"`{role_id}`" if role_id else "None (Join only)"),
            inline=True
        )
        await ctx.send(embed=embed)

    @inviteset_crossverify.command(name="sync", aliases=["audit"])
    async def crossverify_sync(self, ctx: commands.Context):
        """
        Scan all pending members in this server and verify any who have joined the partner server with the required role.
        """
        server_id = await self.config.guild(ctx.guild).cross_verify_guild_id()
        role_id = await self.config.guild(ctx.guild).cross_verify_role_id()
        if not server_id:
            await ctx.send(f"⚠️ No partner server configured. Run `{ctx.clean_prefix}sabbyinviteset crossverify set` first.")
            return

        target_guild = self.bot.get_guild(server_id)
        if not target_guild:
            await ctx.send(f"⚠️ Bot is not in partner server `{server_id}`.")
            return

        all_members = await self.config.all_members(ctx.guild)
        newly_verified = 0
        reverted_to_pending = 0
        still_pending = 0

        async with ctx.typing():
            for mid_str, d in all_members.items():
                mid = int(mid_str)
                if d.get("is_fake", False):
                    continue
                inviter_id = d.get("invited_by")
                if not inviter_id:
                    continue

                m_obj = ctx.guild.get_member(mid)
                if not m_obj:
                    continue

                is_cv = await self._is_cross_server_verified(mid, server_id, role_id)
                pending_cv = d.get("pending_cross_verify", False)
                pending_ob = d.get("pending_onboarding", False)
                was_cv = d.get("cross_verified", False)

                if is_cv:
                    if pending_cv or pending_ob or not was_cv:
                        await self.config.member(m_obj).pending_cross_verify.set(False)
                        await self.config.member(m_obj).pending_onboarding.set(False)
                        await self.config.member(m_obj).cross_verified.set(True)
                        cur_real = await self.config.member_from_ids(ctx.guild.id, inviter_id).real()
                        await self.config.member_from_ids(ctx.guild.id, inviter_id).real.set(cur_real + 1)
                        newly_verified += 1
                else:
                    if was_cv or not pending_cv:
                        await self.config.member(m_obj).pending_cross_verify.set(True)
                        await self.config.member(m_obj).cross_verified.set(False)
                        cur_real = await self.config.member_from_ids(ctx.guild.id, inviter_id).real()
                        if cur_real > 0:
                            await self.config.member_from_ids(ctx.guild.id, inviter_id).real.set(cur_real - 1)
                        reverted_to_pending += 1
                    else:
                        still_pending += 1

            if newly_verified > 0 or reverted_to_pending > 0:
                await self._update_liveboard_message(ctx.guild)

        await ctx.send(
            f"Cross-server sync completed!\n"
            f"• **Newly Verified:** **{newly_verified}**\n"
            f"• **Moved to Pending:** **{reverted_to_pending}**\n"
            f"• **Still Pending:** **{still_pending}**"
        )
