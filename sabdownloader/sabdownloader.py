import asyncio
import ipaddress
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from functools import partial
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import discord
from redbot.core import app_commands, commands, Config, modlog
from redbot.core.bot import Red

log = logging.getLogger("red.sablinova.sabdownloader")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROGRESS_FILLED = "\u2588"  # █
PROGRESS_EMPTY = "\u2591"  # ░
PROGRESS_BAR_LENGTH = 20
PROGRESS_UPDATE_INTERVAL = 1.0  # seconds between progress message edits

ANONDROP_CHUNK_SIZE = 9 * 1024 * 1024  # 9 MB

# TikTok direct scraper constants
_TIKTOK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)
# Shortened UA for short-link resolution (avoids bot detection)
_TIKTOK_SHORT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
)

# File extensions used by yt-dlp / gallery-dl for incomplete or temp files.
# These MUST be filtered out when collecting downloaded files.
_TEMP_EXTENSIONS = {
    ".part",
    ".ytdl",
    ".temp",
    ".tmp",
    ".partial",
    ".part-Frag0",
    ".part-Frag1",
    ".part-Frag2",
}


def _is_temp_file(filepath: str) -> bool:
    """Return True if the file looks like an incomplete/temp download."""
    basename = os.path.basename(filepath)
    # Check known temp extensions (including compound ones like .mp4.part)
    for ext in _TEMP_EXTENSIONS:
        if basename.endswith(ext):
            return True
    # yt-dlp fragment pattern: .part-FragN where N is any number
    if re.search(r"\.part-Frag\d+$", basename):
        return True
    return False


def _collect_real_files(temp_dir: str) -> List[str]:
    """Collect non-temp, non-empty files from a directory.

    If a merged output file exists (no .fNNN. in the name), exclude
    the intermediate format-specific files that yt-dlp leaves behind.
    """
    results = []
    intermediates = []
    for f in os.listdir(temp_dir):
        fp = os.path.join(temp_dir, f)
        if not os.path.isfile(fp):
            continue
        if _is_temp_file(fp):
            log.debug("Skipping temp/partial file: %s", f)
            continue
        if os.path.getsize(fp) == 0:
            log.debug("Skipping zero-byte file: %s", f)
            continue
        # yt-dlp intermediate files have .fNNN. pattern (e.g. video.f137.mp4)
        if re.search(r"\.f\d+\.", f):
            intermediates.append(fp)
        else:
            results.append(fp)

    # If we have merged/final files, skip the intermediates
    if results and intermediates:
        log.info(
            "Found %d merged files and %d intermediates; discarding intermediates",
            len(results),
            len(intermediates),
        )
        return results

    # If no merged files exist (merge failed/skipped), return intermediates
    if not results and intermediates:
        log.info(
            "No merged files found; returning %d intermediate files",
            len(intermediates),
        )
        return intermediates

    return results


_KNOWN_MEDIA_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
    ".avi",
    ".mov",
    ".flv",
    ".wmv",
    ".m4v",
    ".ts",
    ".mp3",
    ".m4a",
    ".ogg",
    ".opus",
    ".flac",
    ".wav",
    ".aac",
    ".wma",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
    ".svg",
}


def _sanitize_discord_filename(filepath: str) -> str:
    """Return a clean filename suitable for Discord uploads.

    Discord sometimes fails to render inline video/audio if the filename
    contains URL-encoded characters, excessively long names, or unusual
    characters.  This produces a short, ASCII-safe name while preserving
    the original extension.
    """
    name = os.path.basename(filepath)

    # os.path.splitext fails on names like "....mp4" (returns no extension).
    # Try matching a known media extension at the end of the filename first.
    ext = ""
    name_lower = name.lower()
    for known_ext in sorted(_KNOWN_MEDIA_EXTENSIONS, key=len, reverse=True):
        if name_lower.endswith(known_ext):
            ext = known_ext
            name = name[: len(name) - len(known_ext)]
            break
    if not ext:
        # Fallback to splitext for unknown extensions
        name, ext = os.path.splitext(name)
        ext = ext.lower()

    # Strip non-ASCII and problematic characters, keep alphanumerics, hyphens, underscores
    clean = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    # Collapse multiple underscores
    clean = re.sub(r"_+", "_", clean).strip("_")
    # Truncate to 60 chars to avoid excessively long names
    if len(clean) > 60:
        clean = clean[:60].rstrip("_")
    # Ensure we have at least something
    if not clean:
        clean = "download"
    return clean + ext


# Private/reserved IP ranges for SSRF prevention
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# Platforms where gallery-dl should be tried first
_GALLERY_DL_PRIORITY = {
    "instagram.com",
    "www.instagram.com",
    "twitter.com",
    "www.twitter.com",
    "x.com",
    "www.x.com",
    "nitter.net",
}

# Platforms where yt-dlp should be tried first
_YTDLP_PRIORITY = {
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "m.youtube.com",
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "v.redd.it",
}

# TikTok domains — handled by direct scraper instead of yt-dlp
_TIKTOK_DOMAINS = {
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "m.tiktok.com",
    "t.tiktok.com",
    "pro.tiktok.com",
}

# Short-link subdomains that need redirect resolution
_TIKTOK_SHORT_DOMAINS = {"vm.tiktok.com", "vt.tiktok.com", "t.tiktok.com"}


# ---------------------------------------------------------------------------
# Progress tracker (shared state for download progress)
# ---------------------------------------------------------------------------


class ProgressTracker:
    """Thread-safe progress state for download/compress/upload operations."""

    def __init__(self):
        self.stage: str = "Downloading"
        self.percent: Optional[float] = None  # None = indeterminate
        self.downloaded_bytes: int = 0
        self.total_bytes: Optional[int] = None
        self.speed: Optional[str] = None
        self.eta: Optional[str] = None
        self._last_update: float = 0.0

    def format_bar(self) -> str:
        """Build the text progress line."""
        if self.percent is not None:
            filled = int(self.percent / 100 * PROGRESS_BAR_LENGTH)
            bar = PROGRESS_FILLED * filled + PROGRESS_EMPTY * (
                PROGRESS_BAR_LENGTH - filled
            )
            pct_str = f"{self.percent:.0f}%"
        else:
            # Indeterminate: animated spinner-style bar
            bar = PROGRESS_EMPTY * PROGRESS_BAR_LENGTH
            pct_str = "..."

        size_str = ""
        if self.total_bytes and self.total_bytes > 0:
            size_str = f" | {_human_size(self.downloaded_bytes)}/{_human_size(self.total_bytes)}"
        elif self.downloaded_bytes > 0:
            size_str = f" | {_human_size(self.downloaded_bytes)}"

        return f"{self.stage}... [{bar}] {pct_str}{size_str}"


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _human_size(num_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


def _is_private_url(url: str) -> bool:
    """Check if a URL resolves to a private/reserved IP range (SSRF prevention)."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return True
        # Try to parse as IP directly
        try:
            addr = ipaddress.ip_address(hostname)
            return any(addr in net for net in _PRIVATE_NETWORKS)
        except ValueError:
            pass
        # For hostnames like localhost
        if hostname in ("localhost", "localhost.localdomain"):
            return True
        return False
    except Exception:
        return True


def _make_temp_dir() -> str:
    """Create a unique temporary directory for downloads."""
    path = os.path.join(tempfile.gettempdir(), f"sabdownloader_{uuid.uuid4().hex[:12]}")
    os.makedirs(path, exist_ok=True)
    return path


def _cleanup_temp_dir(path: str) -> None:
    """Remove a temporary directory and all its contents."""
    try:
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:
        log.warning("Failed to clean up temp dir %s: %s", path, e)


def _detect_platform(url: str) -> str:
    """Extract a human-readable platform name from a URL."""
    try:
        hostname = urlparse(url).hostname or ""
        hostname = hostname.lower().removeprefix("www.").removeprefix("m.")
        platform_map = {
            "instagram.com": "Instagram",
            "twitter.com": "Twitter/X",
            "x.com": "Twitter/X",
            "tiktok.com": "TikTok",
            "youtube.com": "YouTube",
            "youtu.be": "YouTube",
            "reddit.com": "Reddit",
            "v.redd.it": "Reddit",
            "facebook.com": "Facebook",
            "fb.watch": "Facebook",
            "twitch.tv": "Twitch",
            "vimeo.com": "Vimeo",
            "soundcloud.com": "SoundCloud",
            "pinterest.com": "Pinterest",
            "tumblr.com": "Tumblr",
            "flickr.com": "Flickr",
            "imgur.com": "Imgur",
            "nitter.net": "Twitter/X (Nitter)",
        }
        return platform_map.get(hostname, hostname or "Unknown")
    except Exception:
        return "Unknown"


def _get_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _purge_temp_files(temp_dir: str) -> None:
    """Remove all files from temp_dir (used between backend attempts).

    This prevents one backend's leftover/partial files from being
    picked up by the next backend's file scan.
    """
    try:
        for f in os.listdir(temp_dir):
            fp = os.path.join(temp_dir, f)
            try:
                if os.path.isfile(fp):
                    os.unlink(fp)
                elif os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# TikTok direct scraper backend (Cobalt-style)
# ---------------------------------------------------------------------------

# Regex patterns for extracting post IDs from TikTok URLs
_TIKTOK_POST_ID_RE = re.compile(
    r"(?:/(?:video|photo|v)/|/i18n/share/video/)(\d{10,21})"
)


def _tiktok_extract_post_id(url: str) -> Optional[str]:
    """Extract a numeric TikTok post ID from a URL path.

    Supports patterns:
      /@user/video/1234567890
      /@user/photo/1234567890
      /i18n/share/video/1234567890
      /v/1234567890.html
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    # Strip .html suffix (TikTok Lite URLs)
    if path.endswith(".html"):
        path = path[:-5]
    match = _TIKTOK_POST_ID_RE.search(path)
    if match:
        return match.group(1)
    return None


async def _tiktok_resolve_short_link(
    session: aiohttp.ClientSession, url: str
) -> Optional[str]:
    """Resolve a TikTok short link (vm.tiktok.com, vt.tiktok.com, etc.)
    to a full URL and extract the post ID.

    TikTok returns HTML with an <a href="..."> tag pointing to the canonical URL.
    We do NOT follow the redirect directly to avoid bot detection.
    """
    try:
        async with session.get(
            url,
            allow_redirects=False,
            headers={"User-Agent": _TIKTOK_SHORT_UA},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            # Some short links return a 301/302 redirect directly
            location = resp.headers.get("Location")
            if location and "tiktok.com" in location:
                post_id = _tiktok_extract_post_id(location)
                if post_id:
                    return post_id

            # Otherwise parse the HTML body for <a href="...">
            html = await resp.text()
            if html.startswith('<a href="https://'):
                extracted = html.split('<a href="')[1].split('"')[0]
                # Strip query params
                extracted = extracted.split("?")[0]
                post_id = _tiktok_extract_post_id(extracted)
                if post_id:
                    return post_id

            log.warning("[tiktok] Could not resolve short link: %s", url)
            return None
    except Exception as e:
        log.warning("[tiktok] Short link resolution failed for %s: %s", url, e)
        return None


async def _tiktok_download(
    url: str,
    temp_dir: str,
    progress_tracker: Optional[ProgressTracker] = None,
    cookies_file: Optional[str] = None,
    max_filesize: Optional[int] = None,
    max_duration: Optional[int] = None,
    audio_only: bool = False,
    hd_mode: bool = False,
) -> Tuple[List[str], Optional[dict]]:
    """Download TikTok media by scraping the web page directly.

    Extracts the SSR JSON from TikTok's HTML to get direct H.264 video URLs
    (or image URLs for slideshows). No yt-dlp involved.

    Returns (list of file paths, metadata dict or None).
    """
    import json as _json

    domain = _get_domain(url)

    async with aiohttp.ClientSession() as session:
        # Step 1: Resolve post ID
        post_id = _tiktok_extract_post_id(url)

        if not post_id and domain in _TIKTOK_SHORT_DOMAINS:
            post_id = await _tiktok_resolve_short_link(session, url)

        if not post_id:
            raise RuntimeError(f"Could not extract TikTok post ID from URL: {url}")

        log.info("[tiktok] Resolved post ID: %s", post_id)

        # Step 2: Fetch the TikTok page HTML
        # Use @i as placeholder username — works for any video when given a valid post ID
        page_url = f"https://www.tiktok.com/@i/video/{post_id}"
        headers = {"User-Agent": _TIKTOK_USER_AGENT}

        # Add cookies if available
        cookie_header = None
        if cookies_file and os.path.isfile(cookies_file):
            try:
                cookie_pairs = []
                with open(cookies_file, "r") as cf:
                    for line in cf:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split("\t")
                        if len(parts) >= 7 and "tiktok" in parts[0].lower():
                            cookie_pairs.append(f"{parts[5]}={parts[6]}")
                if cookie_pairs:
                    cookie_header = "; ".join(cookie_pairs)
            except Exception as e:
                log.warning("[tiktok] Failed to parse cookies file: %s", e)

        if cookie_header:
            headers["Cookie"] = cookie_header

        try:
            async with session.get(
                page_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"TikTok returned HTTP {resp.status} for {page_url}"
                    )
                html = await resp.text()
        except aiohttp.ClientError as e:
            raise RuntimeError(f"Failed to fetch TikTok page: {e}") from e

        # Step 3: Extract SSR JSON from HTML
        ssr_marker = (
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        )
        if ssr_marker not in html:
            # Try alternate marker used by some TikTok page versions
            ssr_marker_alt = '<script id="SIGI_STATE" type="application/json">'
            if ssr_marker_alt in html:
                ssr_marker = ssr_marker_alt
            else:
                raise RuntimeError(
                    "TikTok page does not contain expected SSR data. "
                    "The page structure may have changed."
                )

        try:
            json_str = html.split(ssr_marker)[1].split("</script>")[0]
            data = _json.loads(json_str)
        except (IndexError, _json.JSONDecodeError) as e:
            raise RuntimeError(f"Failed to parse TikTok SSR JSON: {e}") from e

        # Step 4: Navigate to video detail
        # Primary path: __UNIVERSAL_DATA_FOR_REHYDRATION__
        detail = None
        if "__DEFAULT_SCOPE__" in data:
            video_detail = data["__DEFAULT_SCOPE__"].get("webapp.video-detail", {})
            detail = video_detail.get("itemInfo", {}).get("itemStruct")
        # Fallback: SIGI_STATE structure
        elif "ItemModule" in data:
            items = data["ItemModule"]
            if post_id in items:
                detail = items[post_id]
            elif items:
                detail = next(iter(items.values()))

        if not detail:
            raise RuntimeError(
                "Could not find video detail in TikTok SSR data. "
                "The post may be deleted or unavailable."
            )

        # Check for unavailable / age-restricted content
        status_msg = None
        if "__DEFAULT_SCOPE__" in data:
            status_msg = (
                data["__DEFAULT_SCOPE__"]
                .get("webapp.video-detail", {})
                .get("statusMsg")
            )
        if status_msg:
            raise RuntimeError(f"TikTok post unavailable: {status_msg}")

        if detail.get("isContentClassified"):
            raise RuntimeError(
                "This TikTok is age-restricted and cannot be downloaded."
            )

        # Extract metadata
        author = detail.get("author", {})
        author_name = author.get("uniqueId") or author.get("nickname") or "unknown"
        video_desc = detail.get("desc", "")
        title = video_desc[:100] if video_desc else f"tiktok_{post_id}"
        duration = int(detail.get("video", {}).get("duration", 0))

        # Duration check
        if max_duration and duration > max_duration:
            raise ValueError("Video exceeds maximum allowed duration.")

        metadata = {
            "title": title,
            "uploader": author_name,
            "duration": duration,
            "webpage_url": f"https://www.tiktok.com/@{author_name}/video/{post_id}",
            "id": post_id,
        }

        filename_base = re.sub(
            r"[^a-zA-Z0-9_\-]", "_", f"tiktok_{author_name}_{post_id}"
        )
        filename_base = re.sub(r"_+", "_", filename_base).strip("_")[:80]

        images = (
            detail.get("imagePost", {}).get("images")
            if detail.get("imagePost")
            else None
        )

        # Step 5a: Handle slideshow / image posts
        if images and not audio_only:
            downloaded = []
            for i, img in enumerate(images):
                img_urls = img.get("imageURL", {}).get("urlList", [])
                # Prefer .jpeg URL
                img_url = None
                for candidate in img_urls:
                    if ".jpeg" in candidate or ".jpg" in candidate:
                        img_url = candidate
                        break
                if not img_url and img_urls:
                    img_url = img_urls[0]
                if not img_url:
                    continue

                photo_filename = f"{filename_base}_photo_{i + 1}.jpg"
                photo_path = os.path.join(temp_dir, photo_filename)

                try:
                    async with session.get(
                        img_url,
                        headers={
                            "User-Agent": _TIKTOK_USER_AGENT,
                            "Referer": "https://www.tiktok.com/",
                        },
                        timeout=aiohttp.ClientTimeout(total=60),
                    ) as img_resp:
                        if img_resp.status != 200:
                            log.warning(
                                "[tiktok] Image %d returned HTTP %s",
                                i + 1,
                                img_resp.status,
                            )
                            continue
                        with open(photo_path, "wb") as f:
                            async for chunk in img_resp.content.iter_chunked(65536):
                                f.write(chunk)
                    if os.path.getsize(photo_path) > 0:
                        downloaded.append(photo_path)
                except Exception as e:
                    log.warning("[tiktok] Failed to download image %d: %s", i + 1, e)

            if progress_tracker:
                progress_tracker.percent = 100

            return downloaded, metadata

        # Step 5b: Get video URL
        video_data = detail.get("video", {})
        play_addr = video_data.get("playAddr")

        if not play_addr:
            # Try downloadAddr as fallback
            play_addr = video_data.get("downloadAddr")
        if not play_addr:
            # Try bitrateInfo for explicit H.264
            bitrate_info = video_data.get("bitrateInfo", [])
            for br in bitrate_info:
                codec = br.get("CodecType", "")
                if "h264" in codec.lower() or "avc" in codec.lower():
                    urls = br.get("PlayAddr", {}).get("UrlList", [])
                    if urls:
                        play_addr = urls[0]
                        break
            # Last resort: any bitrate entry
            if not play_addr and bitrate_info:
                urls = bitrate_info[0].get("PlayAddr", {}).get("UrlList", [])
                if urls:
                    play_addr = urls[0]

        if not play_addr:
            raise RuntimeError("Could not find video URL in TikTok data.")

        # For HD mode, try to find the best H.264 from bitrateInfo
        if hd_mode:
            bitrate_info = video_data.get("bitrateInfo", [])
            best_h264 = None
            best_bitrate = 0
            for br in bitrate_info:
                codec = br.get("CodecType", "")
                if "h264" in codec.lower() or "avc" in codec.lower():
                    br_val = br.get("Bitrate", 0)
                    if br_val > best_bitrate:
                        best_bitrate = br_val
                        urls = br.get("PlayAddr", {}).get("UrlList", [])
                        if urls:
                            best_h264 = urls[0]
                            best_bitrate = br_val
            if best_h264:
                play_addr = best_h264
                log.info("[tiktok] HD mode: using H.264 at bitrate %d", best_bitrate)

        # Step 5c: Handle audio-only mode
        if audio_only:
            # Download the video, then extract audio with ffmpeg
            video_filename = f"{filename_base}_temp.mp4"
            video_path = os.path.join(temp_dir, video_filename)
            audio_filename = f"{filename_base}.mp3"
            audio_path = os.path.join(temp_dir, audio_filename)

            # Download video first
            await _tiktok_stream_file(
                session,
                play_addr,
                video_path,
                progress_tracker,
                max_filesize=max_filesize,
            )

            if not os.path.isfile(video_path) or os.path.getsize(video_path) == 0:
                raise RuntimeError(
                    "Failed to download TikTok video for audio extraction."
                )

            # Extract audio with ffmpeg
            if progress_tracker:
                progress_tracker.stage = "Extracting audio"
                progress_tracker.percent = 50

            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-vn",
                "-acodec",
                "libmp3lame",
                "-ab",
                "192k",
                audio_path,
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *ffmpeg_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                if proc.returncode != 0:
                    log.warning(
                        "[tiktok] ffmpeg audio extraction failed: %s",
                        stderr.decode(errors="replace")[-500:],
                    )
                    raise RuntimeError("Audio extraction failed.")
            except asyncio.TimeoutError:
                raise RuntimeError("Audio extraction timed out.")

            # Clean up temp video
            try:
                os.unlink(video_path)
            except OSError:
                pass

            if progress_tracker:
                progress_tracker.percent = 100

            if os.path.isfile(audio_path) and os.path.getsize(audio_path) > 0:
                return [audio_path], metadata
            raise RuntimeError("Audio extraction produced no output.")

        # Step 6: Download the video
        video_filename = f"{filename_base}.mp4"
        video_path = os.path.join(temp_dir, video_filename)

        await _tiktok_stream_file(
            session,
            play_addr,
            video_path,
            progress_tracker,
            max_filesize=max_filesize,
        )

        if not os.path.isfile(video_path) or os.path.getsize(video_path) == 0:
            raise RuntimeError("TikTok video download produced an empty file.")

        # Check filesize limit
        actual_size = os.path.getsize(video_path)
        if max_filesize and actual_size > max_filesize:
            log.warning(
                "[tiktok] Downloaded file %s exceeds max_filesize (%s > %s)",
                video_filename,
                _human_size(actual_size),
                _human_size(max_filesize),
            )

        return [video_path], metadata


async def _tiktok_stream_file(
    session: aiohttp.ClientSession,
    url: str,
    output_path: str,
    progress_tracker: Optional[ProgressTracker] = None,
    max_filesize: Optional[int] = None,
) -> None:
    """Stream a TikTok media URL to disk with progress tracking."""
    try:
        async with session.get(
            url,
            headers={
                "User-Agent": _TIKTOK_USER_AGENT,
                "Referer": "https://www.tiktok.com/",
            },
            timeout=aiohttp.ClientTimeout(total=300),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"TikTok CDN returned HTTP {resp.status}")

            total = resp.content_length
            if progress_tracker:
                progress_tracker.total_bytes = total
                progress_tracker.downloaded_bytes = 0

            downloaded = 0
            with open(output_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(65536):
                    f.write(chunk)
                    downloaded += len(chunk)

                    if progress_tracker:
                        progress_tracker.downloaded_bytes = downloaded
                        if total and total > 0:
                            progress_tracker.percent = (downloaded / total) * 100

                    # Abort if exceeding hard size limit (2x max_filesize as safety)
                    if max_filesize and downloaded > max_filesize * 2:
                        log.warning(
                            "[tiktok] Download exceeds 2x max_filesize, aborting"
                        )
                        break

            if progress_tracker:
                progress_tracker.percent = 100

    except aiohttp.ClientError as e:
        raise RuntimeError(f"TikTok media download failed: {e}") from e


# ---------------------------------------------------------------------------
# gallery-dl backend
# ---------------------------------------------------------------------------


def _gallery_dl_download(
    url: str,
    temp_dir: str,
    cookies_file: Optional[str] = None,
    max_filesize: Optional[int] = None,
) -> List[str]:
    """Run gallery-dl synchronously. Returns list of downloaded file paths.

    Must be called via run_in_executor.
    """
    import gallery_dl
    from gallery_dl import config as gdl_config, job

    # Reset config for this run
    gdl_config.clear()
    gdl_config.load()

    # Set output directory
    gdl_config.set(("extractor",), "base-directory", temp_dir)
    gdl_config.set(("extractor",), "directory", [""])
    # Flatten filenames (no subdirectories)
    gdl_config.set(("extractor",), "filename", "{filename}.{extension}")

    if cookies_file:
        gdl_config.set(("extractor",), "cookies", cookies_file)

    # Snapshot existing files BEFORE download so we only collect new ones
    pre_existing = set()
    for f in os.listdir(temp_dir):
        fp = os.path.join(temp_dir, f)
        if os.path.isfile(fp):
            pre_existing.add(fp)
    try:
        j = job.DownloadJob(url)
        j.run()
    except Exception as e:
        log.warning("[gallery-dl] Download failed for %s: %s", url, e)

    # Collect only NEW, non-temp, non-empty files
    downloaded = []
    for f in os.listdir(temp_dir):
        fp = os.path.join(temp_dir, f)
        if fp in pre_existing:
            continue
        if not os.path.isfile(fp):
            continue
        if _is_temp_file(fp):
            continue
        if os.path.getsize(fp) == 0:
            continue
        downloaded.append(fp)

    # Filter by max filesize if set
    if max_filesize and downloaded:
        downloaded = [f for f in downloaded if os.path.getsize(f) <= max_filesize]

    return downloaded


# ---------------------------------------------------------------------------
# yt-dlp backend
# ---------------------------------------------------------------------------


class _YtdlpLogger:
    """Custom logger that routes all yt-dlp output to our Red logger."""

    def debug(self, msg):
        # yt-dlp sends verbose info via debug
        log.debug("[yt-dlp] %s", msg)

    def info(self, msg):
        log.info("[yt-dlp] %s", msg)

    def warning(self, msg):
        log.warning("[yt-dlp] %s", msg)

    def error(self, msg):
        log.error("[yt-dlp] %s", msg)


def _ytdlp_download(
    url: str,
    temp_dir: str,
    progress_tracker: Optional[ProgressTracker] = None,
    cookies_file: Optional[str] = None,
    max_filesize: Optional[int] = None,
    max_duration: Optional[int] = None,
    audio_only: bool = False,
    hd_mode: bool = False,
    format_id: Optional[str] = None,
) -> Tuple[List[str], Optional[dict]]:
    """Run yt-dlp synchronously. Returns (list of file paths, info_dict or None).

    If format_id is provided, it overrides all format selection logic.
    Must be called via run_in_executor.
    """
    import yt_dlp

    def progress_hook(d):
        if progress_tracker is None:
            return
        status = d.get("status")
        if status == "downloading":
            progress_tracker.stage = "Downloading"
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total and total > 0:
                progress_tracker.percent = (downloaded / total) * 100
                progress_tracker.total_bytes = total
                progress_tracker.downloaded_bytes = downloaded
            else:
                progress_tracker.downloaded_bytes = downloaded
        elif status == "finished":
            progress_tracker.percent = 100.0
            progress_tracker.stage = "Processing"

    opts = {
        "outtmpl": os.path.join(temp_dir, "%(title).100B.%(ext)s"),
        "restrictfilenames": True,
        "noplaylist": True,
        "progress_hooks": [progress_hook],
        "logger": _YtdlpLogger(),
        "noprogress": True,
        "merge_output_format": "mp4",
        # Prevent .part files - write directly to final filename
        "nopart": True,
        # Delete intermediate files after merge (video-only + audio-only streams)
        "keepvideo": False,
        # Retry logic for transient HTTP errors (403, 429, etc.)
        "retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 3,
        # Use a realistic User-Agent to reduce 403 blocks
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        # Enable JS runtimes for YouTube signature/n-challenge solving.
        # yt-dlp defaults to only 'deno' if js_runtimes is not set.
        # We need 'node' since that's what's installed on the VPS.
        "js_runtimes": {
            "node": {},
            "deno": {},
            "bun": {},
            "quickjs": {},
        },
    }

    if cookies_file:
        opts["cookiefile"] = cookies_file

    if max_filesize:
        opts["max_filesize"] = max_filesize

    if max_duration:
        opts["match_filter"] = yt_dlp.utils.match_filter_func(
            f"duration <= {max_duration}"
        )

    # YouTube-specific: use multiple player clients to work around PO Token
    # requirements and 403 errors on datacenter IPs.
    # - "default" works if a PO Token provider plugin is installed
    # - "mweb" is the recommended client with PO Token plugin
    # - "tv" does NOT require PO Tokens (fallback for VPS without plugin)
    opts["extractor_args"] = {
        "youtube": {
            "player_client": ["default", "mweb", "tv"],
        },
    }

    if format_id:
        # User-selected format from resolution picker — override all format logic
        opts["format"] = format_id
        opts.pop("max_filesize", None)
    elif audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    elif hd_mode:
        # HD mode: force highest quality, no resolution/filesize restrictions
        opts["format"] = "bestvideo+bestaudio/best"
        opts.pop("max_filesize", None)
    else:
        # Prefer mp4 containers, fall back through progressively simpler formats.
        # The broad fallback chain avoids 403 issues where specific format
        # combinations are blocked by the server.
        # Note: TikTok is handled by the direct scraper, not yt-dlp.
        opts["format"] = (
            "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo+bestaudio/"
            "best[ext=mp4]/"
            "best"
        )

    info_dict = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        err_str = str(e).lower()
        log.error("[yt-dlp] DownloadError: %s", e, exc_info=True)
        if "duration" in err_str or "match_filter" in err_str:
            raise ValueError("Video exceeds maximum allowed duration.") from e
        raise
    except Exception as e:
        log.error("[yt-dlp] Unexpected exception for %s: %s", url, e, exc_info=True)
        raise

    # Log info_dict details for debugging
    if info_dict:
        log.debug(
            "[yt-dlp] Downloaded: title=%s format=%s resolution=%s",
            info_dict.get("title"),
            info_dict.get("format"),
            info_dict.get("resolution"),
        )

    # Collect output files - filter out temp/partial files and empty files
    downloaded = _collect_real_files(temp_dir)

    return downloaded, info_dict


# ---------------------------------------------------------------------------
# yt-dlp format extraction (for resolution picker)
# ---------------------------------------------------------------------------


def _ytdlp_extract_formats(
    url: str,
    cookies_file: Optional[str] = None,
) -> List[dict]:
    """Extract available formats/resolutions from a URL without downloading.

    Returns a deduplicated list of resolution options, sorted by height descending:
    [
        {"format_id": "137+140", "height": 1080, "label": "1080p",
         "filesize_approx": 52428800, "vcodec": "avc1", "acodec": "mp4a"},
        ...
    ]

    Must be called via run_in_executor (synchronous).
    """
    import yt_dlp

    opts = {
        "noplaylist": True,
        "logger": _YtdlpLogger(),
        "noprogress": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        "js_runtimes": {
            "node": {},
            "deno": {},
            "bun": {},
            "quickjs": {},
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["default", "mweb", "tv"],
            },
        },
    }

    if cookies_file:
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        return []

    formats = info.get("formats") or []
    if not formats:
        # Single-format media (e.g. direct file URL) — no picker needed
        return []

    # Group by resolution height, keeping best quality per height.
    # We want combined video+audio, so look for formats with both, or
    # pair the best video-only with best audio-only per height.
    best_audio = None
    best_audio_size = 0
    video_by_height: Dict[int, dict] = {}

    for f in formats:
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        height = f.get("height")
        fsize = f.get("filesize") or f.get("filesize_approx") or 0

        # Track best audio-only stream
        if (vcodec == "none" or not vcodec) and acodec and acodec != "none":
            if fsize > best_audio_size:
                best_audio = f
                best_audio_size = fsize

        # Skip audio-only or formats without height
        if not height or height <= 0:
            continue
        if vcodec == "none" or not vcodec:
            continue

        # Keep the best (largest filesize) format per height
        existing = video_by_height.get(height)
        if existing is None or fsize > (
            existing.get("filesize") or existing.get("filesize_approx") or 0
        ):
            video_by_height[height] = f

    if not video_by_height:
        return []

    # Build result list, sorted by height descending
    results = []
    audio_id = best_audio.get("format_id") if best_audio else None
    audio_size = best_audio_size

    for height in sorted(video_by_height.keys(), reverse=True):
        f = video_by_height[height]
        vid_id = f.get("format_id", "")
        vcodec = f.get("vcodec", "unknown")
        acodec_val = f.get("acodec", "none")
        vid_size = f.get("filesize") or f.get("filesize_approx") or 0

        # If this format has both video+audio, use it standalone
        if acodec_val and acodec_val != "none":
            fmt_id = vid_id
            total_size = vid_size
            acodec_display = acodec_val
        elif audio_id:
            # Combine video-only + best audio
            fmt_id = f"{vid_id}+{audio_id}"
            total_size = vid_size + audio_size
            acodec_display = (
                best_audio.get("acodec", "unknown") if best_audio else "unknown"
            )
        else:
            # Video-only, no audio available
            fmt_id = vid_id
            total_size = vid_size
            acodec_display = "none"

        # Clean up codec names for display
        vcodec_short = vcodec.split(".")[0] if "." in vcodec else vcodec
        codec_map = {
            "avc1": "H.264",
            "h264": "H.264",
            "vp9": "VP9",
            "vp09": "VP9",
            "av01": "AV1",
            "av1": "AV1",
            "hev1": "HEVC",
            "hevc": "HEVC",
            "hvc1": "HEVC",
        }
        vcodec_display = codec_map.get(vcodec_short, vcodec_short.upper())

        results.append(
            {
                "format_id": fmt_id,
                "height": height,
                "label": f"{height}p",
                "filesize_approx": total_size,
                "vcodec": vcodec_display,
                "acodec": acodec_display,
            }
        )

    return results


def _format_resolution_menu(formats: List[dict]) -> str:
    """Build a user-facing resolution picker menu from format list."""
    lines = ["**Available resolutions:**\n"]
    for i, fmt in enumerate(formats, 1):
        size_str = (
            _human_size(fmt["filesize_approx"])
            if fmt["filesize_approx"]
            else "unknown size"
        )
        lines.append(f"`{i}.` **{fmt['label']}** — ~{size_str} ({fmt['vcodec']})")
    lines.append(f"\nReply with a number (1-{len(formats)}) within 30 seconds.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ffmpeg compression
# ---------------------------------------------------------------------------


async def _ffmpeg_compress(
    input_path: str,
    output_path: str,
    target_size_bytes: int,
    progress_tracker: Optional[ProgressTracker] = None,
    timeout: int = 300,
) -> bool:
    """Two-pass ffmpeg compression to fit target size. Returns True on success."""
    import subprocess

    # Get duration via ffprobe
    try:
        probe = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            input_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(probe.communicate(), timeout=30)
        import json

        probe_data = json.loads(stdout)
        duration = float(probe_data["format"]["duration"])
    except Exception as e:
        log.warning("ffprobe failed: %s", e)
        return False

    if duration <= 0:
        return False

    # Calculate target video bitrate (bits/sec)
    # Reserve 128kbps for audio, use 95% of target to be safe
    audio_bitrate = 128_000
    target_total_bitrate = int((target_size_bytes * 8 * 0.95) / duration)
    video_bitrate = target_total_bitrate - audio_bitrate

    if video_bitrate < 100_000:  # Below 100kbps is unwatchable
        return False

    # Determine if we need to scale down
    scale_filter = ""
    if video_bitrate < 500_000:
        scale_filter = "-vf scale=-2:480"
    elif video_bitrate < 1_000_000:
        scale_filter = "-vf scale=-2:720"

    passlogfile = output_path + "_passlog"

    # Build common args
    scale_args = scale_filter.split() if scale_filter else []

    if progress_tracker:
        progress_tracker.stage = "Compressing"
        progress_tracker.percent = 0
        progress_tracker.total_bytes = None
        progress_tracker.downloaded_bytes = 0

    # Pass 1
    pass1_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-c:v",
        "libx264",
        "-b:v",
        str(video_bitrate),
        *scale_args,
        "-pass",
        "1",
        "-passlogfile",
        passlogfile,
        "-an",
        "-f",
        "null",
        "/dev/null",
    ]

    try:
        p1 = await asyncio.create_subprocess_exec(
            *pass1_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr1 = await asyncio.wait_for(p1.communicate(), timeout=timeout)
        if p1.returncode != 0:
            log.warning(
                "ffmpeg pass 1 failed: %s", stderr1.decode(errors="replace")[-500:]
            )
            return False
    except asyncio.TimeoutError:
        p1.kill()
        log.warning("ffmpeg pass 1 timed out")
        return False

    if progress_tracker:
        progress_tracker.percent = 50

    # Pass 2
    pass2_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-c:v",
        "libx264",
        "-b:v",
        str(video_bitrate),
        *scale_args,
        "-pass",
        "2",
        "-passlogfile",
        passlogfile,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        output_path,
    ]

    try:
        p2 = await asyncio.create_subprocess_exec(
            *pass2_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Parse progress from stderr
        stderr_data = b""
        try:
            _, stderr_data = await asyncio.wait_for(p2.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            p2.kill()
            log.warning("ffmpeg pass 2 timed out")
            return False

        if p2.returncode != 0:
            log.warning(
                "ffmpeg pass 2 failed: %s", stderr_data.decode(errors="replace")[-500:]
            )
            return False

    except Exception as e:
        log.warning("ffmpeg pass 2 error: %s", e)
        return False
    finally:
        # Clean up passlog files
        for ext in ("", "-0.log", "-0.log.mbtree"):
            try:
                os.remove(passlogfile + ext)
            except OSError:
                pass

    if progress_tracker:
        progress_tracker.percent = 100

    # Verify output exists and is smaller than target
    if not os.path.isfile(output_path):
        return False

    output_size = os.path.getsize(output_path)
    if output_size > target_size_bytes:
        log.info(
            "Compressed file still too large: %s > %s",
            _human_size(output_size),
            _human_size(target_size_bytes),
        )
        return False

    return True


# ---------------------------------------------------------------------------
# AnonDrop upload helper
# ---------------------------------------------------------------------------


async def _anondrop_upload(
    file_path: str,
    progress_tracker: Optional[ProgressTracker] = None,
    userkey: Optional[str] = None,
) -> Optional[str]:
    """Upload a file to AnonDrop.net. Returns the download URL or None on failure."""
    file_size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)

    if progress_tracker:
        progress_tracker.stage = "Uploading to AnonDrop"
        progress_tracker.percent = 0
        progress_tracker.total_bytes = file_size
        progress_tracker.downloaded_bytes = 0

    async with aiohttp.ClientSession() as session:
        if file_size <= 8 * 1024 * 1024:
            # Simple upload for files under 8MB
            return await _anondrop_simple_upload(
                session, file_path, filename, progress_tracker
            )
        else:
            # Chunked upload for larger files
            return await _anondrop_chunked_upload(
                session, file_path, filename, file_size, progress_tracker, userkey
            )


async def _anondrop_simple_upload(
    session: aiohttp.ClientSession,
    file_path: str,
    filename: str,
    progress_tracker: Optional[ProgressTracker],
) -> Optional[str]:
    """Simple POST upload for files under 8MB."""
    try:
        data = aiohttp.FormData()
        data.add_field("file", open(file_path, "rb"), filename=filename)
        async with session.post(
            "https://anondrop.net/upload",
            data=data,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                log.warning("AnonDrop simple upload failed: HTTP %s", resp.status)
                return None
            html = await resp.text()
            if progress_tracker:
                progress_tracker.percent = 100
            return _parse_anondrop_link(html)
    except Exception as e:
        log.warning("AnonDrop simple upload error: %s", e)
        return None


async def _anondrop_chunked_upload(
    session: aiohttp.ClientSession,
    file_path: str,
    filename: str,
    file_size: int,
    progress_tracker: Optional[ProgressTracker],
    userkey: Optional[str] = None,
) -> Optional[str]:
    """Chunked upload for files over 8MB."""
    try:
        # Get or register userkey
        if not userkey:
            userkey = await _anondrop_register(session)
            if not userkey:
                log.warning("AnonDrop: failed to get userkey")
                return None

        # Step 1: Initiate upload
        async with session.get(
            "https://anondrop.net/initiateupload",
            params={"filename": filename, "key": userkey},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                log.warning("AnonDrop initiate failed: HTTP %s", resp.status)
                return None
            session_hash = (await resp.text()).strip()

        if not session_hash:
            log.warning("AnonDrop: empty session hash")
            return None

        # Step 2: Upload chunks
        total_chunks = (file_size + ANONDROP_CHUNK_SIZE - 1) // ANONDROP_CHUNK_SIZE
        chunk_num = 0

        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(ANONDROP_CHUNK_SIZE)
                if not chunk:
                    break
                chunk_num += 1

                chunk_data = aiohttp.FormData()
                chunk_data.add_field(
                    "file",
                    chunk,
                    filename=f"chunk_{chunk_num}",
                    content_type="application/octet-stream",
                )
                async with session.post(
                    "https://anondrop.net/uploadchunk",
                    params={"session_hash": session_hash},
                    data=chunk_data,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        log.warning(
                            "AnonDrop chunk %d/%d failed: HTTP %s",
                            chunk_num,
                            total_chunks,
                            resp.status,
                        )
                        return None

                if progress_tracker:
                    progress_tracker.percent = (chunk_num / total_chunks) * 100
                    progress_tracker.downloaded_bytes = min(
                        chunk_num * ANONDROP_CHUNK_SIZE, file_size
                    )

        # Step 3: End upload
        async with session.get(
            "https://anondrop.net/endupload",
            params={"session_hash": session_hash},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                log.warning("AnonDrop end upload failed: HTTP %s", resp.status)
                return None
            html = await resp.text()
            if progress_tracker:
                progress_tracker.percent = 100
            return _parse_anondrop_link(html)

    except Exception as e:
        log.warning("AnonDrop chunked upload error: %s", e)
        return None


async def _anondrop_register(session: aiohttp.ClientSession) -> Optional[str]:
    """Register on AnonDrop to get a userkey."""
    try:
        async with session.get(
            "https://anondrop.net/register",
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                log.warning("AnonDrop register HTTP %s", resp.status)
                return None
            html = await resp.text()
            # Look for localStorage.setItem('userkey', '<number>')
            match = re.search(
                r"localStorage\.setItem\(['\"]userkey['\"],\s*['\"](\d+)['\"]", html
            )
            if match:
                return match.group(1)
            log.warning("AnonDrop register: no userkey in response: %s", html[:200])
    except Exception as e:
        log.warning("AnonDrop register error: %s", e)
    return None


def _parse_anondrop_link(html: str) -> Optional[str]:
    """Extract the AnonDrop download link from response HTML.

    Returns the full link including filename if present, e.g.:
    https://anondrop.net/1480216769674215507/Daddy_Yankee_-_Limbo.mp4
    """
    # Look for anondrop.net/<id>/<filename> pattern (with filename)
    match = re.search(r"(https?://anondrop\.net/\d+/[^\s\"'<>]+)", html)
    if match:
        return match.group(1)

    # Fallback: anondrop.net/<id> pattern (without filename)
    match = re.search(r"(https?://anondrop\.net/[a-zA-Z0-9]+)", html)
    if match:
        link = match.group(1)
        # Filter out the base URL and known endpoints
        path = urlparse(link).path.strip("/")
        if path and path not in (
            "upload",
            "register",
            "initiateupload",
            "uploadchunk",
            "endupload",
            "embed",
        ):
            return link
    # Also try just the path pattern
    match = re.search(r"anondrop\.net/([a-zA-Z0-9]{6,})", html)
    if match:
        return f"https://anondrop.net/{match.group(1)}"
    return None


def _anondrop_to_embed(link: str, filename: Optional[str] = None) -> str:
    """Convert a regular AnonDrop link to an embed link.

    The embed URL MUST include the filename for AnonDrop's player to work.
    If the parsed link already contains a filename, it is kept.  Otherwise
    the caller-supplied *filename* is appended.

    Input:  https://anondrop.net/1480216769674215507/filename.mp4
    Output: https://anondrop.net/embed/1480216769674215507/filename.mp4

    Input:  https://anondrop.net/1480216769674215507  (+ filename="video.mp4")
    Output: https://anondrop.net/embed/1480216769674215507/video.mp4

    If the link doesn't match the expected pattern, returns the original link.
    """
    parsed = urlparse(link)
    path = parsed.path.lstrip("/")

    # Strip existing /embed/ prefix so we can rebuild uniformly
    if path.startswith("embed/"):
        path = path[len("embed/") :]

    if not path:
        return link

    # Check whether the path already contains a filename (id/filename)
    parts = path.split("/", 1)
    if len(parts) == 1 and filename:
        # Only the ID — append the filename
        path = f"{parts[0]}/{filename}"

    return f"https://anondrop.net/embed/{path}"


# ---------------------------------------------------------------------------
# The Cog
# ---------------------------------------------------------------------------


class SabDownloader(commands.Cog):
    """Download media from Instagram, TikTok, YouTube, Twitter, and 1000+ other sites."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=5927381046, force_registration=True
        )
        self.config.register_guild(
            enabled=True,
            max_duration=600,
            allowed_channels=[],
            delete_command=True,
            cooldown=30,
            anondrop_enabled=True,
        )
        self.config.register_global(
            cookies_file=None,
            max_concurrent=3,
            anondrop_userkey=None,
            log_channel=None,
            delete_command=True,
        )
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._cooldowns: Dict[int, float] = {}  # user_id -> last_use timestamp

    async def cog_load(self) -> None:
        """Initialize semaphore on cog load."""
        max_concurrent = await self.config.max_concurrent()
        self._semaphore = asyncio.Semaphore(max_concurrent)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_cooldown(self, user_id: int, cooldown: int) -> Optional[float]:
        """Check if user is on cooldown. Returns remaining seconds or None."""
        now = time.time()
        last_use = self._cooldowns.get(user_id, 0.0)
        remaining = cooldown - (now - last_use)
        if remaining > 0:
            return remaining
        return None

    def _set_cooldown(self, user_id: int) -> None:
        """Mark user as having just used the download command."""
        self._cooldowns[user_id] = time.time()

    async def _update_progress(
        self, message: discord.Message, tracker: ProgressTracker
    ) -> Optional[discord.Message]:
        """Edit the status message with current progress. Returns the message."""
        try:
            new_content = tracker.format_bar()
            if message.content != new_content:
                await message.edit(content=new_content)
        except (discord.NotFound, discord.HTTPException):
            pass
        return message

    async def _progress_loop(
        self,
        message: discord.Message,
        tracker: ProgressTracker,
        done_event: asyncio.Event,
    ) -> None:
        """Background task that updates progress message periodically."""
        while not done_event.is_set():
            await self._update_progress(message, tracker)
            try:
                await asyncio.wait_for(
                    done_event.wait(), timeout=PROGRESS_UPDATE_INTERVAL
                )
            except asyncio.TimeoutError:
                continue

    def _is_video(self, filepath: str) -> bool:
        """Check if a file is a video based on extension."""
        ext = os.path.splitext(filepath)[1].lower()
        return ext in (
            ".mp4",
            ".mkv",
            ".webm",
            ".avi",
            ".mov",
            ".flv",
            ".wmv",
            ".m4v",
            ".ts",
        )

    def _is_audio(self, filepath: str) -> bool:
        """Check if a file is audio based on extension."""
        ext = os.path.splitext(filepath)[1].lower()
        return ext in (".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".aac", ".wma")

    def _is_image(self, filepath: str) -> bool:
        """Check if a file is an image based on extension."""
        ext = os.path.splitext(filepath)[1].lower()
        return ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff")

    async def _try_download(
        self,
        url: str,
        temp_dir: str,
        tracker: ProgressTracker,
        cookies_file: Optional[str],
        max_filesize: int,
        max_duration: int,
        audio_only: bool = False,
        hd_mode: bool = False,
        format_id: Optional[str] = None,
    ) -> Tuple[List[str], Optional[dict]]:
        """Try downloading with gallery-dl first, then yt-dlp (or vice versa depending on domain).

        Returns (list of file paths, info_dict or None).
        """
        domain = _get_domain(url)
        info_dict = None

        # Decide order based on domain
        if domain in _TIKTOK_DOMAINS or "tiktok.com" in domain:
            backends = ["tiktok"]
        elif domain in _YTDLP_PRIORITY:
            backends = ["ytdlp", "gallerydl"]
        elif domain in _GALLERY_DL_PRIORITY:
            backends = ["gallerydl", "ytdlp"]
        else:
            # Default: gallery-dl first for image sites, yt-dlp for everything else
            backends = ["gallerydl", "ytdlp"]

        # Audio extraction on TikTok uses the direct scraper's built-in audio mode
        if audio_only and "tiktok" not in backends:
            backends = ["ytdlp"]

        # HD mode on TikTok uses the direct scraper's built-in HD mode
        if hd_mode and "tiktok" not in backends:
            backends = ["ytdlp"]

        log.info(
            "[_try_download] URL=%s domain=%s backends=%s",
            url,
            domain,
            backends,
        )

        last_error = None

        for backend in backends:
            if backend == "tiktok":
                try:
                    tracker.stage = "Downloading"
                    tracker.percent = 0
                    files, info_dict = await _tiktok_download(
                        url=url,
                        temp_dir=temp_dir,
                        progress_tracker=tracker,
                        cookies_file=cookies_file,
                        max_filesize=max_filesize,
                        max_duration=max_duration,
                        audio_only=audio_only,
                        hd_mode=hd_mode,
                    )
                    if files:
                        return files, info_dict
                except ValueError:
                    # Duration exceeded - re-raise as-is
                    raise
                except Exception as e:
                    last_error = e
                    log.warning("[_try_download] tiktok scraper failed: %s", e)

                _purge_temp_files(temp_dir)

            elif backend == "gallerydl":
                try:
                    tracker.stage = "Downloading"
                    tracker.percent = None  # gallery-dl = indeterminate
                    files = await self.bot.loop.run_in_executor(
                        None,
                        partial(
                            _gallery_dl_download,
                            url=url,
                            temp_dir=temp_dir,
                            cookies_file=cookies_file,
                            max_filesize=max_filesize,
                        ),
                    )
                    if files:
                        return files, None
                except Exception as e:
                    last_error = e
                    log.warning("[_try_download] gallery-dl failed: %s", e)

                _purge_temp_files(temp_dir)

            elif backend == "ytdlp":
                try:
                    tracker.stage = "Downloading"
                    tracker.percent = 0
                    files, info_dict = await self.bot.loop.run_in_executor(
                        None,
                        partial(
                            _ytdlp_download,
                            url=url,
                            temp_dir=temp_dir,
                            progress_tracker=tracker,
                            cookies_file=cookies_file,
                            max_filesize=max_filesize,
                            max_duration=max_duration,
                            audio_only=audio_only,
                            hd_mode=hd_mode,
                            format_id=format_id,
                        ),
                    )
                    if files:
                        return files, info_dict
                except ValueError:
                    # Duration exceeded - re-raise as-is
                    raise
                except Exception as e:
                    last_error = e
                    log.warning("[_try_download] yt-dlp failed: %s", e)

                _purge_temp_files(temp_dir)

        log.error(
            "[_try_download] All backends exhausted for %s. last_error=%s",
            url,
            last_error,
        )
        if last_error:
            raise last_error
        raise RuntimeError("No files were downloaded.")

    async def _handle_file_upload(
        self,
        ctx: commands.Context,
        files: List[str],
        status_msg: discord.Message,
        tracker: ProgressTracker,
        url: str,
        platform: str,
        guild_config: dict,
        hd_mode: bool = False,
    ) -> None:
        """Handle uploading files to Discord (with compression/anondrop fallback).

        In hd_mode, all files go directly to AnonDrop (no compression, no Discord upload).
        """
        filesize_limit = ctx.guild.filesize_limit if ctx.guild else 25 * 1024 * 1024
        anondrop_enabled = guild_config["anondrop_enabled"]
        anondrop_userkey = await self.config.anondrop_userkey()
        global_delete = await self.config.delete_command()
        # Never delete command messages in DMs
        should_delete = (
            guild_config["delete_command"] and global_delete if ctx.guild else False
        )

        uploaded_files = []
        anondrop_links = []
        total_original_size = 0
        total_compressed_size = 0
        compression_used = False
        anondrop_used = False

        # HD mode: skip Discord upload entirely, send everything to AnonDrop
        if hd_mode:
            for filepath in files:
                file_size = os.path.getsize(filepath)
                total_original_size += file_size
                total_compressed_size += file_size
                fname = os.path.basename(filepath)

                tracker.stage = "Uploading to AnonDrop"
                tracker.percent = 0

                link = await _anondrop_upload(
                    file_path=filepath,
                    progress_tracker=tracker,
                    userkey=anondrop_userkey,
                )
                if link:
                    # Use embed URL so Discord auto-embeds the player
                    link = _anondrop_to_embed(link, filename=fname)
                    anondrop_links.append(link)
                    anondrop_used = True
                else:
                    log.warning("HD AnonDrop upload failed for %s", fname)

            # Post results — send as plain text so Discord auto-embeds
            # the video player from AnonDrop's og:video meta tags
            if anondrop_links:
                links_text = "\n".join(anondrop_links)
                await ctx.send(links_text)
            else:
                try:
                    await status_msg.edit(
                        content="Failed to upload HD file to AnonDrop."
                    )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return

            # Delete command message if configured (not applicable for slash commands)
            if should_delete and not ctx.interaction:
                try:
                    await ctx.message.delete()
                except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                    pass

            # Delete status message
            try:
                await status_msg.delete()
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                pass

            # Log channel + modlog (HD mode)
            if ctx.guild:
                try:
                    await modlog.create_case(
                        bot=self.bot,
                        guild=ctx.guild,
                        created_at=ctx.message.created_at
                        if not ctx.interaction
                        else discord.utils.utcnow(),
                        action_type="mediadownload",
                        user=ctx.author,
                        moderator=ctx.guild.me,
                        reason=(
                            f"HD Media download | Platform: {platform} | "
                            f"URL: {url} | "
                            f"Size: {_human_size(total_original_size)} | "
                            f"AnonDrop: {', '.join(anondrop_links)} | "
                            f"Channel: #{ctx.channel.name}"
                        ),
                    )
                except Exception as e:
                    log.debug("Failed to create modlog case: %s", e)

            log_channel_id = await self.config.log_channel()
            if log_channel_id:
                log_channel = self.bot.get_channel(log_channel_id)
                if log_channel:
                    log_embed = discord.Embed(
                        title="HD Media Download",
                        color=discord.Color.gold(),
                        timestamp=ctx.message.created_at
                        if not ctx.interaction
                        else discord.utils.utcnow(),
                    )
                    log_embed.set_author(
                        name=f"{ctx.author} ({ctx.author.id})",
                        icon_url=ctx.author.display_avatar.url,
                    )
                    log_embed.add_field(
                        name="User",
                        value=f"{ctx.author.mention} (`{ctx.author.id}`)",
                        inline=True,
                    )
                    log_embed.add_field(name="Platform", value=platform, inline=True)
                    log_embed.add_field(
                        name="Channel",
                        value=f"{ctx.channel.mention}" if ctx.guild else "DM",
                        inline=True,
                    )
                    log_embed.add_field(
                        name="Server",
                        value=(
                            f"{ctx.guild.name} (`{ctx.guild.id}`)"
                            if ctx.guild
                            else "Direct Message"
                        ),
                        inline=True,
                    )
                    log_embed.add_field(name="URL", value=url, inline=False)
                    log_embed.add_field(
                        name="Size",
                        value=_human_size(total_original_size),
                        inline=True,
                    )
                    log_embed.add_field(
                        name="AnonDrop",
                        value="\n".join(anondrop_links),
                        inline=False,
                    )
                    log_embed.add_field(
                        name="Files",
                        value=str(len(files)),
                        inline=True,
                    )
                    footer_name = ctx.guild.name if ctx.guild else "Direct Message"
                    footer_icon = (
                        ctx.guild.icon.url if ctx.guild and ctx.guild.icon else None
                    )
                    log_embed.set_footer(text=footer_name, icon_url=footer_icon)
                    try:
                        await log_channel.send(embed=log_embed)
                    except discord.HTTPException as e:
                        log.debug("Failed to send download log embed: %s", e)

            return  # HD mode complete

        for filepath in files:
            file_size = os.path.getsize(filepath)
            total_original_size += file_size
            fname = os.path.basename(filepath)

            if file_size <= filesize_limit:
                # Fits directly - upload to Discord
                uploaded_files.append(filepath)
                total_compressed_size += file_size
                continue

            # File too large - try compression if it's a video
            if self._is_video(filepath):
                tracker.stage = "Compressing"
                tracker.percent = 0

                compressed_path = filepath + ".compressed.mp4"
                success = await _ffmpeg_compress(
                    input_path=filepath,
                    output_path=compressed_path,
                    target_size_bytes=filesize_limit,
                    progress_tracker=tracker,
                )

                if success and os.path.isfile(compressed_path):
                    compressed_size = os.path.getsize(compressed_path)
                    if compressed_size <= filesize_limit:
                        uploaded_files.append(compressed_path)
                        total_compressed_size += compressed_size
                        compression_used = True
                        continue
                    # Compressed file still too large
                else:
                    log.warning("ffmpeg compression failed for %s", fname)

            # Still too large or not a video - try AnonDrop
            if anondrop_enabled:
                link = await _anondrop_upload(
                    file_path=filepath,
                    progress_tracker=tracker,
                    userkey=anondrop_userkey,
                )
                if link:
                    # Use embed URL so Discord auto-embeds
                    link = _anondrop_to_embed(link, filename=fname)
                    anondrop_links.append(link)
                    anondrop_used = True
                    total_compressed_size += file_size
                    continue

            # Last resort - skip this file
            log.warning(
                "File %s (%s) too large and all fallbacks failed",
                filepath,
                _human_size(file_size),
            )

        # Build the user-facing embed
        total_size_str = _human_size(total_original_size)
        if compression_used:
            total_size_str += f" (compressed to {_human_size(total_compressed_size)})"

        result_embed = discord.Embed(color=discord.Color.blurple())
        result_embed.set_author(
            name=ctx.author.display_name,
            icon_url=ctx.author.display_avatar.url,
        )
        result_embed.set_footer(text=f"{platform} | {total_size_str}")

        # Upload to Discord
        if uploaded_files:
            # Discord allows up to 10 attachments per message
            for i in range(0, len(uploaded_files), 10):
                batch = uploaded_files[i : i + 10]
                discord_files = []
                for fp in batch:
                    try:
                        discord_files.append(
                            discord.File(fp, filename=_sanitize_discord_filename(fp))
                        )
                    except Exception as e:
                        log.warning("Failed to create discord.File for %s: %s", fp, e)

                if discord_files:
                    try:
                        # First batch gets the embed, subsequent batches are plain
                        if i == 0:
                            await ctx.send(embed=result_embed, files=discord_files)
                        else:
                            await ctx.send(files=discord_files)
                    except discord.HTTPException as e:
                        log.warning("Failed to upload files to Discord: %s", e)
                        # Try AnonDrop fallback for each file
                        if anondrop_enabled:
                            for fp in batch:
                                link = await _anondrop_upload(
                                    file_path=fp,
                                    progress_tracker=tracker,
                                    userkey=anondrop_userkey,
                                )
                                if link:
                                    link = _anondrop_to_embed(
                                        link, filename=os.path.basename(fp)
                                    )
                                    anondrop_links.append(link)
                                    anondrop_used = True

        # Post AnonDrop links — send as plain text so Discord auto-embeds
        # the video player from AnonDrop's og:video meta tags
        if anondrop_links:
            links_text = "\n".join(anondrop_links)
            await ctx.send(links_text)

        # Delete command message if configured (not applicable for slash commands)
        if should_delete and not ctx.interaction:
            try:
                await ctx.message.delete()
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                pass

        # Delete status message
        try:
            await status_msg.delete()
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass

        # Modlog entry (guild only)
        if ctx.guild:
            try:
                await modlog.create_case(
                    bot=self.bot,
                    guild=ctx.guild,
                    created_at=ctx.message.created_at
                    if not ctx.interaction
                    else discord.utils.utcnow(),
                    action_type="mediadownload",
                    user=ctx.author,
                    moderator=ctx.guild.me,
                    reason=(
                        f"Media download | Platform: {platform} | "
                        f"URL: {url} | "
                        f"Original size: {_human_size(total_original_size)} | "
                        f"{'Compressed to: ' + _human_size(total_compressed_size) + ' | ' if compression_used else ''}"
                        f"{'AnonDrop fallback used | ' if anondrop_used else ''}"
                        f"Channel: #{ctx.channel.name}"
                    ),
                )
            except Exception as e:
                log.debug("Failed to create modlog case: %s", e)

        # Send to log channel if configured (bot-wide)
        log_channel_id = await self.config.log_channel()
        if log_channel_id:
            log_channel = self.bot.get_channel(log_channel_id)
            if log_channel:
                log_embed = discord.Embed(
                    title="Media Download",
                    color=discord.Color.blue(),
                    timestamp=ctx.message.created_at
                    if not ctx.interaction
                    else discord.utils.utcnow(),
                )
                log_embed.set_author(
                    name=f"{ctx.author} ({ctx.author.id})",
                    icon_url=ctx.author.display_avatar.url,
                )
                log_embed.add_field(
                    name="User",
                    value=f"{ctx.author.mention} (`{ctx.author.id}`)",
                    inline=True,
                )
                log_embed.add_field(name="Platform", value=platform, inline=True)
                log_embed.add_field(
                    name="Channel",
                    value=f"{ctx.channel.mention}" if ctx.guild else "DM",
                    inline=True,
                )
                log_embed.add_field(
                    name="Server",
                    value=(
                        f"{ctx.guild.name} (`{ctx.guild.id}`)"
                        if ctx.guild
                        else "Direct Message"
                    ),
                    inline=True,
                )
                log_embed.add_field(name="URL", value=url, inline=False)
                log_embed.add_field(
                    name="Original Size",
                    value=_human_size(total_original_size),
                    inline=True,
                )
                if compression_used:
                    log_embed.add_field(
                        name="Compressed Size",
                        value=_human_size(total_compressed_size),
                        inline=True,
                    )
                if anondrop_used:
                    log_embed.add_field(
                        name="AnonDrop",
                        value="\n".join(anondrop_links) if anondrop_links else "Yes",
                        inline=False,
                    )
                log_embed.add_field(
                    name="Files",
                    value=str(len(files)),
                    inline=True,
                )
                footer_name = ctx.guild.name if ctx.guild else "Direct Message"
                footer_icon = (
                    ctx.guild.icon.url if ctx.guild and ctx.guild.icon else None
                )
                log_embed.set_footer(text=footer_name, icon_url=footer_icon)
                try:
                    await log_channel.send(embed=log_embed)
                except discord.HTTPException as e:
                    log.debug("Failed to send download log embed: %s", e)

    # ------------------------------------------------------------------
    # Command group: [p]sabdownloader (admin config)
    # ------------------------------------------------------------------

    @commands.group(aliases=["sabdl"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def sabdownloader(self, ctx: commands.Context):
        """SabDownloader configuration commands."""
        pass

    @sabdownloader.command(name="settings")
    async def sd_settings(self, ctx: commands.Context):
        """Display all current SabDownloader settings."""
        config = await self.config.guild(ctx.guild).all()
        global_config = await self.config.all()

        allowed = config["allowed_channels"]
        if allowed:
            channels = []
            for cid in allowed:
                ch = ctx.guild.get_channel(cid)
                channels.append(ch.mention if ch else f"Unknown (`{cid}`)")
            channels_str = ", ".join(channels)
        else:
            channels_str = "All channels"

        embed = discord.Embed(
            title="SabDownloader Settings",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Enabled", value=str(config["enabled"]), inline=True)
        embed.add_field(
            name="Max Duration", value=f"{config['max_duration']}s", inline=True
        )
        embed.add_field(name="Cooldown", value=f"{config['cooldown']}s", inline=True)
        embed.add_field(
            name="Delete Command", value=str(config["delete_command"]), inline=True
        )
        embed.add_field(
            name="AnonDrop Fallback", value=str(config["anondrop_enabled"]), inline=True
        )
        embed.add_field(
            name="Upload Limit",
            value=_human_size(ctx.guild.filesize_limit),
            inline=True,
        )
        embed.add_field(name="Allowed Channels", value=channels_str, inline=False)

        # Log channel
        if await self.bot.is_owner(ctx.author):
            log_channel_id = global_config.get("log_channel")
            if log_channel_id:
                lc = self.bot.get_channel(log_channel_id)
                log_ch_str = lc.mention if lc else f"Unknown (`{log_channel_id}`)"
            else:
                log_ch_str = "Not set"
            embed.add_field(name="Log Channel", value=log_ch_str, inline=True)

        # Global (bot owner only info)
        if await self.bot.is_owner(ctx.author):
            cookies = global_config["cookies_file"] or "Not set"
            embed.add_field(name="Cookies File", value=cookies, inline=True)
            embed.add_field(
                name="Max Concurrent",
                value=str(global_config["max_concurrent"]),
                inline=True,
            )
            embed.add_field(
                name="Global Delete",
                value=str(global_config.get("delete_command", True)),
                inline=True,
            )

        embed.set_footer(
            text=ctx.guild.name,
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None,
        )
        await ctx.send(embed=embed)

    @sabdownloader.command(name="toggle")
    async def sd_toggle(self, ctx: commands.Context):
        """Enable or disable SabDownloader for this server."""
        current = await self.config.guild(ctx.guild).enabled()
        new_val = not current
        await self.config.guild(ctx.guild).enabled.set(new_val)
        state = "enabled" if new_val else "disabled"
        await ctx.send(f"SabDownloader is now **{state}**.")

    @sabdownloader.command(name="maxduration")
    async def sd_maxduration(self, ctx: commands.Context, seconds: int):
        """Set the maximum allowed video duration in seconds (default: 600)."""
        if seconds < 10:
            await ctx.send("Minimum duration is 10 seconds.")
            return
        if seconds > 7200:
            await ctx.send("Maximum duration is 7200 seconds (2 hours).")
            return
        await self.config.guild(ctx.guild).max_duration.set(seconds)
        await ctx.send(f"Max duration set to **{seconds}** seconds.")

    @sabdownloader.command(name="cooldown")
    async def sd_cooldown(self, ctx: commands.Context, seconds: int):
        """Set the per-user cooldown in seconds (default: 30)."""
        if seconds < 0:
            await ctx.send("Cooldown cannot be negative.")
            return
        if seconds > 600:
            await ctx.send("Maximum cooldown is 600 seconds (10 minutes).")
            return
        await self.config.guild(ctx.guild).cooldown.set(seconds)
        await ctx.send(f"Cooldown set to **{seconds}** seconds.")

    @sabdownloader.command(name="deletecommand")
    async def sd_deletecommand(self, ctx: commands.Context):
        """Toggle whether the user's command message is deleted after download (per-server)."""
        current = await self.config.guild(ctx.guild).delete_command()
        new_val = not current
        await self.config.guild(ctx.guild).delete_command.set(new_val)
        state = "enabled" if new_val else "disabled"
        await ctx.send(f"Delete command message is now **{state}** for this server.")

    @sabdownloader.command(name="globaldelete")
    @commands.is_owner()
    async def sd_globaldelete(self, ctx: commands.Context):
        """(Bot Owner) Toggle command message deletion bot-wide. Overrides per-server setting when off."""
        current = await self.config.delete_command()
        new_val = not current
        await self.config.delete_command.set(new_val)
        state = "enabled" if new_val else "disabled"
        await ctx.send(f"Global delete command message is now **{state}**.")

    @sabdownloader.group(name="allowedchannels", invoke_without_command=True)
    async def sd_allowedchannels(self, ctx: commands.Context):
        """Manage channels where downloads are allowed. Empty = all channels."""
        pass

    @sd_allowedchannels.command(name="add")
    async def sd_allowedchannels_add(
        self, ctx: commands.Context, channel: discord.TextChannel
    ):
        """Add a channel to the allowed list."""
        async with self.config.guild(ctx.guild).allowed_channels() as channels:
            if channel.id not in channels:
                channels.append(channel.id)
        await ctx.send(f"Added {channel.mention} to allowed channels.")

    @sd_allowedchannels.command(name="remove")
    async def sd_allowedchannels_remove(
        self, ctx: commands.Context, channel: discord.TextChannel
    ):
        """Remove a channel from the allowed list."""
        async with self.config.guild(ctx.guild).allowed_channels() as channels:
            if channel.id in channels:
                channels.remove(channel.id)
                await ctx.send(f"Removed {channel.mention} from allowed channels.")
            else:
                await ctx.send(f"{channel.mention} is not in the allowed list.")

    @sabdownloader.group(name="anondrop", invoke_without_command=True)
    async def sd_anondrop(self, ctx: commands.Context):
        """AnonDrop fallback configuration."""
        pass

    @sd_anondrop.command(name="toggle")
    async def sd_anondrop_toggle(self, ctx: commands.Context):
        """Enable or disable AnonDrop fallback for oversized files."""
        current = await self.config.guild(ctx.guild).anondrop_enabled()
        new_val = not current
        await self.config.guild(ctx.guild).anondrop_enabled.set(new_val)
        state = "enabled" if new_val else "disabled"
        await ctx.send(f"AnonDrop fallback is now **{state}**.")

    @sabdownloader.command(name="logchannel")
    @commands.is_owner()
    async def sd_logchannel(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ):
        """(Bot Owner) Set a channel to log all downloads bot-wide. Omit channel to disable."""
        if channel is None:
            await self.config.log_channel.set(None)
            await ctx.send("Download log channel cleared.")
        else:
            await self.config.log_channel.set(channel.id)
            await ctx.send(f"Download log channel set to {channel.mention}.")

    @sabdownloader.command(name="cookies")
    @commands.is_owner()
    async def sd_cookies(self, ctx: commands.Context, path: str):
        """(Bot Owner) Set the path to a Netscape cookies.txt file for authenticated downloads."""
        if not os.path.isfile(path):
            await ctx.send(f"File not found: `{path}`")
            return
        await self.config.cookies_file.set(path)
        await ctx.send(f"Cookies file set to `{path}`.")

    @sabdownloader.command(name="maxconcurrent")
    @commands.is_owner()
    async def sd_maxconcurrent(self, ctx: commands.Context, count: int):
        """(Bot Owner) Set the maximum number of concurrent downloads."""
        if count < 1 or count > 10:
            await ctx.send("Must be between 1 and 10.")
            return
        await self.config.max_concurrent.set(count)
        self._semaphore = asyncio.Semaphore(count)
        await ctx.send(f"Max concurrent downloads set to **{count}**.")

    # ------------------------------------------------------------------
    # Download commands: [p]dl
    # ------------------------------------------------------------------

    @commands.hybrid_group(fallback="download")
    @app_commands.describe(url="The URL to download media from")
    async def dl(self, ctx: commands.Context, url: str):
        """Download media from a URL and upload to Discord."""
        await self._do_download(ctx, url, audio_only=False)

    @dl.command(name="audio")
    @app_commands.describe(url="The URL to extract audio from")
    async def dl_audio(self, ctx: commands.Context, url: str):
        """Extract audio from a URL as MP3."""
        await self._do_download(ctx, url, audio_only=True)

    @dl.command(name="hd")
    @app_commands.describe(url="The URL to download in HD quality")
    async def dl_hd(self, ctx: commands.Context, url: str):
        """Download in highest quality and upload to AnonDrop.

        Shows available resolutions and lets you pick one.
        Bypasses Discord's file size limit by uploading directly
        to AnonDrop.net. No compression is applied - full quality
        is preserved.
        """
        # Defer slash command interaction (buys us 15 minutes)
        if ctx.interaction:
            await ctx.defer()

        # Require AnonDrop to be enabled for HD mode
        if ctx.guild:
            guild_config = await self.config.guild(ctx.guild).all()
            if not guild_config["anondrop_enabled"]:
                await ctx.send(
                    "HD downloads require AnonDrop to be enabled. "
                    "An admin can enable it with `[p]sabdownloader anondrop toggle`.",
                    delete_after=15,
                )
                return

        # URL validation
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            await ctx.send(
                "Invalid URL. Must start with `http://` or `https://`.",
                delete_after=10,
            )
            return

        if _is_private_url(url):
            await ctx.send("That URL is not allowed.", delete_after=10)
            return

        # Show "fetching" status
        status_msg = await ctx.send("Fetching available resolutions...")

        cookies_file = await self.config.cookies_file()

        try:
            formats = await self.bot.loop.run_in_executor(
                None,
                partial(
                    _ytdlp_extract_formats,
                    url=url,
                    cookies_file=cookies_file,
                ),
            )
        except Exception as e:
            log.warning("[dl_hd] Format extraction failed for %s: %s", url, e)
            # Fall back to downloading best quality without picker
            try:
                await status_msg.delete()
            except (discord.NotFound, discord.HTTPException):
                pass
            await self._do_download(ctx, url, hd_mode=True, _deferred=True)
            return

        if not formats:
            # No format info (direct file, single format, etc.) — download best
            try:
                await status_msg.delete()
            except (discord.NotFound, discord.HTTPException):
                pass
            await self._do_download(ctx, url, hd_mode=True, _deferred=True)
            return

        # Show resolution picker
        menu_text = _format_resolution_menu(formats)
        try:
            await status_msg.edit(content=menu_text)
        except (discord.NotFound, discord.HTTPException):
            status_msg = await ctx.send(menu_text)

        # Wait for user's choice
        def check(m: discord.Message) -> bool:
            return (
                m.author.id == ctx.author.id
                and m.channel.id == ctx.channel.id
                and m.content.strip().isdigit()
            )

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=30.0)
        except asyncio.TimeoutError:
            try:
                await status_msg.edit(content="Resolution selection timed out.")
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        choice = int(reply.content.strip())
        if choice < 1 or choice > len(formats):
            try:
                await status_msg.edit(
                    content=f"Invalid choice. Please use a number between 1 and {len(formats)}."
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        selected = formats[choice - 1]

        # Clean up the picker messages
        try:
            await status_msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass
        try:
            await reply.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

        # Download with selected format
        await self._do_download(
            ctx, url, hd_mode=True, format_id=selected["format_id"], _deferred=True
        )

    async def _do_download(
        self,
        ctx: commands.Context,
        url: str,
        audio_only: bool = False,
        hd_mode: bool = False,
        format_id: Optional[str] = None,
        _deferred: bool = False,
    ) -> None:
        """Core download flow."""
        # Defer slash command interaction if not already deferred
        if ctx.interaction and not _deferred:
            await ctx.defer()

        # --- Validation ---
        if ctx.guild:
            guild_config = await self.config.guild(ctx.guild).all()
        else:
            # DM context: use sensible defaults (no guild config available)
            guild_config = {
                "enabled": True,
                "max_duration": 600,
                "allowed_channels": [],
                "delete_command": False,
                "cooldown": 30,
                "anondrop_enabled": True,
            }

        if not guild_config["enabled"]:
            await ctx.send("SabDownloader is disabled in this server.", delete_after=10)
            return

        # Channel restriction (skip in DMs)
        allowed = guild_config["allowed_channels"]
        if allowed and ctx.channel.id not in allowed:
            await ctx.send(
                "Downloads are not allowed in this channel.", delete_after=10
            )
            return

        # Cooldown
        cooldown = guild_config["cooldown"]
        remaining = self._check_cooldown(ctx.author.id, cooldown)
        if remaining is not None and not await self.bot.is_owner(ctx.author):
            await ctx.send(
                f"Please wait **{remaining:.0f}s** before downloading again.",
                delete_after=10,
            )
            return

        # URL validation
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            await ctx.send(
                "Invalid URL. Must start with `http://` or `https://`.", delete_after=10
            )
            return

        # SSRF prevention
        if _is_private_url(url):
            await ctx.send("That URL is not allowed.", delete_after=10)
            return

        # Instagram cookies check
        domain = _get_domain(url)
        cookies_file = await self.config.cookies_file()
        if domain in ("instagram.com", "www.instagram.com") and not cookies_file:
            await ctx.send(
                "Instagram requires authentication. "
                "Ask the bot owner to configure cookies with "
                "`[p]sabdownloader cookies <path>`.",
                delete_after=15,
            )
            return

        # Platform detection
        platform = _detect_platform(url)

        # Concurrency check
        if self._semaphore is None:
            max_concurrent = await self.config.max_concurrent()
            self._semaphore = asyncio.Semaphore(max_concurrent)

        if self._semaphore.locked():
            await ctx.send(
                "Too many downloads in progress. Please try again shortly.",
                delete_after=10,
            )
            return

        # --- Download ---
        temp_dir = _make_temp_dir()
        tracker = ProgressTracker()
        status_msg = await ctx.send(tracker.format_bar())

        # Start progress update loop
        done_event = asyncio.Event()
        progress_task = asyncio.create_task(
            self._progress_loop(status_msg, tracker, done_event)
        )

        try:
            async with self._semaphore:
                self._set_cooldown(ctx.author.id)

                # max_filesize for yt-dlp: This limits individual stream sizes
                # (not merged output). We need to allow large downloads since we
                # have compression and AnonDrop as fallbacks. Use a generous cap
                # for disk safety, but don't tie it to Discord's upload limit
                # which is too restrictive (10MB for unboosted guilds).
                if hd_mode:
                    # HD mode: no practical filesize limit, we want max quality
                    # Cap at 1GB for disk safety only
                    max_filesize = 1024 * 1024 * 1024
                elif guild_config["anondrop_enabled"]:
                    # AnonDrop can handle large files; cap at 500MB for safety
                    max_filesize = 500 * 1024 * 1024
                else:
                    # Without AnonDrop, we can still compress, so allow up to
                    # 200MB per stream (ffmpeg can compress significantly)
                    max_filesize = 200 * 1024 * 1024
                max_duration = guild_config["max_duration"]

                files, info_dict = await self._try_download(
                    url=url,
                    temp_dir=temp_dir,
                    tracker=tracker,
                    cookies_file=cookies_file,
                    max_filesize=max_filesize,
                    max_duration=max_duration,
                    audio_only=audio_only,
                    hd_mode=hd_mode,
                    format_id=format_id,
                )

                if not files:
                    done_event.set()
                    await progress_task
                    await status_msg.edit(content="No media found at that URL.")
                    return

                # Handle upload (compression/anondrop as needed)
                # NOTE: We do NOT set done_event here — the progress loop
                # keeps running through compression, re-encoding, and upload
                # phases so the user sees live progress for those stages too.
                await self._handle_file_upload(
                    ctx=ctx,
                    files=files,
                    status_msg=status_msg,
                    tracker=tracker,
                    url=url,
                    platform=platform,
                    guild_config=guild_config,
                    hd_mode=hd_mode,
                )
                done_event.set()
                await progress_task

        except ValueError as e:
            # Duration exceeded
            done_event.set()
            await progress_task
            try:
                await status_msg.edit(content=str(e))
            except (discord.NotFound, discord.HTTPException):
                pass

        except Exception as e:
            done_event.set()
            await progress_task
            error_msg = "Failed to download media from that URL."
            log.error("Download failed for %s: %s", url, e, exc_info=True)
            try:
                await status_msg.edit(content=error_msg)
            except (discord.NotFound, discord.HTTPException):
                pass

        finally:
            done_event.set()
            if not progress_task.done():
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass
            log.debug("[_do_download] Cleaning up temp_dir: %s", temp_dir)
            _cleanup_temp_dir(temp_dir)


async def setup(bot: Red):
    cog = SabDownloader(bot)
    try:
        await modlog.register_casetype(
            name="mediadownload",
            default_setting=True,
            image="\N{DOWN-POINTING RED TRIANGLE}",
            case_str="Media Download",
        )
    except RuntimeError:
        pass  # Already registered
    await bot.add_cog(cog)
