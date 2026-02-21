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
            logo="https://cdn.discordapp.com/icons/1457550295742812307/a0259221faeb0133e43760697573577a.webp?format=webp&width=60&height=60"
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

    def create_embed(self, data, logo_url):
        # Mapping:
        # Succeeded = accepted_timestamps
        # Total = total_submissions
        # Failed = Total - Succeeded
        
        succeeded = data.get("accepted_timestamps", 0)
        total = data.get("total_submissions", 0)
        failed = total - succeeded
        
        timestamp = datetime.datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")

        embed = discord.Embed(title="TIDB Stats", color=0x00FF00) # Green for Online
        embed.set_thumbnail(url=logo_url) 
        
        embed.add_field(name="TIDB API", value=f"❌ **Failed:** {failed}\n✅ **Succeeded:** {succeeded}\n📊 **Total:** {total}\n📡 **Status:** 🟢 Online", inline=False)
        
        embed.set_footer(text=f"Last Checked: {timestamp}")
        return embed

    @tasks.loop(minutes=5)
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

            embed = self.create_embed(data, settings["logo"])

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
    async def refresh(self, ctx):
        """Force a refresh immediately."""
        await ctx.send("🔄 Refreshing stats...")
        await self.update_stats()
        await ctx.tick()

async def setup(bot):
    await bot.add_cog(TidbStats(bot))
