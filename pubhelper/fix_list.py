import re

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'r', encoding='utf-8') as f:
    content = f.read()

# The function we want to replace:
#     @pubhelper_saveinst.command(name="list")
#     async def pubhelper_saveinst_list(self, ctx: commands.Context) -> None:
# ... up to the next @pubhelper_saveinst.command(name="remove")

pattern = re.compile(
    r'(    @pubhelper_saveinst\.command\(name="list"\)\n    async def pubhelper_saveinst_list\(self, ctx: commands\.Context\) -> None:\n.*?)    @pubhelper_saveinst\.command\(name="remove"\)',
    re.DOTALL
)

new_func = """    @pubhelper_saveinst.command(name="list")
    async def pubhelper_saveinst_list(self, ctx: commands.Context) -> None:
        \"\"\"List all custom and base games configured for /saveinst.\"\"\"
        custom_games = await self.config.custom_saveinst()
        from .savesigner import SAVE_PROFILES, SEGA_PROFILES
        
        view = SaveInstListView(ctx.author, custom_games, SAVE_PROFILES, SEGA_PROFILES)
        
        embed = discord.Embed(
            title="/saveinst Game Profiles",
            description="Select a game from the dropdown below to view its configuration and test its /saveinst output preview.",
            color=discord.Color.blue()
        )
        
        # Count stats
        cc_count = sum(1 for kw in SAVE_PROFILES if kw not in custom_games)
        sega_count = sum(1 for kw in SEGA_PROFILES if kw not in custom_games)
        
        embed.add_field(name="Stats", value=f"**Custom Games:** {len(custom_games)}\\n**Base ColdClient:** {cc_count}\\n**Base SEGA:** {sega_count}", inline=False)
        
        await ctx.send(embed=embed, view=view)

"""

content, count = pattern.subn(new_func + '    @pubhelper_saveinst.command(name="remove")', content)

if count == 0:
    print("FAILED TO REPLACE")
else:
    print("REPLACED SUCCESSFULLY")

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'w', encoding='utf-8') as f:
    f.write(content)

