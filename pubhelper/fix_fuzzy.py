import re

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace custom game fuzzy logic in saveinst slash command
old_slash_fuzzy = """            # 3. Fuzzy custom match
            if not matched_custom_key:
                for k, data in custom_games.items():
                    name_lower = data["name"].lower()
                    if k in game_name or (len(game_name) >= 3 and game_name in k):
                        matched_custom_key = k
                        break
                    elif name_lower in game_name or (len(game_name) >= 3 and game_name in name_lower):
                        matched_custom_key = k
                        break
                    else:
                        # Check if any significant word (>=4 chars) from the keyword or name is in the channel name
                        k_words = [w for w in k.replace(":", " ").split() if len(w) >= 4]
                        name_words = [w for w in name_lower.replace(":", " ").split() if len(w) >= 4]
                        if any(w in game_name for w in k_words) or any(w in game_name for w in name_words):
                            matched_custom_key = k
                            break"""

new_slash_fuzzy = """            # 3. Fuzzy custom match
            if not matched_custom_key:
                for k, data in custom_games.items():
                    name_lower = data["name"].lower()
                    if k in game_name or (len(game_name) >= 3 and game_name in k):
                        matched_custom_key = k
                        break
                    elif name_lower in game_name or (len(game_name) >= 3 and game_name in name_lower):
                        matched_custom_key = k
                        break"""

content = content.replace(old_slash_fuzzy, new_slash_fuzzy)


# We also need to fix the base games fuzzy logic. Right now it is:
# if len(game_name) >= 3 and (game_name in profile_name or profile_name in game_name or game_name in key.lower() or key.lower() in game_name):
# But `key` is now the full name! Wait, the keys are now the full names like "like a dragon gaiden: the man who erased his name".
# And the `name` is the short one like "Gaiden".
# So `key.lower()` is the long name, `profile_name` is the short name.

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed fuzzy")
