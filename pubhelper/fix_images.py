import re

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'r', encoding='utf-8') as f:
    content = f.read()

# --- FIX 1: SaveInstListView.select_callback ---
content = content.replace(
"""        elif cat == "sega":
            data = self.sega_profiles[kw]
            config_info = f"**Type:** Base `SEGA`\\n**Keyword:** `{kw}`\\n**Name:** {data['name']}\\n**Game Folder:** `{data.get('game_folder', '')}`\\n"
            message = SAVE_INSTRUCTIONS_SEGA.format(game_name=data["name"], game_folder=data.get("game_folder", ""))
            img_path = Path(__file__).parent / "save_instruction.png"
            
        embed.title = f"Preview: {data['name']}"
        embed.description = f"{config_info}\\n**--- Preview ---**\\n\\n{message}"
        
        kwargs = {"embed": embed, "ephemeral": False}
        
        if image_url:
            embed.set_image(url=image_url)
        elif img_path and img_path.exists():""",
"""        elif cat == "sega":
            data = self.sega_profiles[kw]
            config_info = f"**Type:** Base `SEGA`\\n**Keyword:** `{kw}`\\n**Name:** {data['name']}\\n**Game Folder:** `{data.get('game_folder', '')}`\\n"
            message = SAVE_INSTRUCTIONS_SEGA.format(game_name=data["name"], game_folder=data.get("game_folder", ""))
            img_path = None
            
        embed.title = f"Preview: {data['name']}"
        embed.description = f"{config_info}\\n**--- Preview ---**\\n\\n{message}"
        
        kwargs = {"embed": embed, "ephemeral": False}
        
        if image_url:
            embed.set_image(url=image_url)
        elif img_path and img_path.exists() and not (cat == "custom" and data.get("type") == "sega"):"""
)

# --- FIX 2: saveinst test command ---
content = content.replace(
"""        if matched_key:
            if is_sega:
                profile = SEGA_PROFILES[matched_key]
                message = SAVE_INSTRUCTIONS_SEGA.format(
                    game_name=profile["name"], game_folder=profile["game_folder"]
                )
            else:
                profile = SAVE_PROFILES[matched_key]
                message = SAVE_INSTRUCTIONS.format(
                    steam_id=profile["steam_id"], config_folder=profile["config_folder"]
                )
            img_path = Path(__file__).parent / "save_instruction.png"
            if img_path.exists():
                file = discord.File(str(img_path), filename="save_instruction.png")
                await ctx.send(message, file=file)
            else:
                await ctx.send(f"{message}\\n\\n*(Default image `save_instruction.png` not found!)*")""",
"""        if matched_key:
            if is_sega:
                profile = SEGA_PROFILES[matched_key]
                message = SAVE_INSTRUCTIONS_SEGA.format(
                    game_name=profile["name"], game_folder=profile["game_folder"]
                )
                await ctx.send(message)
            else:
                profile = SAVE_PROFILES[matched_key]
                message = SAVE_INSTRUCTIONS.format(
                    steam_id=profile["steam_id"], config_folder=profile["config_folder"]
                )
                img_path = Path(__file__).parent / "save_instruction.png"
                if img_path.exists():
                    file = discord.File(str(img_path), filename="save_instruction.png")
                    await ctx.send(message, file=file)
                else:
                    await ctx.send(f"{message}\\n\\n*(Default image `save_instruction.png` not found!)*")"""
)

# --- FIX 3: /saveinst slash command ---
content = content.replace(
"""        if is_sega:
            profile = SEGA_PROFILES[matched_key]
            message = SAVE_INSTRUCTIONS_SEGA.format(
                game_name=profile["name"], game_folder=profile["game_folder"]
            )
        else:
            profile = SAVE_PROFILES[matched_key]
            message = SAVE_INSTRUCTIONS.format(
                steam_id=profile["steam_id"], config_folder=profile["config_folder"]
            )

        img_path = Path(__file__).parent / "save_instruction.png"
        if img_path.exists():
            file = discord.File(str(img_path), filename="save_instruction.png")
            await interaction.response.send_message(message, file=file)
        else:
            await interaction.response.send_message(message)""",
"""        if is_sega:
            profile = SEGA_PROFILES[matched_key]
            message = SAVE_INSTRUCTIONS_SEGA.format(
                game_name=profile["name"], game_folder=profile["game_folder"]
            )
            await interaction.response.send_message(message)
        else:
            profile = SAVE_PROFILES[matched_key]
            message = SAVE_INSTRUCTIONS.format(
                steam_id=profile["steam_id"], config_folder=profile["config_folder"]
            )
            img_path = Path(__file__).parent / "save_instruction.png"
            if img_path.exists():
                file = discord.File(str(img_path), filename="save_instruction.png")
                await interaction.response.send_message(message, file=file)
            else:
                await interaction.response.send_message(message)"""
)

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Images removed from base sega")
