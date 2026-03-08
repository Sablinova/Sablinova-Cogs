# SabHoneypot

A [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) v3 cog that creates a decoy trap channel to automatically detect and act on self-bots and scammers.

When a user posts in the honeypot channel, their message is deleted and a configurable moderation action is taken. All incidents are logged to a designated channel and recorded in Red's modlog.

## Installation

```
[p]repo add sablinova https://github.com/Sablinova/Sablinova-Cogs
[p]cog install sablinova sabhoneypot
[p]load sabhoneypot
```

## Getting Started

### Automated Setup (Recommended for Beginners)

Run the interactive setup wizard and follow the prompts:

```
[p]sabhoneypot setup
```

The wizard walks you through each step:

1. **Honeypot Channel** -- Type `create` to make a new trap channel, or mention an existing one (e.g. `#honeypot`).
2. **Log Channel** -- Mention the channel where detection alerts should go (e.g. `#mod-logs`).
3. **Action** -- Choose what happens when someone gets caught: `ban`, `kick`, `mute`, or `none`.
4. **Mute Role** -- If you chose `mute`, mention the role to assign (e.g. `@Muted`). Otherwise this step is skipped.
5. **Ping Role** -- Optionally mention a role to ping on each detection (e.g. `@Moderator`), or type `skip`.
6. **Enable** -- Type `yes` to activate the trap immediately, or `no` to enable it later.

You can type `cancel` at any point to abort the setup.

### Manual Setup (Step by Step)

If you prefer to configure each setting individually:

1. **Create or choose a honeypot channel.**

   Create a new one (placed at the top of the server):
   ```
   [p]sabhoneypot createchannel
   ```
   Or designate an existing channel:
   ```
   [p]sabhoneypot choosechannel #channel-name
   ```

2. **Set the log channel** where detection alerts will be sent:
   ```
   [p]sabhoneypot logchannel #mod-logs
   ```

3. **Set the action** to take when someone is caught:
   ```
   [p]sabhoneypot action ban
   ```
   Options: `ban`, `kick`, `mute`, or `none` (log-only).

4. **If using mute**, set the mute role:
   ```
   [p]sabhoneypot muterole @Muted
   ```

5. **(Optional) Set a ping role** to notify staff on each detection:
   ```
   [p]sabhoneypot pingrole @Moderator
   ```

6. **Enable the trap:**
   ```
   [p]sabhoneypot enable
   ```

7. **Verify your settings:**
   ```
   [p]sabhoneypot settings
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

### Setup

| Command | Description |
|---------|-------------|
| `setup` | Interactive guided setup wizard. Walks you through full configuration step by step. |

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
| `migrateall` | **(Bot Owner)** Migrate AAA3A Honeypot settings for all servers at once. |

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

If you previously used [AAA3A's Honeypot](https://github.com/AAA3A-AAA3A/AAA3A-cogs) cog, SabHoneypot can import your existing configuration.

### Migrate All Servers at Once (Bot Owner)

The fastest way to migrate. Imports settings for every server the bot is in with one command:

1. Install and load SabHoneypot.
2. Run `[p]sabhoneypot migrateall` from any channel.
3. The bot will show how many servers have AAA3A Honeypot config. Reply `yes` to confirm.
4. A summary embed lists all migrated servers and any skipped channels/roles.
5. Unload the old cog with `[p]unload honeypot` to avoid conflicts.

### Migrate a Single Server

If you only want to migrate one server at a time:

1. Run `[p]sabhoneypot migrate` in the server you want to migrate.
2. Review the migration preview embed. Channels or roles that no longer exist will be skipped.
3. Reply `yes` within 30 seconds to confirm.
4. Unload the old cog with `[p]unload honeypot` to avoid conflicts.

Migrated settings: enabled state, action, honeypot channel, log channel, ping role, mute role, and message deletion days.

## Requirements

- Red-DiscordBot >= 3.5.6
- Python >= 3.9
- No additional pip dependencies
