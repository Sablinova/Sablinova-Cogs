# GameDL (SteamRIP & GameBounty Scraper Cog)

A Red-DiscordBot cog to quickly search [SteamRIP](https://steamrip.com/) and [GameBounty](https://gamebounty.world/) and extract direct game download links, versions, file sizes, and Steam portrait cover art directly into Discord.

## Features

- **Direct Download Links**: Extracts hosts like BZZHR (Buzzheavier direct CDN), Gofile, PixelDrain, MegaDB, FileDitch, 1Fichier, FileQ, DataNodes, and more.
- **Unified Meta-Search**: Concurrently searches SteamRIP and GameBounty, seamlessly merging download mirrors so you get all available links in one place.
- **Interactive UI**: Generates Discord link buttons and markdown hyperlinks for one-click downloading.
- **Steam AppID Support**: Directly search games by name or numeric Steam AppID (e.g. `2651280` or `105600`).
- **Metadata**: Shows game version, file size, thumbnail cover, and direct Steam store page link.
- **Multiple Matches**: Lists other relevant search matches when available.
- **Prefix & Slash Support**: Hybrid commands supporting both prefix commands and slash commands.
- **Zero Extra Dependencies**: Uses Python standard libraries (`urllib`, `re`, `asyncio`, `hmac`, `hashlib`).

## Commands

- `[p]gamedl <game name or AppID>` - Unified search across SteamRIP & GameBounty with merged mirrors (alias: `[p]gdl`).
- `[p]gamebounty <game name or AppID>` - Search GameBounty specifically (alias: `[p]gb`).
- `[p]steamrip <game name or AppID>` - Search SteamRIP specifically (alias: `[p]srip`).
- `/gamedl <game name or AppID>` - Slash command version.
