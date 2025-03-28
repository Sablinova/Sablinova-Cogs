import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import asyncio

class StatusRole(commands.Cog):
    """Cog that assigns roles based on status/about me.
    
    Commands:
    - `statusrole set <keyword>`: Set the keyword to monitor.
    - `statusrole roleset <role>`: Set the role to assign.
    - `statusrole logset <channel>`: Set the logging channel.
    - `statusrole active`: Show users who match the keyword and have the role.
    - `statusrole deactivate`: Disable status role tracking for the server.
    - `statusrole activate`: Re-enable status role tracking for the server.
    - `statusrole debug`: Show debug info.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(keyword=None, role_id=None, log_channel=None, active=True)
        self.debug_mode = False
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

                for member in guild.members:
                    if member.bot:
                        continue

                    status_text = member.activity.name if member.activity else ""
                    about_me = getattr(member, "about_me", "")
                    full_text = f"{status_text} {about_me}".lower()

                    has_role = role in member.roles
                    contains_keyword = keyword.lower() in full_text

                    if contains_keyword and not has_role:
                        try:
                            await member.add_roles(role)
                            if log_channel:
                                await log_channel.send(f"✅ {member.mention} has been given the `{role.name}` role.")
                        except discord.Forbidden:
                            if log_channel:
                                await log_channel.send(f"⚠️ Error: Missing permissions to add {role.name} to {member.mention}.")
                    elif not contains_keyword and has_role:
                        try:
                            await member.remove_roles(role)
                            if log_channel:
                                await log_channel.send(f"❌ {member.mention} no longer has `{role.name}` role.")
                        except discord.Forbidden:
                            if log_channel:
                                await log_channel.send(f"⚠️ Error: Missing permissions to remove {role.name} from {member.mention}.")

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
    async def roleset(self, ctx, role: discord.Role):
        """Set the role to assign when the keyword is found."""
        await self.config.guild(ctx.guild).role_id.set(role.id)
        await ctx.send(f"Role `{role.name}` set.")

    @statusrole.command()
    async def logset(self, ctx, channel: discord.TextChannel):
        """Set the logging channel."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"Log channel set to {channel.mention}.")

    @statusrole.command()
    async def debug(self, ctx):
        """Debugging command to check status and about me."""
        guild = ctx.guild
        keyword = await self.config.guild(guild).keyword()
        role_id = await self.config.guild(guild).role_id()
        role = guild.get_role(role_id) if role_id else None

        if not keyword:
            return await ctx.send("No keyword is set. Use `statusrole set <keyword>` first.")
        
        if not role:
            return await ctx.send("⚠️ Error: No valid role is set. Use `statusrole roleset <role>` first.")

        debug_message = f"🔍 **Debug Info:** Checking for keyword `{keyword}` and role `{role.name}`\n\n"

        for member in guild.members:
            if member.bot:
                continue

            status_text = member.activity.name if member.activity else ""
            about_me = getattr(member, "about_me", "")
            full_text = f"{status_text} {about_me}".lower()

            contains_keyword = keyword.lower() in full_text
            has_role = role in member.roles

            debug_message += f"👤 {member.name}: `{full_text}` -> {'✅ Match' if contains_keyword else '❌ No Match'} | {'🎭 Has Role' if has_role else '🚫 No Role'}\n"

        for chunk in [debug_message[i:i+1900] for i in range(0, len(debug_message), 1900)]:
            await ctx.send(f"```{chunk}```")

    @statusrole.command()
    async def active(self, ctx):
        """Show users who currently match the keyword and have the role."""
        guild = ctx.guild
        keyword = await self.config.guild(guild).keyword()
        role_id = await self.config.guild(guild).role_id()
        role = guild.get_role(role_id) if role_id else None

        if not keyword or not role:
            return await ctx.send("⚠️ No keyword or role is set.")

        active_members = [m for m in guild.members if role in m.roles and keyword.lower() in (m.activity.name.lower() if m.activity else "")]
        if not active_members:
            return await ctx.send("No active members found.")

        member_list = ", ".join([m.mention for m in active_members])
        await ctx.send(f"👥 Members with `{role.name}` and keyword `{keyword}`: {member_list}")

    @statusrole.command()
    async def deactivate(self, ctx):
        """Disable status role tracking for this server."""
        await self.config.guild(ctx.guild).active.set(False)
        await ctx.send("🚫 Status role tracking has been disabled for this server.")

    @statusrole.command()
    async def activate(self, ctx):
        """Re-enable status role tracking for this server."""
        await self.config.guild(ctx.guild).active.set(True)
        await ctx.send("✅ Status role tracking has been re-enabled for this server.")

async def setup(bot):
    await bot.add_cog(StatusRole(bot))
