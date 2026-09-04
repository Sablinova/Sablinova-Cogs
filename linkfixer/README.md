# LinkFixer

Automatically fixes social media links (Twitter/X, TikTok, Instagram, Reddit, Pixiv, Threads, Bilibili) so they embed videos and media cleanly in Discord.

## Features

- **Automatic Link Fixing**: Detects regular social links and replies with rich embedding alternatives (fxtwitter, vxreddit, d.oginstagram, tnktok, phixiv, etc.).
- **Per-Channel Toggles**: Enable or disable LinkFixer per channel, allowing LinkFixer to run only in specific channels (e.g. #media or #memes) while keeping other channels clean.
- **Server-Wide Control**: Enable or disable globally for the entire server.
- **Selective Link Fixers**: Toggle specific platforms on or off.
- **Translation Support**: Automatically requests English translations for supported embedders like fxtwitter.
- **Zero Bot Slowdown**: Fast in-memory channel and guild state tracking for instant message processing.

## Commands

### Server-Wide Commands
- `[p]linkfixer enable` : Enables LinkFixer server-wide.
- `[p]linkfixer disable` : Disables LinkFixer server-wide (per-channel enabled channels still function).

### Per-Channel Commands
- `[p]linkfixer channel enable [channel]` : Enables LinkFixer in a specific channel (runs even if server-wide is disabled).
- `[p]linkfixer channel disable [channel]` : Disables LinkFixer in a specific channel (even if server-wide is enabled).
- `[p]linkfixer channel reset [channel]` : Clears the channel override so it follows the server setting.
- `[p]linkfixer channel list` : Displays all channels with explicit enable or disable overrides.

### Platform and Translation Commands
- `[p]linkfixer links list` : Lists all supported platforms and their active status.
- `[p]linkfixer links enable <platform>` : Enables a specific platform fixer (e.g. `fxtwitter`).
- `[p]linkfixer links disable <platform>` : Disables a specific platform fixer.
- `[p]linkfixer translate enable` : Enables automatic English translation on compatible links.
- `[p]linkfixer translate disable` : Disables automatic English translation.
