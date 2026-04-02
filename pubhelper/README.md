# PubHelper

RE9 config combiner utility for Red-DiscordBot. Combines user configs with basefiles to create ready-to-use packages.

## Installation

```
[p]repo add sablinova https://github.com/Sablinova/Sablinova-Cogs
[p]cog install sablinova pubhelper
[p]load pubhelper
```

## Setup (Bot Owner)

1. Set the basefiles template:
```
[p]pubhelper setbasefiles <url to basefiles.7z>
```

2. Enable and sync the slash command:
```
[p]slash enable re9cc
[p]slash sync
```

3. Check status:
```
[p]pubhelper status
```

## Usage

Users can use the `/re9cc` slash command:

```
/re9cc url:<link to skin zip>
```

The bot will:
1. Download the user's skin zip
2. Extract `configs.user.ini` from it
3. Inject it into the basefiles template
4. Upload the combined package as a zip

## Commands

### Owner Commands

| Command | Description |
|---------|-------------|
| `[p]pubhelper setbasefiles <url>` | Set the basefiles 7z template |
| `[p]pubhelper status` | Check if basefiles are configured |

### Slash Commands

| Command | Description |
|---------|-------------|
| `/re9cc <url>` | Combine your config with RE9 basefiles |

## Requirements

- `py7zr` - For extracting 7z basefiles
- `aiohttp` - For downloading files

These are automatically installed when you install the cog.
