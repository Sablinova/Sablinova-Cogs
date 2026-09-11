# GameDL (SteamRIP Scraper Cog)

A Red-DiscordBot cog to quickly search [SteamRIP](https://steamrip.com/) and extract direct game download links, versions, file sizes, and game cover art directly into Discord.

## Features

- **Direct Download Links**: Extracts hosts like Gofile, Buzzheavier (BZZHR), MegaDB, 1Fichier, FileDitch, and more.
- **Interactive UI**: Generates Discord link buttons and markdown hyperlinks for one-click downloading.
- **Metadata**: Shows game version, file size, thumbnail cover, and direct SteamRIP page link.
- **Multiple Matches**: Lists other relevant search matches when available.
- **Prefix & Slash Support**: Hybrid command supporting both `-gamedl <game>` and `/gamedl <game>`.
- **Zero Extra Dependencies**: Uses Python standard libraries (`urllib`, `re`, `asyncio`).

## Commands

- `[p]gamedl <game name>` - Search SteamRIP for a PC game and extract links (alias: `[p]steamrip`).
- `/gamedl <game name>` - Slash command version.
