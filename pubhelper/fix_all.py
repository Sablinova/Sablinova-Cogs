import json

with open('/tmp/Sablinova-Cogs/pubhelper/savesigner.py', 'r', encoding='utf-8') as f:
    content = f.read()

import ast

def swap_dict(dict_str):
    tree = ast.parse(dict_str)
    # this is too complex to write a quick ast parser, let's just do regex or manual
    pass

