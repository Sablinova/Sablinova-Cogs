# SabHoneypot

A [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) v3 cog that creates a decoy trap channel to automatically detect and act on self-bots and scammers.

When a user posts in the honeypot channel, their message is deleted and a configurable moderation action is taken. All incidents are logged to a designated channel and recorded in Red's modlog.

## Installation

```
[p]repo add sablinova-cogs https://github.com/Sablinova/Sablinova-Cogs
[p]cog install sablinova-cogs sabhoneypot
[p]load sabhoneypot
```

## Quick Start

```
[p]sabhoneypot createchannel
[p]sabhoneypot logchannel #mod-logs
[p]sabhoneypot action ban
[p]sabhoneypot enable
```

## Actions

| Action | Behavior |
|--------|----------|
| `mute` | Assigns a configured mute role to the user. Requires `[p]sabhoneypot muterole` to be set. |
| `kick` | Softbans the user (ban then immediate unban) to kick them and delete recent messages. |
| `ban` | Permanently bans the user and deletes recent messages. |
| `none` | Log-only. The message is deleted and the incident is logged, but no further action is taken. |

## Commands

All commands are under `[p]sabhoneypot` and require `manage_guild` permission.

### Channel Management

| Command | Description |
|---------|-------------|
| `createchannel` | Create a new honeypot trap channel at the top of the server. |
| `choosechannel <channel>` | Designate an existing channel as the honeypot trap. |
| `deletechannel` | Delete the honeypot channel and disable the trap. |

### Toggle

| Command | Description |
|---------|-------------|
| `enable` | Enable the honeypot trap. Requires a honeypot channel and log channel. |
| `disable` | Disable the honeypot trap. |

### Configuration

| Command | Description |
|---------|-------------|
| `action <action>` | Set the action on detection: `mute`, `kick`, `ban`, or `none`. |
| `logchannel <channel>` | Set the channel for incident log embeds. |
| `pingrole [role]` | Set a role to ping on detection. Omit to clear. |
| `muterole <role>` | Set the role to assign when the action is `mute`. |
| `warningtext <text>` | Set custom warning text for the honeypot channel embed. |
| `warningimage [url]` | Set an image URL for the warning embed. Omit to clear. |
| `kickdeletedays <days>` | Days of message history to delete on kick/softban (0-7). |
| `bandeletedays <days>` | Days of message history to delete on ban (0-7). |

### Utility

| Command | Description |
|---------|-------------|
| `refresh` | Update the warning message in the honeypot channel with current settings. |
| `settings` | Display all current honeypot settings. |
| `migrate` | Import settings from AAA3A's Honeypot cog for the current server. |

## Required Bot Permissions

| Permission | Used For |
|------------|----------|
| `manage_channels` | Creating and deleting the honeypot channel |
| `manage_messages` | Deleting trapped messages |
| `ban_members` | Kick (softban) and ban actions |
| `manage_roles` | Mute action (adding the mute role) |

## How It Works

1. The cog monitors the designated honeypot channel for any messages.
2. Messages from exempt users (bot owners, mods, admins, users with `manage_guild`, or users with a role higher than the bot) are ignored.
3. All other messages are immediately deleted.
4. The configured action (mute/kick/ban/none) is executed.
5. An incident embed is sent to the log channel with user info, message content, and action taken.
6. A modlog case is created for the detection.
7. If a ping role is configured, it is mentioned alongside the log embed.

## Migrating from AAA3A's Honeypot

If you previously used [AAA3A's Honeypot](https://github.com/AAA3A-AAA3A/AAA3A-cogs) cog, SabHoneypot can import your existing per-server configuration:

1. Install and load SabHoneypot.
2. Run `[p]sabhoneypot migrate` in each server that had the old cog configured.
3. Review the migration preview embed. Channels or roles that no longer exist will be skipped.
4. Reply `yes` within 30 seconds to confirm.
5. Unload the old cog with `[p]unload honeypot` to avoid conflicts.

Migrated settings: enabled state, action, honeypot channel, log channel, ping role, mute role, and message deletion days.

## Requirements

- Red-DiscordBot >= 3.5.6
- Python >= 3.9
- No additional pip dependencies
