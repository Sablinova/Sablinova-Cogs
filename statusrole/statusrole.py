import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import asyncio

class StatusRole(commands.Cog):
    """Cog that assigns roles based on status/about me."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(keyword=None, role_id=None, log_channel=None, active=True)
        self.task = self.bot.loop.create_task(self.check_status())

    async def check_status(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            for guild in self.bot.guilds:
                active = await self.config.guild(guild).active()
                if not active:
                    continue

                keyword = await self.config.guild(guild).keyword()
                role_id = await self.config.guild(guild).role_id()
                log_channel_id = await self.config.guild(guild).log_channel()

                if not keyword or not role_id:
                    continue

                role = guild.get_role(role_id)
                log_channel = self.bot.get_channel(log_channel_id) if log_channel_id else None
                if not role:
                    continue

                debug_log = [f"🔍 **Debug Info:** Checking for keyword `{keyword}`\n"]
                for member in guild.members:
                    if member.bot:
                        continue

                    status_text = (member.activity.name if member.activity else "")
                    about_me = (member.global_name or "")  # Using global_name since about_me isn't directly available
                    full_text = f"{status_text} {about_me}".lower()

                    has_role = role in member.roles
                    contains_keyword = keyword.lower() in full_text

                    if contains_keyword and not has_role:
                        try:
                            await member.add_roles(role)
                            debug_log.append(f"✅ {member.mention} matched `{keyword}` and was given `{role.name}` role.")
                            if log_channel:
                                await log_channel.send(f"✅ {member.mention} has been given the `{role.name}` role.")
                        except discord.Forbidden:
                            debug_log.append(f"⚠️ Error: Missing permissions to add {role.name} to {member.mention}.")
                    elif not contains_keyword and has_role:
                        try:
                            await member.remove_roles(role)
                            debug_log.append(f"❌ {member.mention} no longer matches `{keyword}` and lost `{role.name}` role.")
                            if log_channel:
                                await log_channel.send(f"❌ {member.mention} no longer has `{role.name}` role.")
                        except discord.Forbidden:
                            debug_log.append(f"⚠️ Error: Missing permissions to remove {role.name} from {member.mention}.")

                # Send debug output to log channel if enabled
                if log_channel and debug_log:
                    await log_channel.send("\n".join(debug_log))

            await asyncio.sleep(60)  # Runs every 60 seconds

    @commands.guild_only()
    @commands.admin()
    @commands.group()
    async def statusrole(self, ctx):
        """Manage the status role settings."""
        pass

    @statusrole.command()
    async def set(self, ctx, keyword: str):
        """Set the keyword to monitor in status/about me."""
        await self.config.guild(ctx.guild).keyword.set(keyword)
        await ctx.send(f"Keyword set to `{keyword}`.")

    @statusrole.command()
    async def active(self, ctx):
        """Show members who currently have the role and match the keyword."""
        role_id = await self.config.guild(ctx.guild).role_id()
        keyword = await self.config.guild(ctx.guild).keyword()
        if not role_id or not keyword:
            return await ctx.send("⚠️ No keyword or role is set.")

        role = ctx.guild.get_role(role_id)
        if not role:
            return await ctx.send("⚠️ Role not found.")

        matching_members = [m.mention for m in ctx.guild.members if role in m.roles]
        if matching_members:
            await ctx.send(f"✅ Users with `{role.name}` role matching `{keyword}`:\n" + "\n".join(matching_members))
        else:
            await ctx.send("❌ No users currently match the criteria.")

    @statusrole.command()
    async def deactivate(self, ctx):
        """Deactivate status role checking in the server."""
        await self.config.guild(ctx.guild).active.set(False)
        await ctx.send("🔴 Status role checking has been **deactivated** for this server.")

    @commands.guild_only()
    @commands.admin()
    @commands.command()
    async def roleset(self, ctx, role: discord.Role):
        """Set the role to assign when the keyword is found."""
        await self.config.guild(ctx.guild).role_id.set(role.id)
        await ctx.send(f"Role `{role.name}` set.")

    @commands.guild_only()
    @commands.admin()
    @commands.command()
    async def logset(self, ctx, channel: discord.TextChannel):
        """Set the logging channel."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"Log channel set to {channel.mention}.")

async def setup(bot):
    await bot.add_cog(StatusRole(bot))
