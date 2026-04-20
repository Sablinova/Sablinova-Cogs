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

# --- FIX 3: /saveinst slash command AND remove duplicate block ---

old_slash = """        if is_sega:
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
            await interaction.response.send_message(message)

        # Fallback: Look up game data in SAVE_PROFILES (case-insensitive fuzzy match)
        matched_key = None
        for key, profile in SAVE_PROFILES.items():
            profile_name = profile["name"].lower()
            if game_name == profile_name or game_name == key.lower():
                matched_key = key
                break

            # Fuzzy fallback (e.g. if channel says 'resident evil 9' but profile is 'Resident Evil 9 Requiem')
            if len(game_name) > 4 and (
                game_name in profile_name or profile_name in game_name
            ):
                matched_key = key
                break

        if not matched_key:
            await interaction.response.send_message(
                f"❌ No save path data found matching: **{game_name}**.\\nAvailable profiles: {', '.join([p['name'] for p in SAVE_PROFILES.values()])}",
                ephemeral=True,
            )
            return

        data = SAVE_PROFILES[matched_key]
        steam_id = data.get("steam_id", "")
        config_folder = data.get("config_folder", "")

        if not steam_id or not config_folder:
            await interaction.response.send_message(
                f"❌ Instructions for **{data['name']}** are missing steam_id or config_folder data.",
                ephemeral=True,
            )
            return

        message = SAVE_INSTRUCTIONS.format(
            steam_id=steam_id, config_folder=config_folder
        )

        img_path = Path(__file__).parent / "save_instruction.png"
        if img_path.exists():
            file = discord.File(str(img_path), filename="save_instruction.png")
            await interaction.response.send_message(message, file=file)
        else:
            await interaction.response.send_message(message)"""

new_slash = """        if is_sega:
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

content = content.replace(old_slash, new_slash)

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Images removed and duplicate code pruned")
