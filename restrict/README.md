# Restrict Cog for Red-DiscordBot

Restrict users, channels, or roles from sending GIFs, media, links, stickers, and reactions with instant, silent auto-deletion by default, configurable notices, temporary durations, mod-logging, and spam escalation controls.

---

## Features

- **Primary GIF Restriction (`restrict gif`):**
  - Instant message deletion the moment a restricted user sends a GIF.
  - Catches Tenor links, Giphy links, direct `.gif` / `.gifv` links, uploaded file attachments, and Discord GIF embeds.
- **Extended Content Restrictions:**
  - `image`: PNG, JPG, JPEG, WEBP attachments & direct image links.
  - `video`: MP4, WEBM, MOV attachments & streaming links (YouTube, TikTok, Streamable, Vimeo).
  - `file`: Any uploaded file attachments.
  - `link`: External web links and URLs.
  - `invite`: Discord server invite links.
  - `sticker`: Discord stickers.
  - `reaction`: Emoji reactions added to messages.
  - `all`: All media, files, links, invites, and stickers.
- **Granular Target Scope:**
  - Restrict a **User**: `[p]restrict gif @user [duration] [reason]`
  - Restrict a **Channel**: `[p]restrict gif #channel [reason]`
  - Restrict a **Role**: `[p]restrict gif @role [reason]`
- **Silent by Default:**
  - Deletes restricted content silently without spamming or cluttering the chat.
  - Optional channel warning that auto-deletes itself after 5 seconds (`[p]restrict set notify channel`).
  - Optional private DM notification to the user (`[p]restrict set notify dm`).
- **Temporary Expiration:**
  - Supports natural durations like `30m`, `2h`, `1d`, `7d`, or permanent.
- **High-Performance In-Memory Cache:**
  - Fast in-memory dictionary cache ensures normal chat messages incur zero database/disk overhead ($< 0.05\text{ ms}$).
- **Audit Mod-Log & Repeat Escalation:**
  - Optional mod-log channel for audit trails.
  - Optional auto-timeout if a restricted user tries to spam restricted content $N$ times within $M$ minutes.

---

## Commands

### Restrict Commands
| Command | Description |
| :--- | :--- |
| `[p]restrict gif <target> [duration] [reason]` | Restrict target (user, channel, or role) from posting GIFs. |
| `[p]restrict image <target> [duration] [reason]` | Restrict target from posting non-GIF images. |
| `[p]restrict video <target> [duration] [reason]` | Restrict target from posting videos or video links. |
| `[p]restrict file <target> [duration] [reason]` | Restrict target from uploading file attachments. |
| `[p]restrict link <target> [duration] [reason]` | Restrict target from posting web links. |
| `[p]restrict invite <target> [duration] [reason]` | Restrict target from posting Discord invites. |
| `[p]restrict sticker <target> [duration] [reason]` | Restrict target from sending stickers. |
| `[p]restrict reaction <target> [duration] [reason]` | Restrict target from adding emoji reactions. |
| `[p]restrict all <target> [duration] [reason]` | Restrict target from all media, links, invites, and stickers. |

### Unrestrict Commands
| Command | Description |
| :--- | :--- |
| `[p]unrestrict gif <target>` | Remove GIF restriction from target. |
| `[p]unrestrict <type> <target>` | Remove specific restriction type from target. |
| `[p]unrestrict all <target>` | Remove all restrictions from target. |

### Inspection & Management
| Command | Description |
| :--- | :--- |
| `[p]restrict list [user\|channel\|role]` | List all active restrictions in this server. |
| `[p]restrict check <member>` | Inspect all direct and role-inherited restrictions on a member. |

### Configuration Commands
| Command | Description |
| :--- | :--- |
| `[p]restrict set notify <silent\|channel\|dm>` | Set notification mode (default: `silent`). |
| `[p]restrict set autodelete <seconds>` | Set auto-delete delay for channel warnings (default: 5s). |
| `[p]restrict set modlog [#channel\|none]` | Set or disable audit mod-log channel. |
| `[p]restrict set escalate <true/false> [attempts] [window_m] [timeout_m]` | Configure repeat spam auto-timeout. |
| `[p]restrict set show` | Display current configuration settings. |

---

## Examples

- **Restrict a user from sending GIFs silently forever:**
  ```
  [p]restrict gif @spammer
  ```
- **Restrict a user from sending GIFs for 2 hours with reason:**
  ```
  [p]restrict gif @spammer 2h Stop gif spamming
  ```
- **Disallow GIFs in a text channel:**
  ```
  [p]restrict gif #announcements
  ```
- **Unrestrict a user:**
  ```
  [p]unrestrict gif @spammer
  ```
