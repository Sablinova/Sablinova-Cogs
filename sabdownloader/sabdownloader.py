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
from redbot.core import commands, Config, modlog
from redbot.core.bot import Red

log = logging.getLogger("red.sablinova.sabdownloader")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROGRESS_FILLED = "\u2588"  # █
PROGRESS_EMPTY = "\u2591"  # ░
PROGRESS_BAR_LENGTH = 20
PROGRESS_UPDATE_INTERVAL = 3.0  # seconds between progress message edits

ANONDROP_CHUNK_SIZE = 9 * 1024 * 1024  # 9 MB

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
    """Collect non-temp, non-empty files from a directory."""
    results = []
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
        results.append(fp)
    return results


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
        log.debug("gallery-dl failed for %s: %s", url, e)

    # Collect only NEW, non-temp, non-empty files
    downloaded = []
    for f in os.listdir(temp_dir):
        fp = os.path.join(temp_dir, f)
        if fp in pre_existing:
            continue
        if not os.path.isfile(fp):
            continue
        if _is_temp_file(fp):
            log.debug("gallery-dl: skipping temp file: %s", f)
            continue
        if os.path.getsize(fp) == 0:
            log.debug("gallery-dl: skipping zero-byte file: %s", f)
            continue
        downloaded.append(fp)

    # Filter by max filesize if set
    if max_filesize and downloaded:
        downloaded = [f for f in downloaded if os.path.getsize(f) <= max_filesize]

    return downloaded


# ---------------------------------------------------------------------------
# yt-dlp backend
# ---------------------------------------------------------------------------


def _ytdlp_download(
    url: str,
    temp_dir: str,
    progress_tracker: Optional[ProgressTracker] = None,
    cookies_file: Optional[str] = None,
    max_filesize: Optional[int] = None,
    max_duration: Optional[int] = None,
    audio_only: bool = False,
) -> Tuple[List[str], Optional[dict]]:
    """Run yt-dlp synchronously. Returns (list of file paths, info_dict or None).

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
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "merge_output_format": "mp4",
        # Prevent .part files - write directly to final filename
        "nopart": True,
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

    if audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        # Prefer mp4 containers, fall back through progressively simpler formats.
        # The broad fallback chain avoids 403 issues where specific format
        # combinations are blocked by the server.
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
        if "duration" in err_str or "match_filter" in err_str:
            raise ValueError("Video exceeds maximum allowed duration.") from e
        raise
    except Exception as e:
        log.debug("yt-dlp failed for %s: %s", url, e)
        raise

    # Collect output files - filter out temp/partial files and empty files
    downloaded = _collect_real_files(temp_dir)

    return downloaded, info_dict


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
            html = await resp.text()
            # Look for localStorage.setItem('userkey', '<number>')
            match = re.search(
                r"localStorage\.setItem\(['\"]userkey['\"],\s*['\"](\d+)['\"]", html
            )
            if match:
                return match.group(1)
    except Exception as e:
        log.warning("AnonDrop register error: %s", e)
    return None


def _parse_anondrop_link(html: str) -> Optional[str]:
    """Extract the AnonDrop download link from response HTML."""
    # Look for anondrop.net/<id> pattern
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
        ):
            return link
    # Also try just the path pattern
    match = re.search(r"anondrop\.net/([a-zA-Z0-9]{6,})", html)
    if match:
        return f"https://anondrop.net/{match.group(1)}"
    return None


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
    ) -> Tuple[List[str], Optional[dict]]:
        """Try downloading with gallery-dl first, then yt-dlp (or vice versa depending on domain).

        Returns (list of file paths, info_dict or None).
        """
        domain = _get_domain(url)
        info_dict = None

        # Decide order based on domain
        if domain in _YTDLP_PRIORITY:
            backends = ["ytdlp", "gallerydl"]
        elif domain in _GALLERY_DL_PRIORITY:
            backends = ["gallerydl", "ytdlp"]
        else:
            # Default: gallery-dl first for image sites, yt-dlp for everything else
            backends = ["gallerydl", "ytdlp"]

        # Audio extraction only makes sense with yt-dlp
        if audio_only:
            backends = ["ytdlp"]

        last_error = None

        for backend in backends:
            if backend == "gallerydl":
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
                    log.debug("gallery-dl failed for %s: %s", url, e)

                # Clean up any leftover files before trying next backend
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
                        ),
                    )
                    if files:
                        return files, info_dict
                except ValueError:
                    # Duration exceeded - re-raise as-is
                    raise
                except Exception as e:
                    last_error = e
                    log.debug("yt-dlp failed for %s: %s", url, e)

                # Clean up any leftover files before trying next backend
                _purge_temp_files(temp_dir)

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
    ) -> None:
        """Handle uploading files to Discord (with compression/anondrop fallback)."""
        filesize_limit = ctx.guild.filesize_limit
        anondrop_enabled = guild_config["anondrop_enabled"]
        anondrop_userkey = await self.config.anondrop_userkey()

        uploaded_files = []
        anondrop_links = []
        total_original_size = 0
        total_compressed_size = 0
        compression_used = False
        anondrop_used = False

        for filepath in files:
            file_size = os.path.getsize(filepath)
            total_original_size += file_size

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

            # Still too large or not a video - try AnonDrop
            if anondrop_enabled:
                link = await _anondrop_upload(
                    file_path=filepath,
                    progress_tracker=tracker,
                    userkey=anondrop_userkey,
                )
                if link:
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

        # Upload to Discord
        if uploaded_files:
            # Discord allows up to 10 attachments per message
            for i in range(0, len(uploaded_files), 10):
                batch = uploaded_files[i : i + 10]
                discord_files = []
                for fp in batch:
                    try:
                        discord_files.append(
                            discord.File(fp, filename=os.path.basename(fp))
                        )
                    except Exception as e:
                        log.warning("Failed to create discord.File for %s: %s", fp, e)

                if discord_files:
                    try:
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
                                    anondrop_links.append(link)
                                    anondrop_used = True

        # Post AnonDrop links
        if anondrop_links:
            links_text = "\n".join(anondrop_links)
            await ctx.send(f"File too large for Discord. Download here:\n{links_text}")

        # Delete command message if configured
        if guild_config["delete_command"]:
            try:
                await ctx.message.delete()
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                pass

        # Delete status message
        try:
            await status_msg.delete()
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass

        # Modlog entry
        try:
            await modlog.create_case(
                bot=self.bot,
                guild=ctx.guild,
                created_at=ctx.message.created_at,
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
                    timestamp=ctx.message.created_at,
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
                    value=f"{ctx.channel.mention}",
                    inline=True,
                )
                log_embed.add_field(
                    name="Server",
                    value=f"{ctx.guild.name} (`{ctx.guild.id}`)",
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
                log_embed.set_footer(
                    text=ctx.guild.name,
                    icon_url=ctx.guild.icon.url if ctx.guild.icon else None,
                )
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
        """Toggle whether the user's command message is deleted after download."""
        current = await self.config.guild(ctx.guild).delete_command()
        new_val = not current
        await self.config.guild(ctx.guild).delete_command.set(new_val)
        state = "enabled" if new_val else "disabled"
        await ctx.send(f"Delete command message is now **{state}**.")

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

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    async def dl(self, ctx: commands.Context, url: str):
        """Download media from a URL and upload to Discord."""
        await self._do_download(ctx, url, audio_only=False)

    @dl.command(name="audio")
    async def dl_audio(self, ctx: commands.Context, url: str):
        """Extract audio from a URL as MP3."""
        await self._do_download(ctx, url, audio_only=True)

    async def _do_download(
        self, ctx: commands.Context, url: str, audio_only: bool = False
    ) -> None:
        """Core download flow."""
        # --- Validation ---
        guild_config = await self.config.guild(ctx.guild).all()

        if not guild_config["enabled"]:
            await ctx.send("SabDownloader is disabled in this server.", delete_after=10)
            return

        # Channel restriction
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

                max_filesize = ctx.guild.filesize_limit * 3
                max_duration = guild_config["max_duration"]

                files, info_dict = await self._try_download(
                    url=url,
                    temp_dir=temp_dir,
                    tracker=tracker,
                    cookies_file=cookies_file,
                    max_filesize=max_filesize,
                    max_duration=max_duration,
                    audio_only=audio_only,
                )

                if not files:
                    done_event.set()
                    await progress_task
                    await status_msg.edit(content="No media found at that URL.")
                    return

                # Handle upload (compression/anondrop as needed)
                done_event.set()
                await progress_task
                await self._handle_file_upload(
                    ctx=ctx,
                    files=files,
                    status_msg=status_msg,
                    tracker=tracker,
                    url=url,
                    platform=platform,
                    guild_config=guild_config,
                )

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
