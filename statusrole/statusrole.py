import discord
import json
import os
from redbot.core import commands, checks
from redbot.core.bot import Red

CONFIG_FILE = "data/statusrole/config.json"

class StatusRole(commands.Cog):
    """Assign roles based on status or bio"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = self.load_config()

    def load_config(self):
        """Load or create config.json"""
        if not os.path.exists(CONFIG_FILE):
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump({}, f)
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)

    def save_config(self):
        """Save settings to JSON file"""
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=4)

    @commands.guild_only()
    @commands.group()
    @checks.admin()
    async def statusrole(self, ctx):
        """Manage StatusRole settings"""
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `-statusrole set`, `-statusrole roleset`, or `-statusrole logset`.")

    @statusrole.command()
    async def set(self, ctx, keyword: str):
        """Set the keyword to check in status/about me"""
        guild_id = str(ctx.guild.id)
        if guild_id not in self.config:
            self.config[guild_id] = {}
        self.config[guild_id]["keyword"] = keyword
        self.save_config()
        await ctx.send(f"Keyword set to `{keyword}` for this server.")

    @statusrole.command()
    async def roleset(self, ctx, role: discord.Role):
        """Set the role to be assigned when a user has the keyword"""
        guild_id = str(ctx.guild.id)
        if guild_id not in self.config:
            self.config[guild_id] = {}
        self.config[guild_id]["role_id"] = role.id
        self.save_config()
        await ctx.send(f"Role `{role.name}` set for this server.")

    @statusrole.command()
    async def logset(self, ctx, channel: discord.TextChannel):
        """Set the logging channel"""
        guild_id = str(ctx.guild.id)
        if guild_id not in self.config:
            self.config[guild_id] = {}
        self.config[guild_id]["log_channel_id"] = channel.id
        self.save_config()
        await ctx.send(f"Logging set to `{channel.name}`.")

    async def check_status(self, member: discord.Member):
        """Check member's status/bio and assign/remove role"""
        guild_id = str(member.guild.id)
        if guild_id not in self.config:
            return

        keyword = self.config[guild_id].get("keyword")
        role_id = self.config[guild_id].get("role_id")
        log_channel_id = self.config[guild_id].get("log_channel_id")

        if not keyword or not role_id:
            return  # No settings configured

        role = member.guild.get_role(role_id)
        log_channel = member.guild.get_channel(log_channel_id) if log_channel_id else None

        # Get user's status/about me
        about_me = member.global_name or ""  # Get the user's about me (fallback)
        if member.activity and isinstance(member.activity, discord.CustomActivity):
            status = member.activity.name or ""
        else:
            status = ""

        # Role management logic
        if keyword in status or keyword in about_me:
            if role and role not in member.roles:
                await member.add_roles(role)
                if log_channel:
                    await log_channel.send(f"✅ `{member}` met the keyword `{keyword}` and was given `{role.name}`")
        else:
            if role and role in member.roles:
                await member.remove_roles(role)
                if log_channel:
                    await log_channel.send(f"❌ `{member}` lost the keyword `{keyword}` and was removed from `{role.name}`")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Triggered when a user updates their status or bio"""
        await self.check_status(after)

    @commands.Cog.listener()
    async def on_ready(self):
        """Run a full check when bot starts"""
        for guild in self.bot.guilds:
            for member in guild.members:
                await self.check_status(member)
