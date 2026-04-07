# SabMuteMessage

Send customizable messages to a designated channel when members are muted.

## Features

- Detects Discord timeouts and mute role assignments
- Multiple message templates with random selection
- Template variables: `$username`, `$server`, `$duration`, `$moderator`, `$reason`
- Optional image attachment
- Audit log integration for moderator and reason tracking

## Installation

```
[p]repo add sablinova https://github.com/Sablinova/Sablinova-Cogs
[p]cog install sablinova sabmutemessage
[p]load sabmutemessage
```

## Configuration

### Set the channel for mute messages

```
[p]sabmutemessage channel #mute-log
```

### Configure detection methods

**Discord timeouts (recommended):**
```
[p]sabmutemessage detecttimeout true
```

**Mute role assignment:**
```
[p]sabmutemessage muterole @Muted
[p]sabmutemessage detectrole true
```

### Add message templates

```
[p]sabmutemessage addmessage $username was muted for $duration by $moderator. Reason: $reason
```

**Available variables:**
- `$username` - The muted user's name
- `$server` - The server name
- `$duration` - How long the mute is for (e.g., "1 hour", "indefinitely")
- `$moderator` - Who issued the mute (requires View Audit Log permission)
- `$reason` - The mute reason (requires View Audit Log permission)

### Add an image (optional)

Attach an image to your command:
```
[p]sabmutemessage setimage
```

### View settings

```
[p]sabmutemessage settings
```

## Commands

| Command | Description |
|---------|-------------|
| `[p]sabmutemessage channel [#channel]` | Set channel for mute messages (empty = disable) |
| `[p]sabmutemessage muterole [@role]` | Set the mute role to detect (empty = disable) |
| `[p]sabmutemessage detecttimeout [true/false]` | Toggle timeout detection |
| `[p]sabmutemessage detectrole [true/false]` | Toggle mute role detection |
| `[p]sabmutemessage addmessage <text>` | Add a message template |
| `[p]sabmutemessage removemessage` | Interactive removal of templates |
| `[p]sabmutemessage listmessages` | List all templates |
| `[p]sabmutemessage setimage` | Attach image to mute messages |
| `[p]sabmutemessage unsetimage` | Remove the image |
| `[p]sabmutemessage settings` | Show current settings |

Alias: `[p]sabmm`

## Permissions

The bot needs the following permissions for full functionality:

- **Send Messages** - In the configured channel
- **Attach Files** - If using image attachment
- **View Audit Log** - For moderator and reason tracking

## Example Setup

```
[p]sabmutemessage channel #mod-log
[p]sabmutemessage detecttimeout true
[p]sabmutemessage addmessage **$username** was muted for **$duration** by $moderator. Reason: $reason
[p]sabmutemessage addmessage $username got muted 🤫 Duration: $duration
```

Now when someone gets muted, one of these messages will be randomly selected and sent to #mod-log.

## Notes

- Requires Red-DiscordBot v3.5.0 or higher
- Requires `humanize` package (auto-installed via Red's Downloader)
- Mute detection requires `Intents.members` (enabled by default in Red)
- If the bot doesn't have View Audit Log permission, `$moderator` will show "Unknown" and `$reason` will show "No reason provided"

## Support

For issues or feature requests, visit the [GitHub repository](https://github.com/Sablinova/Sablinova-Cogs/issues).
