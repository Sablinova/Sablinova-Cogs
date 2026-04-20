import re

with open('/tmp/Sablinova-Cogs/pubhelper/savesigner.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Swap SAVE_PROFILES
new_save_profiles = """SAVE_PROFILES = {
    "resident evil 9 requiem": {
        "name": "RE9",
        "profile": "Resident Evil 9 Requiem v1.bin",
        "steam_id": "3764200",
        "config_folder": "pub_re9/",
    },
    "dragon's dogma 2": {
        "name": "DD2",
        "profile": "Dragon's Dogma 2 v1.bin",
        "steam_id": "2054970",
        "config_folder": "",
    },
    "monster hunter wilds": {
        "name": "MHWilds",
        "profile": "Monster Hunter Wilds v1.bin",
        "steam_id": "2246340",
        "config_folder": "",
    },
    "kunitsu-gami path of the goddess": {
        "name": "Kunitsu",
        "profile": "Kunitsu-Gami Path of the Goddess v1.bin",
        "steam_id": "2510720",
        "config_folder": "",
    },
    "dead rising deluxe remaster": {
        "name": "DeadRising",
        "profile": "Dead Rising Deluxe Remaster v1.bin",
        "steam_id": "2531360",
        "config_folder": "",
    },
    "monster hunter stories 3 twisted reflection": {
        "name": "MHStories3",
        "profile": "Monster Hunter Stories 3 Twisted Reflection v1.bin",
        "steam_id": "2498260",  # Fallback value, real ID wasn't originally included
        "config_folder": "pub_mhs3/",
    },
    "mega man star force legacy collection": {
        "name": "MegaMan",
        "profile": "Mega Man Star Force Legacy Collection v1.bin",
        "steam_id": "2816910",  # Fallback value, real ID wasn't originally included
        "config_folder": "",
    },
    "pragmata": {
        "name": "Pragmata",
        "profile": "PRAGMATA_v1.bin",
        "steam_id": "3357650",
        "config_folder": "pub_pragmata/",
    },
}"""

# Extract the old SAVE_PROFILES up to SEGA_PROFILES
pattern = re.compile(r'SAVE_PROFILES = \{.*?\n\}\n\n\nSAVE_INSTRUCTIONS_SEGA', re.DOTALL)
content = pattern.sub(new_save_profiles + '\n\n\nSAVE_INSTRUCTIONS_SEGA', content)


# Swap SEGA_PROFILES
new_sega_profiles = """SEGA_PROFILES = {
    "persona 3 reload": {
        "name": "P3R",
        "game_folder": "P3R",
    },
    "like a dragon: infinite wealth": {
        "name": "IW",
        "game_folder": "YakuzaLikeADragon8",
    },
    "like a dragon gaiden: the man who erased his name": {
        "name": "Gaiden",
        "game_folder": "LikeADragonGaiden",
    },
    "yakuza kiwami 3 & dark ties": {
        "name": "K3",
        "game_folder": "YakuzaKiwami3",
    },
    "like a dragon: pirate yakuza in hawaii": {
        "name": "Pirate",
        "game_folder": "LikeADragonPirateYakuza",
    },
    "sonic x shadow generations": {
        "name": "SSG",
        "game_folder": "SonicXShadowGenerations",
    },
    "metaphor: refantazio": {
        "name": "Metaphor",
        "game_folder": "Metaphor"
    },
}"""

pattern2 = re.compile(r'SEGA_PROFILES = \{.*?\n\}\n\nclass SaveSigner:', re.DOTALL)
content = pattern2.sub(new_sega_profiles + '\n\nclass SaveSigner:', content)

with open('/tmp/Sablinova-Cogs/pubhelper/savesigner.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done")
