# PubHelper

Config combiner utility for Red-DiscordBot. Combines user token configs with basefiles to create ready-to-use packages with **per-game customizable instructions**.

## Features

- 🎮 **Multi-game support** - Built-in profiles for RE9, CD, and add custom games
- 📦 **Smart file combining** - Automatically injects user configs into basefiles
- 🖼️ **Per-game instructions** - Custom instructions text + image for each game
- 📊 **Usage logging** - Track slash command usage in a designated channel
- 🔄 **DLL updates** - Update steamclient64.dll across all basefiles at once
- 📁 **Archive support** - Both .7z and .zip formats supported
- 🎯 **Slash commands** - Easy-to-use `/gamecc` commands for users

## Installation

```
[p]repo add sablinova https://github.com/Sablinova/Sablinova-Cogs
[p]cog install sablinova pubhelper
[p]load pubhelper
```

## Quick Setup (Bot Owner)

### 1. Upload Basefiles

**Interactive:**
```
[p]pubhelper setup
```

**Direct:**
```
[p]pubhelper setbasefiles re9 <url to RE9 basefiles.7z>
[p]pubhelper setbasefiles cd <url to CD basefiles.7z>
```

### 2. Customize Instructions (Optional)

**Set game-specific instructions:**
```
[p]pubhelper setinstructions re9 1. Extract to {install_path}
2. Run the launcher
3. Enjoy!
```

**Set game-specific image (URL or attachment):**
```
[p]pubhelper setinstructionsimage re9 https://i.imgur.com/guide.png
```
Or attach an image:
```
[p]pubhelper setinstructionsimage re9
[attach image file]
```

**Set base/default (used when game has no custom instructions):**
```
[p]pubhelper setinstructions base <text>
[p]pubhelper setinstructionsimage base <url>
```

### 3. Enable Slash Commands

```
[p]slash enable re9cc
[p]slash enable cdcc
[p]slash sync
```

### 4. Check Status
```
[p]pubhelper status
```

## Usage (End Users)

Users run slash commands with their token zip URL:

```
/re9cc url:<link to token zip>
/cdcc url:<link to token zip>
```

The bot will:
1. Download the user's token zip
2. Extract `configs.user.ini` from it
3. Inject it into the basefiles template
4. Upload the combined package as a zip
5. Send custom installation instructions (if configured)

## Commands Reference

### Game Management

| Command | Description |
|---------|-------------|
| `[p]pubhelper setup` | Interactive basefiles setup wizard |
| `[p]pubhelper setbasefiles <game> <url>` | Set basefiles for a game |
| `[p]pubhelper addgame` | Add a new game profile (interactive) |
| `[p]pubhelper removegame <id>` | Remove a game profile |
| `[p]pubhelper status` | Check all games status & paths |
| `[p]pubhelper structure <game>` | Show basefiles directory tree |
| `[p]pubhelper pullbasefiles <game>` | Export basefiles archive via Discord |

### Configuration

| Command | Description |
|---------|-------------|
| `[p]pubhelper configpath <game> <path>` | Set config.user.ini target path |
| `[p]pubhelper setpath` | Interactive path change wizard |
| `[p]pubhelper updatedll` | Update steamclient64.dll in all basefiles |

### Instructions Customization

| Command | Description |
|---------|-------------|
| `[p]pubhelper setinstructions` | Show guide on per-game instructions |
| `[p]pubhelper setinstructions <game\|base> <text>` | Set instructions text |
| `[p]pubhelper setinstructions <game>` | View game's current instructions |
| `[p]pubhelper setinstructionsimage` | Show guide on per-game images |
| `[p]pubhelper setinstructionsimage <game\|base> <url>` | Set image from URL |
| `[p]pubhelper setinstructionsimage <game\|base>` + attachment | Set image from file |
| `[p]pubhelper setinstructionsimage <game>` | View game's current image |
| `[p]pubhelper setinstructionsimage <game> clear` | Remove custom image |

**Placeholders for instructions text:**
- `{game_name}` - Replaced with game's display name
- `{install_path}` - Replaced with game's install path

### Utilities

| Command | Description |
|---------|-------------|
| `[p]pubhelper syncslash` | Sync slash commands to Discord |
| `[p]pubhelper logchannel <#channel>` | Set command usage log channel |
| `[p]pubhelper logchannel clear` | Disable logging |
| `[p]pubhelper help` | Show detailed setup guide |

### Slash Commands (User-facing)

| Command | Description |
|---------|-------------|
| `/re9cc <url>` | Combine your config with RE9 basefiles |
| `/cdcc <url>` | Combine your config with CD basefiles |
| `/<game>cc <url>` | Combine with custom game basefiles |

## Adding New Games

1. Run the interactive wizard:
```
[p]pubhelper addgame
```

2. Follow the prompts to set:
   - Game ID (e.g., `mhw`)
   - Display name (e.g., `Monster Hunter Wilds`)
   - Description
   - Config target path
   - Install path

3. Reload the cog:
```
[p]reload pubhelper
```

4. Enable the new slash command:
```
[p]slash enable mhwcc
[p]slash sync ~
```

5. Upload basefiles:
```
[p]pubhelper setup
```

6. (Optional) Set custom instructions:
```
[p]pubhelper setinstructions mhw <your custom text>
[p]pubhelper setinstructionsimage mhw <url or attachment>
```

## Per-Game Instructions

Each game can have **custom instructions and images**. Games without custom instructions use the **base/default** as a fallback.

### Example Workflow:

**Set base instructions (used by all games without custom ones):**
```
[p]pubhelper setinstructions base 1. Extract to {install_path}
2. Run START_GAME.exe
3. Contact support if needed
```

**Set RE9-specific instructions:**
```
[p]pubhelper setinstructions re9 1. Extract to {install_path}
2. Run RE9_Launcher.exe as Admin
3. Join our Discord for updates
```

**Set CD-specific image:**
```
[p]pubhelper setinstructionsimage cd https://i.imgur.com/cd_guide.png
```

Now:
- `/re9cc` users get RE9 custom instructions
- `/cdcc` users get base instructions + CD custom image
- Any game without custom instructions gets base instructions

## Usage Logging

Track all slash command usage in a designated channel:

```
[p]pubhelper logchannel #bot-logs
```

**Logged information:**
- User who ran the command
- Game command used (`/re9cc`, `/cdcc`, etc.)
- Token URL provided
- Timestamp
- Success/failure status
- Output filename and size (on success)

## Requirements

- `py7zr` - For extracting 7z basefiles
- `aiohttp` - For downloading files

These are automatically installed when you install the cog.

## Support

For issues or questions:
- Check `[p]pubhelper help` in Discord
- View available commands: `[p]help pubhelper`
- View per-game instructions guide: `[p]pubhelper setinstructions`
