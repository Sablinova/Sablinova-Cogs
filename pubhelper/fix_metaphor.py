import re

with open('/tmp/Sablinova-Cogs/pubhelper/savesigner.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace(
    '"metaphor": {\n        "name": "Metaphor: ReFantazio",\n        "game_folder": "Metaphor"\n    }',
    '"metaphor: refantazio": {\n        "name": "Metaphor",\n        "game_folder": "Metaphor"\n    }'
)

with open('/tmp/Sablinova-Cogs/pubhelper/savesigner.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done")
