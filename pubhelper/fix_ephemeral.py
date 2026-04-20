import re

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Change ephemeral: True to ephemeral: False
content = content.replace('"ephemeral": True', '"ephemeral": False')

with open('/tmp/Sablinova-Cogs/pubhelper/pubhelper.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Changed to public (ephemeral=False)")
