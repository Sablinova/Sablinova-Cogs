import discord
from discord.ui import View, Select
import asyncio

class SaveInstListView(discord.ui.View):
    def __init__(self, cog, ctx, custom_games, save_profiles, sega_profiles):
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        self.custom_games = custom_games
        self.save_profiles = save_profiles
        self.sega_profiles = sega_profiles
        
        self.build_selects()

    def build_selects(self):
        # We need to create up to 5 select menus (max 25 options each)
        options = []
        
        # 1. Custom Games
        for kw, data in self.custom_games.items():
            options.append(discord.SelectOption(
                label=data['name'][:100], 
                description=f"Custom ({data.get('type', 'Unknown')}) - kw: {kw}"[:100],
                value=f"custom:{kw}"
            ))
            
        # 2. ColdClient Games
        for kw, profile in self.save_profiles.items():
            if kw not in self.custom_games:  # Don't show base if overridden
                options.append(discord.SelectOption(
                    label=profile['name'][:100],
                    description=f"ColdClient - kw: {kw}"[:100],
                    value=f"base:{kw}"
                ))
                
        # 3. SEGA Games
        for kw, profile in self.sega_profiles.items():
            if kw not in self.custom_games:
                options.append(discord.SelectOption(
                    label=profile['name'][:100],
                    description=f"SEGA - kw: {kw}"[:100],
                    value=f"sega:{kw}"
                ))
        
        # Batch options into selects of 25
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
        import os
        from pathlib import Path
        
        attach_file = None
        message = ""
        config_info = ""
        image_url = ""
        img_path = None
        
        if cat == "custom":
            data = self.custom_games[kw]
            config_info = f"**Type:** Custom `{data.get('type', 'custom')}`\n**Keyword:** `{kw}`\n**Name:** {data['name']}\n"
            
            if data["type"] == "custom":
                message = data["custom_text"].format(name=data["name"], keyword=kw)
            elif data["type"] == "sega":
                message = SAVE_INSTRUCTIONS_SEGA.format(game_name=data["name"], game_folder=data["config_folder"])
                config_info += f"**Game Folder:** `{data['config_folder']}`\n"
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
            message = SAVE_INSTRUCTIONS_SEGA.format(game_name=data["name"], game_folder=data["game_folder"])
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

