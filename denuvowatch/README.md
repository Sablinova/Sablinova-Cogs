# DenuvoWatch

A Red-DiscordBot port of the standalone "Steam Monitor" bot. Watches a single
global watchlist of Steam games and posts an alert when:

- **Denuvo anti-tamper is removed** (or added)
- A game's **public build (depot)** is updated

## Setup

1. Load the cog: `[p]load denuvowatch`
2. Set the alert channel: `[p]denuvowatch channel #channel`
3. (Optional) Ping a user on build updates: `[p]denuvowatch pinguser @user`
4. Add games: `/dadd <name or AppID>`

The cog auto-installs `beautifulsoup4` and `aiohttp` via Downloader.

## Commands

| Command | Who | Description |
| --- | --- | --- |
| `/dadd <name or AppID>` | admin | Add a game (searches Steam if you pass a name) |
| `/dremove <name or AppID>` | admin | Remove a game from the watchlist |
| `/dlist` | anyone | Show all watched games with current Denuvo/build |
| `/dcheck <name or AppID>` | anyone | Instantly check any game's current status |
| `/dforcecheck` | admin | Manually trigger a full watchlist scan |
| `/dstatus` | anyone | Watchlist size + next scheduled check |
| `[p]denuvowatch channel <channel>` | admin | Set alert channel |
| `[p]denuvowatch mention [@user or @role]` | admin | Ping a user/role on **every** update (omit to clear) |
| `[p]denuvowatch pinguser [user]` | admin | Set/clear an extra ping on build updates only (omit to clear) |
| `[p]denuvowatch interval <minutes>` | admin | Set scan interval (min 5) |
| `[p]denuvowatch show` | admin | Show current config |
| `[p]denuvowatch clear` | admin | Clear the entire watchlist |
| `[p]denuvowatch import [url]` | admin | Import games from an attached JSON file or a direct JSON URL |
| `[p]denuvowatch addadmin @user` | **owner** | Grant a user access to all admin commands |
| `[p]denuvowatch removeadmin @user` | **owner** | Revoke a user's admin access |
| `[p]denuvowatch admins` | **owner** | List users with admin access |

### Permissions

By default only the **bot owner** can use the admin-gated commands above. The
owner can grant access to specific users with `[p]denuvowatch addadmin @user`.
Granted admins can use everything *except* `addadmin` / `removeadmin` / `admins`
themselves — so admins can't add more admins. Access is per-user only (no roles,
no server-permission shortcuts).

### Importing a watchlist

Run `[p]denuvowatch import` two ways:

- **Attachment:** send the command with a JSON file attached.
- **URL:** `[p]denuvowatch import https://.../steam_data.json` — must be a
  **direct/raw** JSON link (e.g. a Discord CDN attachment URL or a
  `raw.githubusercontent.com` link). A normal GitHub page link returns HTML and
  will be rejected.

It accepts the original `steam_data.json` shape (`{"games": {appid: {...}}}`) or
a bare `{appid: {...}}` mapping. Existing games are kept, new ones are added up
to the 50-game cap, and a summary of added/skipped is reported.

All `/d*` commands are hybrid, so they work as both slash commands and prefix
commands (`[p]dadd`, etc.).

## How it works

- The background loop scans every watched game on a configurable interval
  (default **15 minutes**, minimum 5).
- Denuvo is detected two ways: the `drm_notice` field in Steam's appdetails API,
  and a fallback scrape of the store page HTML for the word "Denuvo".
- Build IDs and push timestamps come from the SteamCMD API
  (`api.steamcmd.net`).
- State (name, Denuvo status, build ID, build timestamp, header image) is stored
  globally via Red `Config` and persists across restarts.
- On each scan, any change triggers an embed in the configured alert channel.
- If a mention is set via `[p]denuvowatch mention`, that user or role is pinged
  on **every** update (Denuvo added/removed and build updates). The legacy
  `pinguser` adds an extra ping on build updates only.

## Storage

This cog stores a **single global watchlist** shared across all servers the bot
is in, plus one alert channel, an optional ping user, and the scan interval.
There is a 50-game limit to keep scan times and Steam request volume reasonable.

## Notes / Limitations

- Denuvo detection catches the vast majority of cases, but a very recent
  add/remove may not reflect until the next scan cycle.
- A 0.5s delay is inserted between per-game requests to respect Steam rate
  limits; large watchlists take proportionally longer per scan.
- This is a global-scope cog; only the bot owner can modify the watchlist or
  configuration.
