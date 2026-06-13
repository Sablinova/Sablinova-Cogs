# DenuvoWatch

A Red-DiscordBot port of the standalone "Steam Monitor" bot. Watches a single
global watchlist of Steam games and posts an alert when:

- **Denuvo anti-tamper is removed** (or added)
- A game's **public build (depot)** is updated

## Setup

1. Load the cog: `[p]load denuvowatch`
2. Set the alert channel: `[p]denuvowatch channel #channel`
3. (Optional) Ping a user/role on build updates: `[p]denuvowatch pinguser @user-or-role`
4. Add games: `/dadd <name or AppID>`

The cog auto-installs `beautifulsoup4` and `aiohttp` via Downloader.

## Commands

| Command | Who | Description |
| --- | --- | --- |
| `/dadd <name or AppID>` | admin | Add a game (searches Steam if you pass a name) |
| `/dremove <name or AppID>` | admin | Remove a game from the watchlist |
| `/dlist` | anyone | Show all watched games with current Denuvo/build |
| `/dcheck <name or AppID>` | anyone | Instantly check any game's current status |
| `/exeloc <name or AppID>` | anyone | List all `.exe` paths in the latest depot (via ManifestHub2) |
| `/dforcecheck` | admin | Manually trigger a full watchlist scan |
| `/dstatus` | anyone | Watchlist size + next scheduled check |
| `[p]denuvowatch channel <channel>` | admin | Set alert channel |
| `[p]denuvowatch mention [@user or @role]` | admin | Ping a user/role on **every** update (omit to clear) |
| `[p]denuvowatch pinguser [@user/@role]` | admin | Set/clear an extra ping on build updates only (omit to clear) |
| `[p]denuvowatch interval <minutes>` | admin | Set scan interval (min 5) |
| `[p]denuvowatch show` | admin | Show current config |
| `[p]denuvowatch clear` | admin | Clear the entire watchlist |
| `[p]denuvowatch hubcapkey add <key>` | **owner** | Add a HubCap API key (round-robined for more daily quota) |
| `[p]denuvowatch hubcapkey remove <n\|key>` | **owner** | Remove a key (by list number or value) |
| `[p]denuvowatch hubcapkey list` | **owner** | List keys (masked) with remaining quota |
| `[p]denuvowatch hubcapkey clear` | **owner** | Remove all keys |
| `[p]denuvowatch cacheall [force\|fresh]` | admin | Cache exe paths + file snapshots for the watchlist (`fresh` = pull all from HubCap for accurate diff baselines) |
| `[p]denuvowatch cachestatus` | admin | Show how many games have cached exe data |
| `[p]denuvowatch cacheclear` | admin | Clear the exe-path cache |
| `[p]denuvowatch difftest <game>` | admin | Preview a Build Updated embed with a simulated file diff (no data changed) |
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

## Executable lookup (`/exeloc`)

`/exeloc <name or AppID>` lists every `.exe` path in a game's latest public
depot. As a slash command it **autocompletes**: your watchlist and cached games
are suggested first (tagged `[watchlist]` / `[cached]`), and once you type 3+
characters it also searches the HubCap library (`[library]`). Picking a
suggestion uses its exact AppID. Example output:

```
Engine/Binaries/Win64/CrashReportClient.exe
Engine/Extras/Redist/en-us/UE4PrereqSetup_x64.exe
P3R/Binaries/Win64/P3R.exe
```

Depot file data comes from two sources, tried in order:

1. **HubCapManifest** (`hubcapmanifest.com`) — an authenticated API that serves
   already-decrypted manifest bundles and can pull directly from Steam, so it
   covers far more games (incl. brand-new releases). Requires one or more API
   keys added by the owner with `[p]denuvowatch hubcapkey add <key>`. Each
   `/exeloc` lookup that hits HubCap uses one daily download. **Multiple keys
   are round-robined** — before each download the cog picks the key with the
   most quota left and skips exhausted ones, so N keys ≈ N×25 downloads/day.
2. **ManifestHub2** (`github.com/SSMGAlt/ManifestHub2`) — a free static GitHub
   mirror used as a fallback when no key is set or HubCap has no data. Some of
   its manifests have AES-encrypted filenames, which the cog decrypts using the
   depot key (via the `cryptography` library).

The cog parses the raw Steam manifest protobuf itself — no SteamCMD or Steam
account required. If neither source has the game, `/exeloc` reports no depot
data.

### Caching (saving HubCap quota)

Exe lists are cached per game, keyed by the game's build ID. `/exeloc` serves
from cache when the build is unchanged, so repeat lookups cost **zero** HubCap
downloads. A fetch only happens when there's no cache or the build moved on.

`[p]denuvowatch cacheall` pre-warms the whole watchlist:

- Games whose build hasn't changed since last cache are **skipped** (free).
- For the rest, **ManifestHub2 (free)** is tried first; **HubCap** is used only
  when the free source has nothing, and only while daily quota remains.
- If the HubCap daily limit is hit mid-run, remaining HubCap-only games are
  skipped (their existing cache is kept) and reported in the summary.
- `[p]denuvowatch cacheall fresh` caches via HubCap with a forced refresh
  (accurate baseline), but **still skips games already cached at the current
  build** — so re-running it after hitting the daily limit only spends quota on
  the games still missing. Use this when the free mirror is stale.
- `[p]denuvowatch cacheall force` re-caches everything (ignores the
  unchanged-build skip), still free-source first.
- `[p]denuvowatch cacheall forcefresh` = force + fresh: re-pull every game from
  HubCap. Costs one HubCap download per game — mind your daily quota.

`[p]denuvowatch cachestatus` shows cache size and how many entries are stale
(build moved); `[p]denuvowatch cacheclear` empties the cache.

### Setting HubCap keys

DM the bot (so keys aren't shown in a server) and run
`[p]denuvowatch hubcapkey add <key>` for each key. If you run it in a server
channel, the cog deletes your message automatically. Each key is verified
against HubCap before being saved and is stored in Red Config — never in the
repo. Manage keys with `hubcapkey list` (masked, with remaining quota),
`hubcapkey remove <number|key>`, and `hubcapkey clear`. Adding several keys
multiplies your daily download quota via round-robin selection.

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
- **Build Updated embeds include a depot file diff** — counts, a total size
  change, plus lists of added / removed / modified files **with file sizes**
  (modified files show `old → new, +delta`) — when a previous file snapshot
  exists for that game. Small diffs are shown inline; large diffs collapse to
  the summary and the full file-by-file list is attached as a `.txt`.
- **Content-hashed asset bundles** (e.g. Unity `…<hex>.bundle` / `.resource`)
  re-hash their filenames every build, which would otherwise flood the diff with
  add/remove churn. These are **collapsed** into a single
  "🎁 N asset bundles re-hashed" line, while meaningful named files (exes, dlls,
  data) are still listed individually. The attached `.txt` always contains the
  complete list. The diff compares the new depot manifest
  against the snapshot stored at the last build. Run `[p]denuvowatch cacheall`
  once to seed snapshots so the *next* build update can be diffed (the
  first-ever update for a game has no baseline to compare against). Snapshots
  cached before this update have no size data and will show sizes as `?` until
  the game is re-cached.
- On a build change the new file list is fetched from **HubCap with a forced
  refresh** (HubCap pulls live from Steam). The free ManifestHub2 mirror lags
  by weeks/months and can't be trusted for a just-pushed build, so it's only
  used for diffs when no HubCap key is set. A forced refresh uses one HubCap
  daily download per build change.
- If a mention is set via `[p]denuvowatch mention`, that user or role is pinged
  on **every** update (Denuvo added/removed and build updates). The legacy
  `pinguser` (user or role) adds an extra ping on build updates only.

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
