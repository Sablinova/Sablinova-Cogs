import re

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_logic = """        # Look up game data in custom config first
        custom_games = await self.config.custom_saveinst()
        matched_custom_key = None
        
        # 1. Exact custom keyword match
        if game_name in custom_games:
            matched_custom_key = game_name
        else:
            # 2. Exact custom Display Name match
            for k, data in custom_games.items():
                if game_name == data["name"].lower():
                    matched_custom_key = k
                    break
            
            # 3. Fuzzy custom match
            if not matched_custom_key:
                for k, data in custom_games.items():
                    name_lower = data["name"].lower()
                    if k in game_name or (len(game_name) >= 3 and game_name in k):
                        matched_custom_key = k
                        break
                    elif name_lower in game_name or (len(game_name) >= 3 and game_name in name_lower):
                        matched_custom_key = k
                        break

        if matched_custom_key:
            data = custom_games[matched_custom_key]
            if data["type"] == "custom":
                message = data["custom_text"].format(name=data["name"], keyword=matched_custom_key)
            elif data["type"] == "sega":
                message = SAVE_INSTRUCTIONS_SEGA.format(game_name=data["name"], game_folder=data["config_folder"])
            else:
                message = SAVE_INSTRUCTIONS.format(steam_id=data.get("steam_id", ""), config_folder=data.get("config_folder", ""))

            if data.get("attach_image", False):
                custom_image_url = data.get("custom_image_url", "")

                if custom_image_url:
                    # Send with custom image URL (as an embed so it embeds natively)
                    embed = discord.Embed(
                        description=message, color=discord.Color.blue()
                    )
                    embed.set_image(url=custom_image_url)
                    return await interaction.response.send_message(embed=embed)
                else:
                    # Send with default local image
                    img_path = Path(__file__).parent / "save_instruction.png"
                    if img_path.exists():
                        file = discord.File(
                            str(img_path), filename="save_instruction.png"
                        )
                        return await interaction.response.send_message(
                            message, file=file
                        )

            # Send without image
            return await interaction.response.send_message(message)

        # Fallback: Look up game data in SAVE_PROFILES and SEGA_PROFILES (case-insensitive fuzzy match)
        matched_key = None
        is_sega = False
        
        for key, profile in SAVE_PROFILES.items():
            profile_name = profile["name"].lower()
            if game_name == profile_name or game_name == key.lower():
                matched_key = key
                break
            if len(game_name) >= 3 and (
                game_name in profile_name or profile_name in game_name or game_name in key.lower() or key.lower() in game_name
            ):
                matched_key = key
                break
                
        if not matched_key:
            for key, profile in SEGA_PROFILES.items():
                profile_name = profile["name"].lower()
                if game_name == profile_name or game_name == key.lower():
                    matched_key = key
                    is_sega = True
                    break
                if len(game_name) >= 3 and (
                    game_name in profile_name or profile_name in game_name or game_name in key.lower() or key.lower() in game_name
                ):
                    matched_key = key
                    is_sega = True
                    break

        if not matched_key:
            all_profiles = list(SAVE_PROFILES.values()) + list(SEGA_PROFILES.values())
            await interaction.response.send_message(
                f"❌ No save path data found matching: **{game_name}**.\\nAvailable profiles: {str(', '.join([p['name'] for p in all_profiles]))}",
                ephemeral=True,
            )
            return

        if is_sega:
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

new_logic = """        # Unified Game Matching System
        custom_games = await self.config.custom_saveinst()
        
        targets = []
        for k, data in custom_games.items():
            targets.append({"key": k, "name": data["name"].lower(), "type": "custom", "data": data, "original_key": k})
        for k, profile in SAVE_PROFILES.items():
            if k not in custom_games:
                targets.append({"key": k, "name": profile["name"].lower(), "type": "base", "data": profile, "original_key": k})
        for k, profile in SEGA_PROFILES.items():
            if k not in custom_games:
                targets.append({"key": k, "name": profile["name"].lower(), "type": "sega", "data": profile, "original_key": k})

        best_match = None

        # Phase 1: Exact Match
        for t in targets:
            if game_name == t["key"] or game_name == t["name"]:
                best_match = t
                break
        
        # Phase 2: Substring Match (Longest targets first to avoid hijacking e.g. "Like a Dragon" stealing "Like a Dragon Gaiden")
        if not best_match:
            targets_sorted_by_len = sorted(targets, key=lambda x: max(len(x["key"]), len(x["name"])), reverse=True)
            for t in targets_sorted_by_len:
                if t["key"] in game_name or t["name"] in game_name:
                    best_match = t
                    break
                if len(game_name) > 4 and (game_name in t["key"] or game_name in t["name"]):
                    best_match = t
                    break

        # Phase 3: Aggressive Word Scoring (Finds highest overlap of >=3 char words)
        if not best_match:
            game_words = set(w for w in game_name.replace(":", " ").replace("-", " ").split() if len(w) >= 3)
            best_score = 0
            for t in targets:
                t_words = set(w for w in t["key"].replace(":", " ").replace("-", " ").split() if len(w) >= 3) | set(w for w in t["name"].replace(":", " ").replace("-", " ").split() if len(w) >= 3)
                overlap = len(game_words & t_words)
                if overlap > best_score:
                    best_score = overlap
                    best_match = t

        if not best_match:
            all_profiles = list(SAVE_PROFILES.values()) + list(SEGA_PROFILES.values())
            await interaction.response.send_message(
                f"❌ No save path data found matching: **{game_name}**.\\nAvailable profiles: {str(', '.join([p['name'] for p in all_profiles]))}",
                ephemeral=True,
            )
            return

        # Output logic
        if best_match["type"] == "custom":
            data = best_match["data"]
            kw = best_match["original_key"]
            if data["type"] == "custom":
                message = data["custom_text"].format(name=data["name"], keyword=kw)
            elif data["type"] == "sega":
                message = SAVE_INSTRUCTIONS_SEGA.format(game_name=data["name"], game_folder=data.get("config_folder", ""))
            else:
                message = SAVE_INSTRUCTIONS.format(steam_id=data.get("steam_id", ""), config_folder=data.get("config_folder", ""))

            if data.get("attach_image", False):
                custom_image_url = data.get("custom_image_url", "")
                if custom_image_url:
                    embed = discord.Embed(description=message, color=discord.Color.blue())
                    embed.set_image(url=custom_image_url)
                    return await interaction.response.send_message(embed=embed)
                else:
                    img_path = Path(__file__).parent / "save_instruction.png"
                    if img_path.exists():
                        file = discord.File(str(img_path), filename="save_instruction.png")
                        return await interaction.response.send_message(message, file=file)
            return await interaction.response.send_message(message)

        elif best_match["type"] == "sega":
            profile = best_match["data"]
            message = SAVE_INSTRUCTIONS_SEGA.format(game_name=profile["name"], game_folder=profile["game_folder"])
            await interaction.response.send_message(message)

        else:
            profile = best_match["data"]
            message = SAVE_INSTRUCTIONS.format(steam_id=profile["steam_id"], config_folder=profile["config_folder"])
            img_path = Path(__file__).parent / "save_instruction.png"
            if img_path.exists():
                file = discord.File(str(img_path), filename="save_instruction.png")
                await interaction.response.send_message(message, file=file)
            else:
                await interaction.response.send_message(message)"""

content = content.replace(old_logic, new_logic)

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Unified match logic injected")
