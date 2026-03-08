# Sablinova-Cogs

Custom cogs for [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) v3.

## Installation

```
[p]load downloader
[p]repo add sablinova-cogs https://github.com/Sablinova/Sablinova-Cogs
[p]cog install sablinova-cogs <cog_name>
[p]load <cog_name>
```

## Cog Overview

| Cog | Description |
|-----|-------------|
| [SabHoneypot](#sabhoneypot) | Trap channel to catch self-bots and scammers with automatic moderation actions. |
| [Backup](#backup) | Export and import installed repositories and cogs for easy bot migration. |
| [RoleAll](#roleall) | Mass-assign a specific role to every member in the server. |
| [TidbStats](#tidbstats) | Auto-updating embed displaying live statistics from The Intro Database API. |
| [Sabby](#sabby) | Two-way bridge routing Red bot owner-mentions to a Sabby OpenClaw AI webhook. |

---

## SabHoneypot

Creates a decoy trap channel at the top of your server to automatically detect and act on self-bots and scammers. When a user posts in the honeypot channel, their message is deleted and a configurable action is taken. All incidents are logged to a designated channel and recorded in Red's modlog.

Migrating from AAA3A's Honeypot cog is supported via the built-in `migrate` command.

### Quick Start

```
[p]cog install sablinova-cogs honeypot
[p]load honeypot
[p]honeypot createchannel
[p]honeypot logchannel #mod-logs
[p]honeypot action ban
[p]honeypot enable
```

### Actions

| Action | Behavior |
|--------|----------|
| `mute` | Assigns a configured mute role to the user. Requires `[p]honeypot muterole` to be set. |
| `kick` | Softbans the user (ban then immediate unban) to kick them and delete recent messages. |
| `ban` | Permanently bans the user and deletes recent messages. |
| `none` | Log-only. The message is deleted and the incident is logged, but no further action is taken. |

### Command Reference

All commands are under the `[p]honeypot` group and require `manage_guild` permission.

| Command | Description |
|---------|-------------|
| `createchannel` | Create a new honeypot trap channel at the top of the server. |
| `choosechannel <channel>` | Designate an existing channel as the honeypot trap. |
| `deletechannel` | Delete the honeypot channel and disable the trap. |
| `enable` | Enable the honeypot trap. Requires a honeypot channel and log channel to be configured. |
| `disable` | Disable the honeypot trap. |
| `action <action>` | Set the action on detection: `mute`, `kick`, `ban`, or `none`. |
| `logchannel <channel>` | Set the channel for incident log embeds. |
| `pingrole [role]` | Set a role to ping on detection. Omit the role argument to clear. |
| `muterole <role>` | Set the role to assign when the action is `mute`. |
| `warningtext <text>` | Set custom warning text for the honeypot channel embed. |
| `warningimage [url]` | Set an image URL for the warning embed. Omit the URL to clear. |
| `kickdeletedays <days>` | Set days of message history to delete on kick/softban (0-7). |
| `bandeletedays <days>` | Set days of message history to delete on ban (0-7). |
| `refresh` | Update the warning message in the honeypot channel with current settings. |
| `settings` | Display all current honeypot settings. |
| `migrate` | Import settings from AAA3A's Honeypot cog for the current server. |

### Required Bot Permissions

The bot needs different permissions depending on which features are used:

- `manage_channels` -- creating and deleting the honeypot channel
- `manage_messages` -- deleting trapped messages
- `ban_members` -- kick (softban) and ban actions
- `manage_roles` -- mute action

### Migrating from AAA3A's Honeypot

If you previously used AAA3A's Honeypot cog, SabHoneypot can import your existing per-server configuration:

1. Install and load SabHoneypot.
2. Run `[p]honeypot migrate` in each server that had AAA3A's Honeypot configured.
3. Review the migration preview embed. Channels or roles that no longer exist will be skipped.
4. Reply `yes` within 30 seconds to confirm.
5. Unload the old cog with `[p]unload honeypot` to avoid conflicts.

Migrated settings include: enabled state, action, honeypot channel, log channel, ping role, mute role, and message deletion days.

---

## Backup

A utility to export and import your installed repositories and cogs, making bot migration straightforward. Exports a JSON file containing all repositories, branches, and installed cogs. On import, repositories are re-added and cogs are reinstalled automatically.

Authors: cswimr, Sablinova

### Commands

All commands require bot owner permissions.

| Command | Description |
|---------|-------------|
| `[p]backup export` | Export installed repositories and cogs to a JSON file. |
| `[p]backup import [url]` | Import from an attached JSON file, a replied message attachment, or a URL. |

---

## RoleAll

Mass-assign a specific role to every member in the server. Provides progress updates during execution and reports how many members were added, already had the role, or failed.

Requires administrator permission. The bot needs `manage_roles` and the target role must be below the bot's highest role.

### Commands

| Command | Description |
|---------|-------------|
| `[p]roleall <role>` | Give the specified role to all members in the server. |

---

## TidbStats

Displays live statistics from [The Intro Database](https://theintrodb.org/) (TIDB) API in an auto-updating embed. Shows accepted/unaccepted timestamps, total submissions, contributor count, database coverage, and top contributing media. The embed refreshes automatically every hour.

Requires `manage_guild` permission. The bot needs `send_messages` and `embed_links`.

### Commands

All commands are under the `[p]tidbstats` group.

| Command | Description |
|---------|-------------|
| `[p]tidbstats channel <channel>` | Set the channel for the stats embed and enable auto-updates. |
| `[p]tidbstats enable` | Enable auto-updates. |
| `[p]tidbstats disable` | Disable auto-updates. |
| `[p]tidbstats refresh` | Force an immediate stats refresh. |
| `[p]tidbstats logo <url>` | Set a custom logo URL for the embed thumbnail. |
| `[p]tidbstats tmdbkey <key>` | Set the TMDB API key for resolving media names. |

---

## Sabby

A two-way bridge between Red and Sabby (OpenClaw AI). Forwards owner mentions to an OpenClaw webhook and runs a local webserver to receive replies. This is a purpose-built integration and is unlikely to be useful outside of its intended deployment.

### Commands

All commands are under the `[p]sabbyconf` group.

| Command | Description |
|---------|-------------|
| `[p]sabbyconf port <port>` | Set the port for the incoming webserver. |
| `[p]sabbyconf url <url>` | Set the public URL that Sabby should reply to. |

---

## Requirements

- Red-DiscordBot >= 3.5.6
- Python >= 3.9

## Support

For bug reports and feature requests, open an issue on the [GitHub repository](https://github.com/Sablinova/Sablinova-Cogs/issues).
