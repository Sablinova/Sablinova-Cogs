# GameDL (SteamRIP, GameBounty, SteamUnderground & WorldOfPCGames Scraper Cog)

A Red-DiscordBot cog to quickly search [SteamRIP](https://steamrip.com/), [GameBounty](https://gamebounty.world/), [SteamUnderground](https://steamunderground.net/), and [WorldOfPCGames](https://worldofpcgames.com/) and extract direct game download links, versions, file sizes, and Steam portrait cover art directly into Discord.

## Features

- **Direct Download Links**: Extracts hosts like BZZHR (Buzzheavier direct CDN), Gofile, PixelDrain, MegaDB, DataNodes, FileKeeper, FileDitch, 1Fichier, FileQ, Torrent, and more.
- **Unified Meta-Search**: Concurrently searches across SteamRIP, GameBounty, SteamUnderground, and WorldOfPCGames, seamlessly merging download mirrors so you get all available links in one place.
- **Interactive UI**: Generates Discord link buttons and markdown hyperlinks for one-click downloading.
- **Steam AppID Support**: Directly search games by name or numeric Steam AppID (e.g. `2651280` or `105600`).
- **Metadata**: Shows game version, file size, thumbnail cover, and direct Steam store page link.
- **Multiple Matches**: Lists other relevant search matches when available.
- **Prefix & Slash Support**: Hybrid commands supporting both prefix commands and slash commands.
- **Zero Extra Dependencies**: Uses Python standard libraries (`urllib`, `re`, `asyncio`, `hmac`, `hashlib`).

## Commands

- `[p]gamedl <game name or AppID>` - Unified search across all supported sources with merged mirrors (aliases: `[p]gdl`, `[p]steamrip`).
- `/gamedl <game name or AppID>` - Slash command version.

