import discord
from redbot.core import commands, Config
from discord.ext import tasks
import aiohttp
import logging
import datetime

class TidbStats(commands.Cog):
    """Live statistics from The Intro Database."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8473829201, force_registration=True)
        self.config.register_guild(
            channel_id=None,
            message_id=None,
            enabled=False,
            url="https://api.theintrodb.org/v2/stats",
            logo="https://cdn.discordapp.com/icons/1457550295742812307/a0259221faeb0133e43760697573577a.webp?format=webp&width=60&height=60",
            tmdb_api_key="a416278866ffc1357efc0369047cc736"
        )
        self.session = aiohttp.ClientSession()
        self.update_stats.start()

    def cog_unload(self):
        self.update_stats.cancel()
        self.bot.loop.create_task(self.session.close())

    async def fetch_stats(self, url):
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logging.error(f"TIDB Fetch Error: {e}")
        return None

    async def fetch_tmdb_name(self, tmdb_id, media_type, api_key):
        """Fetch the name of a show/movie from TMDB."""
        if not api_key:
            return f"TMDB ID: {tmdb_id}"
            
        url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}?api_key={api_key}"
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Return 'name' for TV, 'title' for movie
                    return data.get("name") or data.get("title") or f"TMDB ID: {tmdb_id}"
        except Exception as e:
            logging.error(f"TMDB Fetch Error for {tmdb_id}: {e}")
        return f"TMDB ID: {tmdb_id}"

    async def create_embed(self, data, logo_url, api_key):
        # Stats
        succeeded = data.get("accepted_timestamps", 0)
        total_submissions = data.get("total_submissions", 0)
        failed = total_submissions - succeeded
        
        contributors = data.get("contributors", 0)
        total_shows = data.get("total_shows", 0)
        total_movies = data.get("total_movies", 0)
        total_episodes = data.get("total_episodes", 0)
        
        # Time saved calculation
        ms_saved = data.get("total_time_saved_ms", 0)
        # ms -> seconds -> minutes -> hours
        hours_saved = int(ms_saved / (1000 * 60 * 60))
        
        timestamp = int(datetime.datetime.now().timestamp())

        embed = discord.Embed(title="TIDB Stats", color=0x00FF00)
        embed.set_thumbnail(url=logo_url)
        
        # Main Stats Block
        stats_text = (
            f"🚫 **Unaccepted:** {failed:,}\n"
            f"✅ **Accepted Timestamps:** {succeeded:,}\n"
            f"🗃️ **Total Submissions:** {total_submissions:,}\n"
            f"📡 **Status:** 🟢 Online"
        )
        embed.add_field(name="TIDB API", value=stats_text, inline=False)
        
        # Database Growth Block
        growth_text = (
            f"👥 **Contributors:** {contributors}\n"
            f"📺 **Shows:** {total_shows}\n"
            f"🎬 **Movies:** {total_movies}\n"
            f"🎞️ **Episodes:** {total_episodes:,}\n"
            f"⏳ **Time Saved:** {hours_saved:,} hours"
        )
        embed.add_field(name="Database Growth", value=growth_text, inline=False)

        # Top Media Block
        top_media = data.get("top_media", [])
        if top_media:
            top_list = []
            for i, item in enumerate(top_media[:6], 1): # Top 6
                name = await self.fetch_tmdb_name(item['tmdb_id'], item['type'], api_key)
                count = item.get('submissions', 0)
                top_list.append(f"**{i}. {name}** — {count:,} submissions")
            
            embed.add_field(name="Top Contributing Media", value="\n".join(top_list), inline=False)
        
        embed.set_footer(text=f"Last Checked:")
        embed.timestamp = datetime.datetime.now() # This adds the ISO timestamp for the footer
        # Alternatively, use description for a relative time string:
        # embed.description = f"Last updated: <t:{timestamp}:R>"
        
        return embed

    @tasks.loop(hours=1)
    async def update_stats(self):
        for guild in self.bot.guilds:
            settings = await self.config.guild(guild).all()
            if not settings["enabled"] or not settings["channel_id"]:
                continue

            channel = guild.get_channel(settings["channel_id"])
            if not channel:
                continue

            data = await self.fetch_stats(settings["url"])
            if not data:
                continue

            embed = await self.create_embed(data, settings["logo"], settings["tmdb_api_key"])

            # Try editing existing message
            if settings["message_id"]:
                try:
                    msg = await channel.fetch_message(settings["message_id"])
                    await msg.edit(embed=embed)
                    continue
                except (discord.NotFound, discord.Forbidden):
                    pass # Message deleted, send new one

            # Send new message
            try:
                msg = await channel.send(embed=embed)
                await self.config.guild(guild).message_id.set(msg.id)
            except discord.Forbidden:
                pass

    @update_stats.before_loop
    async def before_update_stats(self):
        await self.bot.wait_until_ready()

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def tidbstats(self, ctx):
        """Manage TIDB Stats display."""
        pass

    @tidbstats.command()
    async def channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for the stats embed."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send(f"✅ Stats will be displayed in {channel.mention}. Fetching now...")
        # Trigger immediate update
        await self.update_stats()

    @tidbstats.command()
    async def enable(self, ctx):
        """Enable auto-updates."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("✅ Auto-updates enabled.")
        await self.update_stats()

    @tidbstats.command()
    async def disable(self, ctx):
        """Disable auto-updates."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("❌ Auto-updates disabled.")

    @tidbstats.command()
    async def logo(self, ctx, url: str):
        """Set the logo URL for the embed."""
        await self.config.guild(ctx.guild).logo.set(url)
        await ctx.send(f"✅ Logo updated. It will appear on the next refresh.")
        await self.update_stats()

    @tidbstats.command()
    async def tmdbkey(self, ctx, key: str):
        """Set the TMDB API key."""
        await self.config.guild(ctx.guild).tmdb_api_key.set(key)
        await ctx.send("✅ TMDB API Key updated.")
        await self.update_stats()

    @tidbstats.command()
    async def refresh(self, ctx):
        """Force a refresh immediately."""
        await ctx.send("🔄 Refreshing stats...")
        await self.update_stats()
        await ctx.tick()

async def setup(bot):
    await bot.add_cog(TidbStats(bot))
