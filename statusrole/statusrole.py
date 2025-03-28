from redbot.core import commands, Config
import discord
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import humanize_list

class StatusRole(commands.Cog):
    """Cog that assigns roles based on status/about me"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(keyword=None, role_id=None, log_channel=None)

    @commands.guild_only()
    @commands.admin()
    @commands.command()
    async def statusrole(self, ctx, keyword: str):
        """Set the keyword to monitor in status/about me"""
        await self.config.guild(ctx.guild).keyword.set(keyword)
        await ctx.send(f"Keyword set to `{keyword}`.")

    @commands.guild_only()
    @commands.admin()
    @commands.command()
    async def roleset(self, ctx, role: discord.Role):
        """Set the role to assign when the keyword is found"""
        await self.config.guild(ctx.guild).role_id.set(role.id)
        await ctx.send(f"Role `{role.name}` set.")

    @commands.guild_only()
    @commands.admin()
    @commands.command()
    async def logset(self, ctx, channel: discord.TextChannel):
        """Set the logging channel"""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"Log channel set to {channel.mention}.")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Checks if a member's status/about me changes and assigns/removes roles accordingly."""
        guild = after.guild
        keyword = await self.config.guild(guild).keyword()
        role_id = await self.config.guild(guild).role_id()
        log_channel_id = await self.config.guild(guild).log_channel()

        if not keyword or not role_id:
            return  # No keyword or role set

        role = guild.get_role(role_id)
        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None

        if not role:
            return  # Role no longer exists

        status_text = (after.activity.name if after.activity else "") + (after.bio if hasattr(after, "bio") and after.bio else "")
        had_role = role in after.roles
        has_keyword = keyword.lower() in status_text.lower()

        if has_keyword and not had_role:
            await after.add_roles(role)
            if log_channel:
                await log_channel.send(f"✅ {after.mention} has been given the role `{role.name}` for containing `{keyword}` in their status/about me.")
        elif not has_keyword and had_role:
            await after.remove_roles(role)
            if log_channel:
                await log_channel.send(f"❌ {after.mention} has been removed from the role `{role.name}` as they no longer have `{keyword}` in their status/about me.")

async def setup(bot):
    await bot.add_cog(StatusRole(bot))
