import discord
from redbot.core import commands, Config
import asyncio

class StatusRole(commands.Cog):
    """Cog that assigns roles based on status/about me."""

    def __init__(self, bot):
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
                    continue  # Skip if deactivated

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
                    full_text = f"{status_text} {about_me}".strip().lower()

                    has_role = role in member.roles
                    contains_keyword = keyword.lower() in full_text

                    # ✅ Only log role changes, no extra debug messages
                    if contains_keyword and not has_role:
                        await member.add_roles(role)
                        if log_channel:
                            await log_channel.send(f"✅ {member.mention} has been given `{role.name}`.")
                    elif not contains_keyword and has_role:
                        await member.remove_roles(role)
                        if log_channel:
                            await log_channel.send(f"❌ {member.mention} lost `{role.name}`.")

            await asyncio.sleep(60)  # Run every 60 seconds

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
        """Debugging command to check users affected by the keyword."""
        guild = ctx.guild
        keyword = await self.config.guild(guild).keyword()
        role_id = await self.config.guild(guild).role_id()
        role = guild.get_role(role_id) if role_id else None

        if not keyword:
            return await ctx.send("⚠️ No keyword set. Use `statusrole set <keyword>` first.")
        
        if not role:
            return await ctx.send("⚠️ No valid role set. Use `statusrole roleset <role>` first.")

        debug_message = ""

        for member in guild.members:
            if member.bot:
                continue  # Ignore bots

            status_text = member.activity.name if member.activity else ""
            about_me = getattr(member, "about_me", "")
            full_text = f"{status_text} {about_me}".strip().lower()

            contains_keyword = keyword.lower() in full_text
            has_role = role in member.roles

            if contains_keyword and not has_role:
                debug_message += f"✅ {member.display_name} matches `{keyword}` but **doesn't have `{role.name}`**.\n"
            elif not contains_keyword and has_role:
                debug_message += f"❌ {member.display_name} **has `{role.name}` but doesn't match `{keyword}`**.\n"

        if debug_message:
            await ctx.send(f"```{debug_message}```")
        else:
            await ctx.send("✅ No mismatches found. Everything is correctly assigned.")

    @statusrole.command()
    async def active(self, ctx):
        """Show users who have the role and match the keyword."""
        guild = ctx.guild
        keyword = await self.config.guild(guild).keyword()
        role_id = await self.config.guild(guild).role_id()
        role = guild.get_role(role_id) if role_id else None

        if not keyword or not role:
            return await ctx.send("⚠️ No keyword or role set. Use `statusrole set` and `statusrole roleset` first.")

        matching_members = [
            member.mention for member in guild.members
            if role in member.roles and keyword.lower() in (member.activity.name.lower() if member.activity else "")
        ]

        if matching_members:
            await ctx.send(f"✅ **Active Members with `{role.name}`:** {', '.join(matching_members)}")
        else:
            await ctx.send(f"❌ No active members currently match `{keyword}`.")

    @statusrole.command()
    async def deactivate(self, ctx):
        """Disable status role tracking in this guild."""
        await self.config.guild(ctx.guild).active.set(False)
        await ctx.send("🚫 Status role tracking has been **disabled** for this server.")

    @statusrole.command()
    async def activate(self, ctx):
        """Enable status role tracking in this guild."""
        await self.config.guild(ctx.guild).active.set(True)
        await ctx.send("✅ Status role tracking has been **enabled** for this server.")

async def setup(bot):
    await bot.add_cog(StatusRole(bot))
