with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, line in enumerate(lines):
    if "# Fallback: Look up game data in SAVE_PROFILES" in line:
        skip = True
    if skip and "@app_commands.command(" in line:
        skip = False
        
    if not skip:
        new_lines.append(line)
        
with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("Removed duplicated fallback block")
