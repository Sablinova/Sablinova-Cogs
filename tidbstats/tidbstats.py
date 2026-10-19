import datetime
import logging
import random
import urllib.parse
from typing import Any, Dict, Optional, Tuple

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands

log = logging.getLogger("red.sablinova.tidbstats")

USER_AGENT = "TheIntroDB-Discord/4.0 (Red-DiscordBot; +https://github.com/Sablinova/Sablinova-Cogs)"


def format_ms(ms: Optional[int]) -> str:
    """Format milliseconds into MM:SS or HH:MM:SS."""
    if ms is None or ms < 0:
        return "00:00"
    total_seconds = int(ms / 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def make_progress_bar(ratio: float, length: int = 10) -> str:
    """ASCII progress bar for ratio metrics."""
    clamped = max(0.0, min(1.0, ratio))
    filled = int(round(clamped * length))
    bar = "█" * filled + "░" * (length - filled)
    return f"[`{bar}`] {clamped * 100:.1f}%"


class SearchMediaModal(discord.ui.Modal, title="Search TheIntroDB"):
    title_input = discord.ui.TextInput(
        label="Show or Movie Title",
        placeholder="e.g. The Simpsons, Breaking Bad, Inception",
        min_length=1,
        max_length=100,
        required=True,
    )
    season_input = discord.ui.TextInput(
        label="Season Number (TV only)",
        placeholder="e.g. 1",
        default="1",
        min_length=1,
        max_length=4,
        required=False,
    )
    episode_input = discord.ui.TextInput(
        label="Episode Number (TV only)",
        placeholder="e.g. 1",
        default="1",
        min_length=1,
        max_length=4,
        required=False,
    )

    def __init__(self, cog: "TidbStats"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        title = self.title_input.value.strip()
        s_val = self.season_input.value.strip() or "1"
        e_val = self.episode_input.value.strip() or "1"
        try:
            season = int(s_val)
            episode = int(e_val)
        except ValueError:
            await interaction.followup.send(
                "Season and episode numbers must be positive integers.",
                ephemeral=True,
            )
            return

        embed = await self.cog.lookup_media(interaction.guild, title, season, episode)
        await interaction.followup.send(embed=embed, ephemeral=True)


class TidbStatsView(discord.ui.View):
    def __init__(self, cog: "TidbStats"):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(
            discord.ui.Button(
                label="TheIntroDB",
                style=discord.ButtonStyle.link,
                url="https://theintrodb.org",
                emoji="🌐",
            )
        )

    @discord.ui.button(
        label="Search Media",
        style=discord.ButtonStyle.primary,
        emoji="🔍",
        custom_id="tidbstats:search",
    )
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SearchMediaModal(self.cog))

    @discord.ui.button(
        label="Random Intro",
        style=discord.ButtonStyle.secondary,
        emoji="🎲",
        custom_id="tidbstats:random",
    )
    async def random_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        embed = await self.cog.get_random_intro_embed(interaction.guild)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Setup Guide",
        style=discord.ButtonStyle.secondary,
        emoji="📖",
        custom_id="tidbstats:guide",
    )
    async def guide_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.cog.get_setup_guide_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TidbStats(commands.Cog):
    """Live statistics and timestamp lookups from TheIntroDB v4 API."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8473829201, force_registration=True)
        self.config.register_guild(
            channel_id=None,
            message_id=None,
            enabled=False,
            url="https://api.theintrodb.org/v4/stats",
            logo="https://cdn.discordapp.com/icons/1457550295742812307/a0259221faeb0133e43760697573577a.webp?format=webp&width=60&height=60",
            tmdb_api_key="a416278866ffc1357efc0369047cc736",
            interval_hours=1,
            last_snapshot={},
        )
        self.session: Optional[aiohttp.ClientSession] = None
        self._media_cache: Dict[str, Dict[str, Any]] = {}
        self._last_stats_cache: Dict[str, Any] = {}

    async def cog_load(self):
        self.session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        self.bot.add_view(TidbStatsView(self))
        self.update_stats.start()

    def cog_unload(self):
        self.update_stats.cancel()
        if self.session and not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def get_guild_settings(self, guild: Optional[discord.Guild]) -> Dict[str, Any]:
        """Fetch guild settings with fallback to defaults, avoiding nested_update issues."""
        defaults = {
            "channel_id": None,
            "message_id": None,
            "enabled": False,
            "url": "https://api.theintrodb.org/v4/stats",
            "logo": "https://cdn.discordapp.com/icons/1457550295742812307/a0259221faeb0133e43760697573577a.webp?format=webp&width=60&height=60",
            "tmdb_api_key": "a416278866ffc1357efc0369047cc736",
            "interval_hours": 1,
            "last_snapshot": {},
        }
        if not guild:
            return defaults

        g = self.config.guild(guild)
        return {
            "channel_id": await g.channel_id(),
            "message_id": await g.message_id(),
            "enabled": await g.enabled(),
            "url": await g.url(),
            "logo": await g.logo(),
            "tmdb_api_key": await g.tmdb_api_key(),
            "interval_hours": await g.interval_hours(),
            "last_snapshot": (await g.last_snapshot()) or {},
        }

    async def fetch_json(self, url: str) -> Tuple[Optional[Dict[str, Any]], int]:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json(), 200
                return None, resp.status
        except Exception as e:
            log.warning("HTTP request to %s failed: %s", url, e)
            return None, 0

    async def resolve_tvdb_media(self, tvdb_id: int, media_type: str, api_key: str) -> Dict[str, Any]:
        """Resolve TVDB ID to human-readable title and poster art using TMDB external find."""
        cache_key = f"tvdb:{tvdb_id}"
        if cache_key in self._media_cache:
            return self._media_cache[cache_key]

        data = {"title": f"TVDB ID {tvdb_id}", "poster_url": None, "tmdb_id": None}
        if not api_key:
            return data

        url = f"https://api.themoviedb.org/3/find/{tvdb_id}?api_key={api_key}&external_source=tvdb_id"
        result, status = await self.fetch_json(url)
        if status == 200 and result:
            items = result.get("tv_results" if media_type == "tv" else "movie_results") or result.get("tv_results") or result.get("movie_results") or []
            if items:
                match = items[0]
                title = match.get("name") or match.get("title") or f"TVDB ID {tvdb_id}"
                poster_path = match.get("poster_path")
                poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None
                data = {"title": title, "poster_url": poster_url, "tmdb_id": match.get("id")}
                self._media_cache[cache_key] = data
                return data

        self._media_cache[cache_key] = data
        return data

    async def lookup_media(self, guild: Optional[discord.Guild], query: str, season: int = 1, episode: int = 1) -> discord.Embed:
        """Search media on TMDB and fetch intro timestamps from TheIntroDB v4."""
        settings = await self.get_guild_settings(guild)
        api_key = settings["tmdb_api_key"]
        if not api_key:
            return discord.Embed(
                title="Configuration Missing",
                description="TMDB API key is not configured for this server. Set it using `-tidbstats tmdbkey <key>`.",
                color=discord.Color.red(),
            )

        encoded_query = urllib.parse.quote(query)
        search_url = f"https://api.themoviedb.org/3/search/multi?api_key={api_key}&query={encoded_query}"
        search_res, status = await self.fetch_json(search_url)

        if not search_res or not search_res.get("results"):
            return discord.Embed(
                title="Media Not Found",
                description=f"Could not find any TV show or movie matching `{query}` on TMDB.",
                color=discord.Color.orange(),
            )

        match = None
        for item in search_res["results"]:
            if item.get("media_type") in ("tv", "movie"):
                match = item
                break

        if not match:
            return discord.Embed(
                title="No Valid Media Found",
                description=f"`{query}` did not return a movie or TV series.",
                color=discord.Color.orange(),
            )

        media_type = match["media_type"]
        tmdb_id = match["id"]
        title = match.get("name") or match.get("title") or query
        poster_path = match.get("poster_path")
        poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

        if media_type == "tv":
            tidb_url = f"https://api.theintrodb.org/v4/media?tmdb_id={tmdb_id}&season={season}&episode={episode}"
            heading = f"{title} — Season {season}, Episode {episode}"
        else:
            tidb_url = f"https://api.theintrodb.org/v4/media?tmdb_id={tmdb_id}"
            heading = f"{title} (Movie)"

        tidb_res, tidb_status = await self.fetch_json(tidb_url)

        embed = discord.Embed(title=heading, color=0x5865F2)
        if poster_url:
            embed.set_thumbnail(url=poster_url)

        if tidb_status == 404:
            embed.color = discord.Color.gold()
            embed.description = (
                f"No timestamps submitted yet for this {media_type}.\n\n"
                f"You can submit intro and credits markers at [theintrodb.org](https://theintrodb.org)."
            )
            embed.set_footer(text="TheIntroDB v4 • No Submissions Found")
            return embed

        if tidb_status != 200 or not tidb_res:
            embed.color = discord.Color.red()
            embed.description = f"TheIntroDB returned HTTP status {tidb_status} while searching for skip timestamps."
            return embed

        intro_segments = tidb_res.get("intro", [])
        credits_segments = tidb_res.get("credits", [])
        recap_segments = tidb_res.get("recap", [])
        preview_segments = tidb_res.get("preview", [])

        segments_text = []

        if intro_segments:
            for seg in intro_segments:
                start = format_ms(seg.get("start_ms"))
                end = format_ms(seg.get("end_ms"))
                dur = ""
                if seg.get("start_ms") is not None and seg.get("end_ms") is not None:
                    dur_s = max(0, int((seg["end_ms"] - seg["start_ms"]) / 1000))
                    dur = f" ({dur_s}s)"
                segments_text.append(f"⏱️ **Intro:** `{start} — {end}`{dur}")
        else:
            segments_text.append("⏱️ **Intro:** None detected")

        if credits_segments:
            for seg in credits_segments:
                start = format_ms(seg.get("start_ms"))
                end = format_ms(seg.get("end_ms"))
                segments_text.append(f"🎬 **Credits:** `{start} — {end}`")

        if recap_segments:
            for seg in recap_segments:
                start = format_ms(seg.get("start_ms"))
                end = format_ms(seg.get("end_ms"))
                segments_text.append(f"🔄 **Recap:** `{start} — {end}`")

        if preview_segments:
            for seg in preview_segments:
                start = format_ms(seg.get("start_ms"))
                end = format_ms(seg.get("end_ms"))
                segments_text.append(f"⏩ **Preview:** `{start} — {end}`")

        embed.add_field(
            name="Skip Timestamps",
            value="\n".join(segments_text),
            inline=False,
        )

        tvdb_id = tidb_res.get("tvdb_id")
        embed.set_footer(text=f"TheIntroDB v4 • TVDB: {tvdb_id or 'N/A'}")
        return embed

    async def get_random_intro_embed(self, guild: Optional[discord.Guild]) -> discord.Embed:
        """Fetch a random top show from TheIntroDB and display its intro timestamps."""
        top_media = self._last_stats_cache.get("top_media")
        settings = await self.get_guild_settings(guild)

        if not top_media:
            data, status = await self.fetch_json(settings["url"])
            if status == 200 and data:
                top_media = data.get("top_media", [])

        if not top_media:
            return discord.Embed(
                title="No Media Available",
                description="TheIntroDB statistics are currently unavailable.",
                color=discord.Color.red(),
            )

        item = random.choice(top_media)
        tvdb_id = item.get("tvdb_id")
        details = await self.resolve_tvdb_media(tvdb_id, item.get("type", "tv"), settings["tmdb_api_key"])

        title = details["title"]
        tidb_url = f"https://api.theintrodb.org/v4/media?tvdb_id={tvdb_id}&season=1&episode=1"
        tidb_res, tidb_status = await self.fetch_json(tidb_url)

        embed = discord.Embed(title=f"🎲 {title} — S01E01", color=0x2ECC71)
        if details["poster_url"]:
            embed.set_thumbnail(url=details["poster_url"])

        if tidb_status == 200 and tidb_res and tidb_res.get("intro"):
            seg = tidb_res["intro"][0]
            start = format_ms(seg.get("start_ms"))
            end = format_ms(seg.get("end_ms"))
            embed.add_field(name="Intro Timestamps", value=f"`{start} — {end}`", inline=False)
        else:
            embed.add_field(
                name="Intro Timestamps",
                value="Episode 1 markers pending verification.",
                inline=False,
            )

        embed.add_field(
            name="Community Activity",
            value=f"{item.get('submissions', 0):,} verified submissions",
            inline=False,
        )
        embed.set_footer(text=f"TheIntroDB v4 • TVDB ID: {tvdb_id}")
        return embed

    def get_setup_guide_embed(self) -> discord.Embed:
        """Generate media server integration guide."""
        embed = discord.Embed(
            title="TheIntroDB Integration Guide",
            description="Use community skip timestamps with your self-hosted media server.",
            color=0x3498DB,
        )
        embed.add_field(
            name="Jellyfin & Emby",
            value=(
                "1. Install the **Intro Skipper** plugin from your repository catalog.\n"
                "2. Open Plugin Settings and enable **TheIntroDB** provider.\n"
                "3. Player will automatically display 'Skip Intro' or jump past titles."
            ),
            inline=False,
        )
        embed.add_field(
            name="Plex Media Server",
            value=(
                "Use community webhook integrations (such as `plex-intro-skipper`) "
                "to synchronize intro and credit markers directly with your Plex library database."
            ),
            inline=False,
        )
        embed.add_field(
            name="Contributing",
            value="Submit missing timestamps directly at [theintrodb.org](https://theintrodb.org).",
            inline=False,
        )
        embed.set_footer(text="TheIntroDB Community Guide")
        return embed

    async def create_embed(self, guild: discord.Guild, data: Dict[str, Any], logo_url: str, api_key: str) -> discord.Embed:
        """Create the central live statistics embed."""
        self._last_stats_cache = data

        succeeded = data.get("accepted_timestamps", 0)
        total_submissions = data.get("total_submissions", 0)
        unaccepted = max(0, total_submissions - succeeded)
        ratio = (succeeded / total_submissions) if total_submissions > 0 else 0.0

        contributors = data.get("contributors", 0)
        total_shows = data.get("total_shows", 0)
        total_movies = data.get("total_movies", 0)
        total_episodes = data.get("total_episodes", 0)

        ms_saved = data.get("total_time_saved_ms", 0)
        hours_saved = int(ms_saved / (1000 * 60 * 60))
        years_saved = hours_saved / (24 * 365.25)

        embed = discord.Embed(title="TheIntroDB — Live Statistics", color=0x2ECC71)
        if logo_url:
            embed.set_thumbnail(url=logo_url)

        bar = make_progress_bar(ratio, length=10)
        stats_text = (
            f"🗃️ **Submissions:** {total_submissions:,}\n"
            f"✅ **Validated:** {succeeded:,} {bar}\n"
            f"🚫 **Unaccepted:** {unaccepted:,}\n"
            f"📡 **Status:** 🟢 Online"
        )
        embed.add_field(name="Database Integrity", value=stats_text, inline=False)

        now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
        settings = await self.get_guild_settings(guild)
        last_snap = settings.get("last_snapshot") or {}
        delta_line = ""
        if last_snap:
            prev_ts = last_snap.get("timestamp", 0)
            prev_subs = last_snap.get("submissions", 0)
            prev_hours = last_snap.get("hours_saved", 0)
            if now_ts - prev_ts >= 21600:
                diff_subs = total_submissions - prev_subs
                diff_hours = hours_saved - prev_hours
                if diff_subs > 0 or diff_hours > 0:
                    delta_line = f"\n📈 **Recent Growth:** +{diff_subs:,} submissions (+{diff_hours:,} hrs)"
                await self.config.guild(guild).last_snapshot.set(
                    {"timestamp": now_ts, "submissions": total_submissions, "hours_saved": hours_saved}
                )
        else:
            await self.config.guild(guild).last_snapshot.set(
                {"timestamp": now_ts, "submissions": total_submissions, "hours_saved": hours_saved}
            )

        growth_text = (
            f"👥 **Contributors:** {contributors:,}\n"
            f"📺 **Shows:** {total_shows:,}\n"
            f"🎬 **Movies:** {total_movies:,}\n"
            f"🎞️ **Episodes:** {total_episodes:,}\n"
            f"⏳ **Time Saved:** {hours_saved:,} hrs (~{years_saved:.2f} yrs){delta_line}"
        )
        embed.add_field(name="Database Metrics", value=growth_text, inline=False)

        top_media = data.get("top_media", [])
        if top_media:
            top_list = []
            for i, item in enumerate(top_media[:6], 1):
                tvdb_id = item.get("tvdb_id") or item.get("tmdb_id")
                media_type = item.get("type", "tv")
                resolved = await self.resolve_tvdb_media(tvdb_id, media_type, api_key)
                name = resolved["title"]
                count = item.get("submissions", 0)
                top_list.append(f"**{i}. {name}** — {count:,} submissions")

            embed.add_field(name="Top Contributing Media", value="\n".join(top_list), inline=False)

        embed.set_footer(text="TheIntroDB v4 API • Updated")
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        return embed

    async def update_guild_stats(self, guild: discord.Guild) -> bool:
        """Fetch and update stats embed for a specific guild."""
        settings = await self.get_guild_settings(guild)
        if not settings["enabled"] or not settings["channel_id"]:
            return False

        channel = guild.get_channel(settings["channel_id"])
        if not channel:
            return False

        data, status = await self.fetch_json(settings["url"])
        if not data or status != 200:
            log.warning("Failed to fetch TIDB stats for guild %s: status %s", guild.id, status)
            return False

        embed = await self.create_embed(guild, data, settings["logo"], settings["tmdb_api_key"])
        view = TidbStatsView(self)

        msg_id = settings.get("message_id")
        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(embed=embed, view=view)
                return True
            except (discord.NotFound, discord.Forbidden):
                pass

        try:
            msg = await channel.send(embed=embed, view=view)
            await self.config.guild(guild).message_id.set(msg.id)
            return True
        except discord.Forbidden:
            log.warning("Missing permissions to send stats in channel %s", channel.id)
            return False

    @tasks.loop(hours=1)
    async def update_stats(self):
        for guild in self.bot.guilds:
            try:
                await self.update_guild_stats(guild)
            except Exception as e:
                log.error("Error updating stats for guild %s: %s", guild.id, e)

    @update_stats.before_loop
    async def before_update_stats(self):
        await self.bot.wait_until_ready()

    @commands.group(name="tidb", invoke_without_command=True)
    async def tidb_group(self, ctx):
        """TheIntroDB commands."""
        await ctx.send_help()

    @tidb_group.command(name="check", aliases=["lookup"])
    async def tidb_check(self, ctx, query: str, season: int = 1, episode: int = 1):
        """Look up intro skip timestamps for a TV show or movie.

        Examples:
          [p]tidb check "The Simpsons" 1 1
          [p]tidb check "Breaking Bad" 2 4
          [p]tidb check "Inception"
        """
        async with ctx.typing():
            embed = await self.lookup_media(ctx.guild, query, season, episode)
            await ctx.send(embed=embed)

    @tidb_group.command(name="stats")
    async def tidb_stats(self, ctx):
        """View current TheIntroDB database statistics."""
        async with ctx.typing():
            settings = await self.get_guild_settings(ctx.guild)
            data, status = await self.fetch_json(settings["url"])
            if status != 200 or not data:
                await ctx.send("Failed to retrieve statistics from TheIntroDB API.")
                return
            embed = await self.create_embed(ctx.guild, data, settings["logo"], settings["tmdb_api_key"])
            view = TidbStatsView(self)
            await ctx.send(embed=embed, view=view)

    @commands.group(name="tidbstats")
    @commands.admin_or_permissions(manage_guild=True)
    async def tidbstats_admin(self, ctx):
        """Manage TIDB Stats display."""
        pass

    @tidbstats_admin.command(name="channel")
    async def channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for the stats billboard."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send(f"Stats will be posted to {channel.mention}. Initializing refresh...")
        await self.update_guild_stats(ctx.guild)

    @tidbstats_admin.command(name="enable")
    async def enable(self, ctx):
        """Enable auto-updates."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("Auto-updates enabled.")
        await self.update_guild_stats(ctx.guild)

    @tidbstats_admin.command(name="disable")
    async def disable(self, ctx):
        """Disable auto-updates."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("Auto-updates disabled.")

    @tidbstats_admin.command(name="interval")
    async def interval(self, ctx, hours: int):
        """Set stats update frequency (1 to 24 hours)."""
        if hours < 1 or hours > 24:
            await ctx.send("Interval must be between 1 and 24 hours.")
            return
        await self.config.guild(ctx.guild).interval_hours.set(hours)
        self.update_stats.change_interval(hours=hours)
        await ctx.send(f"Update interval set to every {hours} hour(s).")

    @tidbstats_admin.command(name="logo")
    async def logo(self, ctx, url: str):
        """Set the logo URL for the embed."""
        await self.config.guild(ctx.guild).logo.set(url)
        await ctx.send("Logo updated.")
        await self.update_guild_stats(ctx.guild)

    @tidbstats_admin.command(name="tmdbkey")
    async def tmdbkey(self, ctx, key: str):
        """Set the TMDB API key."""
        await self.config.guild(ctx.guild).tmdb_api_key.set(key)
        await ctx.send("TMDB API key updated.")
        await self.update_guild_stats(ctx.guild)

    @tidbstats_admin.command(name="refresh")
    async def refresh(self, ctx):
        """Force a refresh immediately."""
        await ctx.send("Refreshing stats...")
        success = await self.update_guild_stats(ctx.guild)
        if success:
            await ctx.tick()
        else:
            await ctx.send("Failed to refresh stats. Check bot logs.")


async def setup(bot):
    await bot.add_cog(TidbStats(bot))
