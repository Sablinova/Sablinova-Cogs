# PubHelper

Config combiner utility for Red-DiscordBot. Combines user token configs with basefiles to create ready-to-use packages.

## Installation

```
[p]repo add sablinova https://github.com/Sablinova/Sablinova-Cogs
[p]cog install sablinova pubhelper
[p]load pubhelper
```

## Setup (Bot Owner)

1. Set the basefiles templates for each game:
```
[p]pubhelper setbasefiles re9 <url to RE9 basefiles.7z>
[p]pubhelper setbasefiles cd <url to CD basefiles.7z>
```

2. Enable and sync the slash commands:
```
[p]slash enable re9cc
[p]slash enable cdcc
[p]slash sync
```

3. Check status:
```
[p]pubhelper status
```

## Usage

Users can use the slash commands:

```
/re9cc url:<link to token zip>
/cdcc url:<link to token zip>
```

The bot will:
1. Download the user's token zip
2. Extract `configs.user.ini` from it
3. Inject it into the basefiles template
4. Upload the combined package as a zip

## Commands

### Owner Commands

| Command | Description |
|---------|-------------|
| `[p]pubhelper setbasefiles <game> <url>` | Set basefiles for a game (re9, cd) |
| `[p]pubhelper status` | Check status of all basefiles |

### Slash Commands

| Command | Description |
|---------|-------------|
| `/re9cc <url>` | Combine your config with RE9 basefiles |
| `/cdcc <url>` | Combine your config with CD basefiles |

## Requirements

- `py7zr` - For extracting 7z basefiles
- `aiohttp` - For downloading files

These are automatically installed when you install the cog.
