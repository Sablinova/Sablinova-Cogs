import discord
from redbot.core import commands, checks
import logging

class RoleAll(commands.Cog):
    """Give a role to all members in the server."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @checks.admin_or_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def roleall(self, ctx, role: discord.Role):
        """
        Give a specific role to ALL members in the server.
        
        Usage: [p]roleall <role_name_or_id>
        """
        # Hierarchy check
        if role >= ctx.guild.me.top_role:
            await ctx.send(f"❌ **Error:** I cannot assign {role.mention} because it is higher than or equal to my own top role.")
            return

        # Confirmation
        await ctx.send(f"⚠️ **Processing...**\nAttempting to add {role.mention} to all members. This might take a while depending on server size.")
        
        added_count = 0
        failed_count = 0
        already_has_count = 0
        
        members = ctx.guild.members
        
        # Simple feedback loop
        msg = await ctx.send(f"🔄 Starting... ({len(members)} members found)")

        for i, member in enumerate(members):
            if role in member.roles:
                already_has_count += 1
                continue
            
            try:
                await member.add_roles(role, reason=f"RoleAll command by {ctx.author}")
                added_count += 1
            except discord.Forbidden:
                failed_count += 1
            except discord.HTTPException:
                failed_count += 1
            
            # Update status every 50 members to avoid rate limits on the message edit
            if i % 50 == 0 and i > 0:
                try:
                    await msg.edit(content=f"🔄 Working... {i}/{len(members)} processed...")
                except:
                    pass

        await msg.edit(content=f"✅ **Finished!**\n- Added: {added_count}\n- Already had it: {already_has_count}\n- Failed (Perms/Error): {failed_count}")

async def setup(bot):
    await bot.add_cog(RoleAll(bot))
