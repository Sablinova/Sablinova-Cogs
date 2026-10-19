# TidbStats Cog

Live statistics billboard and interactive skip timestamp lookups from [TheIntroDB](https://theintrodb.org) v4 API.

---

## Features

- **Interactive Discord UI Buttons:**
  - 🔍 **Search Media (Modal):** Pop-up form allowing members to search any TV show or movie with ephemeral results visible only to them.
  - 🎲 **Random Intro:** Ephemerally pulls a top series from the database with S01E01 timestamps.
  - 📖 **Setup Guide:** Private guide detailing integration with Plex, Jellyfin, and Emby via `intro-skipper`.
  - 🔄 **Refresh Button:** Cooldown-protected on-demand billboard refresh for members and administrators.
  - 🌐 **TheIntroDB Link:** Direct link to the community web database.
- **TheIntroDB API v4 Support:**
  - Full compatibility with TVDB-keyed media records and TMDB resolution.
  - Live progress bar for accepted vs unaccepted submission integrity.
  - Recent growth tracking (submissions and hours saved).
- **Chat Commands:**
  - `[p]tidb check <show/movie> [season] [episode]` — Check intro/credits markers for any title.
  - `[p]tidb stats` — Print current database stats embed on demand.

---

## Command Reference

### User Commands

| Command | Usage | Description |
| :--- | :--- | :--- |
| `[p]tidb check <title> [s] [e]` | `[p]tidb check "The Simpsons" 1 1` | Look up intro, recap, and credit timestamps for an episode or movie. |
| `[p]tidb stats` | `[p]tidb stats` | Display live TheIntroDB statistics on demand. |

### Admin Configuration (`[p]tidbstats`)

| Command | Usage | Description |
| :--- | :--- | :--- |
| `[p]tidbstats channel <#channel>` | `[p]tidbstats channel #tidb-stats` | Set the channel where the auto-updating stats billboard is posted. |
| `[p]tidbstats enable` | `[p]tidbstats enable` | Enable background periodic stat updates. |
| `[p]tidbstats disable` | `[p]tidbstats disable` | Disable background stat updates. |
| `[p]tidbstats interval <hours>` | `[p]tidbstats interval 2` | Configure update frequency (1 to 24 hours). |
| `[p]tidbstats logo <url>` | `[p]tidbstats logo <url>` | Set a custom thumbnail image URL for the billboard embed. |
| `[p]tidbstats tmdbkey <key>` | `[p]tidbstats tmdbkey <key>` | Set the TMDB API key used for media name resolution. |
| `[p]tidbstats refresh` | `[p]tidbstats refresh` | Force an immediate update of the live billboard message. |
