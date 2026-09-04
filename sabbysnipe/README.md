# SabbySnipe

Ultra-fast, persistent SQLite WAL-backed message sniping engine for Red-DiscordBot.

## Features

- **Persistent SQLite WAL Database**: Deleted and edited messages persist across bot restarts, crashes, and reloads.
- **Zero-Lag Event Pipeline**: Event listeners evaluate checks via in-memory sets in `O(1)` time (0 ms) with zero disk blocking.
- **Ultra-Low RAM Footprint**: Stores only primitive values and releases Discord.py objects (`Member`, `Guild`, `Message`) immediately to garbage collection.
- **Modern Interactive UI**: Browse multiple deleted or edited messages using interactive Discord button views (`discord.ui.View`).
- **Deep Historical Search**: Full-text keyword search across deleted and edited message histories.
- **Full Revision History**: View multi-step edit history for any message ID.

## Commands

- `[p]snipe [channel] [index]` : Snipe a deleted message from the current or specified channel.
- `[p]snipe member [channel] <user>` : Snipe deleted messages by a specific user.
- `[p]snipe bulk [channel]` : Interactively browse recent deleted messages with pagination buttons.
- `[p]snipe search <keyword> [channel]` : Search deleted messages by content text.
- `[p]snipe clear [channel]` : Clear snipe history for a channel.
- `[p]esnipe [channel] [index]` : Snipe an edited message showing before and after content.
- `[p]esnipe member [channel] <user>` : Snipe edited messages by a specific user.
- `[p]esnipe bulk [channel]` : Interactively browse recent edited messages with pagination buttons.
- `[p]esnipe search <keyword> [channel]` : Search edited messages by text content.
- `[p]esnipe history <message_id>` : Show complete multi-revision edit history for a message.
- `[p]setsnipe stats` : View database and RAM cache performance statistics.
- `[p]setsnipe toggle` : Enable or disable tracking in the server.
- `[p]setsnipe ignorechannel <channel>` : Ignore or unignore a channel.
- `[p]setsnipe ignorerole <role>` : Ignore or unignore messages from members with a role.
- `[p]setsnipe retention <days>` : Configure data retention in days (0 = unlimited).
