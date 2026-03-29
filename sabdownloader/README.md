# SabDownloader

A [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) v3 cog that downloads media from Instagram, TikTok, YouTube, Twitter/X, Reddit, Spotify, and 1000+ other sites, then uploads directly to Discord.

Uses gallery-dl, yt-dlp, and spotdl as download backends. Large videos are automatically compressed with ffmpeg to fit Discord's upload limits. Files that still exceed the limit are uploaded to AnonDrop.net as a fallback.

## Installation

```
[p]repo add sablinova https://github.com/Sablinova/Sablinova-Cogs
[p]cog install sablinova sabdownloader
[p]load sabdownloader
```

**System requirement:** ffmpeg must be installed on the host system for video compression.

## Quick Start

Download media from any supported URL:

```
[p]dl https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

Extract audio as MP3:

```
[p]dl audio https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

## How It Works

1. User posts `[p]dl <url>` in a channel.
2. The cog validates the URL (checks scheme, SSRF prevention, cooldown, channel restrictions).
3. A progress bar message is sent and updated throughout the process.
4. The URL is downloaded using gallery-dl first (better for images, Instagram, Twitter). If that fails, yt-dlp is tried (better for YouTube, Reddit, video in general).
5. If the file fits Discord's upload limit, it is uploaded directly.
6. If the file is too large and is a video, it is compressed with ffmpeg (two-pass libx264 encoding).
7. If compression is insufficient or the file is not a video, it is uploaded to AnonDrop.net and the link is posted.
8. The user's command message and the progress message are cleaned up.
9. The download is logged to Red's modlog.

## Supported Platforms

gallery-dl, yt-dlp, and spotdl together support over 1000 sites. Some highlights:

| Platform | Best Backend | Notes |
|----------|-------------|-------|
| Instagram | gallery-dl | Reels, carousels, stories. Requires cookies. |
| Twitter/X | gallery-dl | Images and video. |
| YouTube | yt-dlp | All formats, playlists disabled by default. |
| TikTok | yt-dlp | Single videos. |
| Reddit | yt-dlp | Muxes separate video+audio DASH streams. |
| Facebook | yt-dlp | Public videos. |
| Twitch | yt-dlp | Clips. |
| **Spotify** | **spotdl** | **Tracks, albums, playlists. No authentication needed.** |

## Commands

### Download (Everyone)

| Command | Description |
|---------|-------------|
| `[p]dl <url>` | Download media from a URL and upload to Discord. |
| `[p]dl audio <url>` | Extract audio from a URL as MP3. |
| `[p]dl hd <url>` | Download in highest quality and upload to AnonDrop. |

### Configuration (Admin / manage_guild)

All configuration commands are under `[p]sabdownloader` (alias: `[p]sabdl`).

| Command | Description |
|---------|-------------|
| `settings` | Display all current settings. |
| `toggle` | Enable or disable SabDownloader for this server. |
| `maxduration <seconds>` | Set maximum allowed video duration (default: 600s). |
| `cooldown <seconds>` | Set per-user cooldown (default: 30s). |
| `deletecommand` | Toggle deletion of the user's command message after download. |
| `allowedchannels add <channel>` | Restrict downloads to specific channels. |
| `allowedchannels remove <channel>` | Remove a channel from the allowed list. |
| `anondrop toggle` | Enable or disable AnonDrop fallback for oversized files. |

### Bot Owner

| Command | Description |
|---------|-------------|
| `cookies <path>` | Set path to a Netscape cookies.txt file (needed for Instagram). |
| `maxconcurrent <count>` | Set maximum concurrent downloads (1-10, default: 3). |
| `spotify status` | Check spotdl installation status and version. |

## File Size Handling

Discord upload limits vary by server boost tier:

| Boost Tier | Limit |
|------------|-------|
| None / 0 / 1 | 10 MB |
| 2 | 50 MB |
| 3 | 100 MB |

When a file exceeds the limit:

1. **Video files** are compressed with ffmpeg two-pass encoding. Resolution is scaled down if the target bitrate is too low (1080p to 720p to 480p).
2. If compression is not enough, or the file is not a video, **AnonDrop.net** is used as a fallback file host.
3. Admins can disable the AnonDrop fallback with `[p]sabdownloader anondrop toggle`.

## Progress Bar

A text-based progress bar is displayed in the channel while downloading:

```
Downloading... [████████░░░░░░░░░░░░] 40% | 3.2MB/8.0MB
```

- yt-dlp downloads show real percentage and size.
- gallery-dl and spotdl downloads show an animated loading indicator (no progress callback available).
- The bar updates every 3 seconds to avoid Discord rate limits.

## HD Mode

`[p]dl hd <url>` downloads media at the highest available quality with no resolution or file size restrictions. The file is uploaded directly to AnonDrop.net and a download link is posted in the channel.

- No compression is applied. Full quality is preserved.
- AnonDrop must be enabled for the server (`[p]sabdownloader anondrop toggle`).
- Useful for YouTube videos where you want 1080p or 4K without Discord's upload limit.

## Instagram Authentication

Instagram requires login cookies for most content. To configure:

1. Export cookies from a logged-in browser session using a cookie export extension (Netscape/cookies.txt format).
2. Place the file on the bot's host system.
3. Run: `[p]sabdownloader cookies /path/to/cookies.txt`

Without cookies configured, Instagram URLs will show an error message directing the user to contact the bot owner.

## Spotify Downloads

Spotify support is powered by [spotdl](https://github.com/spotDL/spotify-downloader) using the [TzurSoffer fork](https://github.com/TzurSoffer/spotify-downloader) which includes **spotipyFree** - a library that scrapes Spotify without requiring API credentials.

### How It Works
- No Spotify API credentials needed
- No Premium subscription required
- Auto-installs spotdl on first use
- Downloads tracks as 320kbps MP3
- Works with tracks, albums, and playlists

### Example
```
[p]dl https://open.spotify.com/track/7hPwUxGo7YgQFFYDynDQwp
```

### Status Check
Bot owners can verify spotdl installation:
```
[p]sabdownloader spotify status
```

This will show the spotdl version and whether it's ready to use.

## Security

- **SSRF prevention**: URLs pointing to private/reserved IP ranges (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, etc.) are rejected.
- **Disk exhaustion**: Downloads are capped at 3x the guild's file size limit. Temp files are cleaned up in all cases.
- **CPU abuse**: Global concurrency semaphore, per-user cooldown, 5-minute ffmpeg timeout.
- **No shell injection**: yt-dlp runs as a Python library, ffmpeg uses `create_subprocess_exec` (no shell).
- **Cookies file**: Restricted to bot owner only, must be an existing file path.

## Requirements

- Red-DiscordBot >= 3.5.6
- Python >= 3.9
- ffmpeg installed on the host system
- Pip dependencies installed automatically: `yt-dlp`, `gallery-dl`
- Spotify support: `spotdl` (auto-installed from TzurSoffer fork on first use)

## Optional: User-Installed App Support in Threads

If you enable **user-installable app** mode for your bot (allowing users to install the bot personally and use slash commands in servers where the bot isn't a member), there's a bug in Red-DiscordBot that causes "Application did not respond" errors when using `/dl` in threads.

This is because Red's permission system tries to access `ctx.channel.parent` and `ctx.channel.category` on threads, but these are `None` when the bot isn't a guild member and can't see the parent channel.

### Optional Patch

To fix this, you can patch Red's `requires.py` file. This is **optional** — only needed if you use user-install mode and want thread support.

**File:** `<your-venv>/lib/python3.xx/site-packages/redbot/core/commands/requires.py`

**Patch (around line 590-594):**

```diff
         if isinstance(ctx.channel, discord.Thread):
-            channels.append(ctx.channel.parent)
+            # PATCHED: Check parent is not None before appending
+            if ctx.channel.parent is not None:
+                channels.append(ctx.channel.parent)
         else:
             channels.append(ctx.channel)
-        category = ctx.channel.category
+        # PATCHED: Fix for user-installed apps in threads (Parent channel not found bug)
+        if isinstance(ctx.channel, discord.Thread):
+            category = ctx.channel.parent.category if ctx.channel.parent else None
+        else:
+            category = ctx.channel.category
```

**Note:** This patch must be re-applied after updating Red-DiscordBot.
