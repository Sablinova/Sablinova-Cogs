import re

with open('/tmp/Sablinova-Cogs/pubhelper/savesigner.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_save = 'SAVE_INSTRUCTIONS = """```\\nSave File Instructions\\n\\n1. Press Win + R, paste the path below and hit Enter:\\n%USERPROFILE%\\\\AppData\\\\Roaming\\\\GSE Saves\\\\{steam_id}\\\\remote\\\\win64_save\\\\\\n\\n2. Send a .zip / .7z of the win64_save folder\\n\\n3. Send configs.user.ini — it can be found inside {config_folder}steam_settings\\n```"""'

content = re.sub(
    r'SAVE_INSTRUCTIONS = """.*?steam_settings`"""',
    new_save.replace('\\', '\\\\'),
    content,
    flags=re.DOTALL
)

with open('/tmp/Sablinova-Cogs/pubhelper/savesigner.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Replaced")
