import re

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the specific malformed string
old_string = '''        embed.add_field(name="Stats", value=f"**Custom Games:** {len(custom_games)}
**Base ColdClient:** {cc_count}
**Base SEGA:** {sega_count}", inline=False)'''

new_string = r'        embed.add_field(name="Stats", value=f"**Custom Games:** {len(custom_games)}\n**Base ColdClient:** {cc_count}\n**Base SEGA:** {sega_count}", inline=False)'

content = content.replace(old_string, new_string)

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed syntax error")
