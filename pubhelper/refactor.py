import sys
import re

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Insert SaveInstListView class before class SabPubHelper(commands.Cog):
view_class = r"""
class SaveInstListView(discord.ui.View):
    def __init__(self, author: discord.User, custom_games: dict, save_profiles: dict, sega_profiles: dict):
        super().__init__(timeout=180)
        self.author = author
        self.custom_games = custom_games
        self.save_profiles = save_profiles
        self.sega_profiles = sega_profiles
        self.build_selects()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return False
        return True

    def build_selects(self):
        options = []
        
        # 1. Custom Games
        for kw, data in sorted(self.custom_games.items()):
            options.append(discord.SelectOption(
                label=data['name'][:100], 
                description=f"Custom ({data.get('type', 'Unknown')}) - kw: {kw}"[:100],
                value=f"custom:{kw}"
            ))
            
        # 2. ColdClient Games
        for kw, profile in sorted(self.save_profiles.items()):
            if kw not in self.custom_games:  # Don't show base if overridden
                options.append(discord.SelectOption(
                    label=profile['name'][:100],
                    description=f"ColdClient - kw: {kw}"[:100],
                    value=f"base:{kw}"
                ))
                
        # 3. SEGA Games
        for kw, profile in sorted(self.sega_profiles.items()):
            if kw not in self.custom_games:
                options.append(discord.SelectOption(
                    label=profile['name'][:100],
                    description=f"SEGA - kw: {kw}"[:100],
                    value=f"sega:{kw}"
                ))
        
        # Batch options into selects of 25 (Discord UI limit)
        for i in range(0, len(options), 25):
            batch = options[i:i+25]
            select = discord.ui.Select(
                placeholder=f"Select a game to preview (Page {i//25 + 1})...",
                min_values=1,
                max_values=1,
                options=batch,
                custom_id=f"select_game_{i}"
            )
            select.callback = self.select_callback
            self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        cat, kw = value.split(":", 1)
        
        embed = discord.Embed(color=discord.Color.blue())
        from .savesigner import SAVE_INSTRUCTIONS, SAVE_INSTRUCTIONS_SEGA
        from pathlib import Path
        
        message = ""
        config_info = ""
        image_url = ""
        img_path = None
        
        if cat == "custom":
            data = self.custom_games[kw]
            config_info = f"**Type:** Custom `{data.get('type', 'custom')}`\n**Keyword:** `{kw}`\n**Name:** {data['name']}\n"
            
            if data.get("type") == "custom":
                message = data.get("custom_text", "").format(name=data["name"], keyword=kw)
            elif data.get("type") == "sega":
                message = SAVE_INSTRUCTIONS_SEGA.format(game_name=data["name"], game_folder=data.get("config_folder", ""))
                config_info += f"**Game Folder:** `{data.get('config_folder', '')}`\n"
            else:
                message = SAVE_INSTRUCTIONS.format(steam_id=data.get("steam_id", ""), config_folder=data.get("config_folder", ""))
                config_info += f"**Steam ID:** `{data.get('steam_id', '')}`\n**Config Folder:** `{data.get('config_folder', '')}`\n"
                
            if data.get("attach_image", False):
                image_url = data.get("custom_image_url", "")
                if not image_url:
                    img_path = Path(__file__).parent / "save_instruction.png"
                    
        elif cat == "base":
            data = self.save_profiles[kw]
            config_info = f"**Type:** Base `ColdClient`\n**Keyword:** `{kw}`\n**Name:** {data['name']}\n**Steam ID:** `{data.get('steam_id', '')}`\n**Config Folder:** `{data.get('config_folder', '')}`\n"
            message = SAVE_INSTRUCTIONS.format(steam_id=data.get("steam_id", ""), config_folder=data.get("config_folder", ""))
            img_path = Path(__file__).parent / "save_instruction.png"
            
        elif cat == "sega":
            data = self.sega_profiles[kw]
            config_info = f"**Type:** Base `SEGA`\n**Keyword:** `{kw}`\n**Name:** {data['name']}\n**Game Folder:** `{data.get('game_folder', '')}`\n"
            message = SAVE_INSTRUCTIONS_SEGA.format(game_name=data["name"], game_folder=data.get("game_folder", ""))
            img_path = Path(__file__).parent / "save_instruction.png"
            
        embed.title = f"Preview: {data['name']}"
        embed.description = f"{config_info}\n**--- Preview ---**\n\n{message}"
        
        kwargs = {"embed": embed, "ephemeral": True}
        
        if image_url:
            embed.set_image(url=image_url)
        elif img_path and img_path.exists():
            file = discord.File(str(img_path), filename="save_instruction.png")
            embed.set_image(url="attachment://save_instruction.png")
            kwargs["file"] = file
            
        await interaction.response.send_message(**kwargs)

class SabPubHelper"""

content = content.replace("class SabPubHelper", view_class)

old_list_command = r"""    @pubhelper_saveinst.command(name="list")
    async def pubhelper_saveinst_list(self, ctx: commands.Context) -> None:
        ""\"List all custom and base games configured for /saveinst.""\"
        custom_games = await self.config.custom_saveinst()
        
        msg = ""
        if custom_games:
            msg += "**Custom `/saveinst` Games:**\n"
            for keyword, data in custom_games.items():
                msg += f"• **{data['name']}** (Keyword: `{keyword}`)\n  Type: `{data['type']}`\n"
        else:
            msg += "**Custom `/saveinst` Games:**\n*None configured. Use `[p]pubhelper saveinst setup` to add one.*\n"
        
        msg += "\n**Base ColdClient Games:**\n"
        from .savesigner import SAVE_PROFILES, SEGA_PROFILES
        for key, profile in SAVE_PROFILES.items():
            msg += f"• **{profile['name']}** (Keyword: `{key.lower()}`)\n"

        msg += "\n**Base SEGA Games:**\n"
        for key, profile in SEGA_PROFILES.items():
            msg += f"• **{profile['name']}** (Keyword: `{key.lower()}`)\n"
            
        # If msg gets too long, split it, but usually discord limit is 2000
        if len(msg) > 1900:
            lines = msg.split('\n')
            chunk = ""
            for line in lines:
                if len(chunk) + len(line) > 1900:
                    await ctx.send(chunk)
                    chunk = line + "\n"
                else:
                    chunk += line + "\n"
            if chunk:
                await ctx.send(chunk)
        else:
            await ctx.send(msg)"""

new_list_command = r"""    @pubhelper_saveinst.command(name="list")
    async def pubhelper_saveinst_list(self, ctx: commands.Context) -> None:
        ""\"List all custom and base games configured for /saveinst.""\"
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
        
        embed.add_field(name="Stats", value=f"**Custom Games:** {len(custom_games)}\n**Base ColdClient:** {cc_count}\n**Base SEGA:** {sega_count}", inline=False)
        
        await ctx.send(embed=embed, view=view)"""

content = content.replace(old_list_command, new_list_command)

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done")
