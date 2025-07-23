import discord
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import box
import asyncio
import os
import tempfile
import shutil
import traceback
import aiohttp

class YTD(commands.Cog):
    """YouTube Downloader Cog"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_global = {
            "log_channel": None,
            "cooldown_seconds": 0,
            "last_run": 0,
        }
        self.config.register_global(**default_global)

        # Try install yt_dlp if missing
        try:
            import yt_dlp  # noqa
        except ImportError:
            import subprocess, sys
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "yt-dlp"])
                print("yt-dlp installed automatically.")
            except Exception:
                print("yt-dlp installation failed. Please install manually with 'pip install yt-dlp'.")

    def cog_unload(self):
        # cleanup if needed
        pass

    @commands.group()
    async def ytd(self, ctx):
        """YouTube downloader commands group."""
        if ctx.invoked_subcommand is None:
            # fallback: download with args
            await self.ytd_download(ctx, None, "mp3", "best")

    @ytd.command(name="cooldown")
    @commands.is_owner()
    async def ytd_cooldown(self, ctx, cooldown: str):
        """Set cooldown for ytd command. Example: 5m, 30s, 1h."""
        seconds = self.parse_time_to_seconds(cooldown)
        if seconds is None:
            await ctx.send("Invalid cooldown format! Use formats like '5m', '30s', '1h'.")
            return
        await self.config.cooldown_seconds.set(seconds)
        await ctx.send(f"Cooldown set to {cooldown} ({seconds} seconds).")

    def parse_time_to_seconds(self, time_str):
        try:
            units = {"s": 1, "m": 60, "h": 3600}
            unit = time_str[-1]
            if unit not in units:
                return None
            num = int(time_str[:-1])
            return num * units[unit]
        except Exception:
            return None

    @ytd.command(name="log")
    @commands.is_owner()
    async def ytd_log(self, ctx, channel: discord.TextChannel = None):
        """Set or clear the global log channel for YTD usage logs."""
        if channel is None:
            await self.config.log_channel.set(None)
            await ctx.send("YTD log channel cleared.")
        else:
            await self.config.log_channel.set(channel.id)
            await ctx.send(f"YTD log channel set to {channel.mention}.")

    @commands.cooldown(1, 1, commands.BucketType.user)  # per user basic cooldown
    @commands.has_permissions(administrator=True)  # or replace with your permission logic
    @ytd.command(name="download")
    async def ytd_download(self, ctx, link: str = None, format_choice: str = "mp3", quality: str = "best", *, flags: str = ""):
        """
        Download YouTube video or audio.
        Usage:
        -ytd download <link> [mp3|mp4] [quality] [flags]

        Flags:
        --discord : upload file directly to Discord instead of PixelDrain upload.
        """
        # Owner bypasses global cooldown
        cd_seconds = await self.config.cooldown_seconds()
        if ctx.author.id != self.bot.owner_id and cd_seconds > 0:
            import time
            last = await self.config.last_run()
            now = time.time()
            diff = now - last
            if diff < cd_seconds:
                await ctx.send(f"Cooldown active. Try again in {int(cd_seconds - diff)} seconds.")
                return
            await self.config.last_run.set(now)

        if not link:
            await ctx.send("You must provide a YouTube link.")
            return
        format_choice = format_choice.lower()
        if format_choice not in ("mp3", "mp4"):
            await ctx.send("Invalid format choice! Must be mp3 or mp4.")
            return

        flag_direct = "--discord" in flags.lower()

        progress_msg = await ctx.send("Starting download... 0%")

        file_path, error = await self.download_youtube(link, format_choice, quality, progress_msg)
        if not file_path:
            await progress_msg.edit(content=f"❌ Download failed:\n{box(error or 'Unknown error')}")
            return

        try:
            if flag_direct:
                # Upload file directly to discord
                if os.path.getsize(file_path) > 8 * 1024 * 1024:  # 8MB limit fallback
                    await progress_msg.edit(content="❌ File too large to upload directly to Discord. Try without --discord flag.")
                    return
                await progress_msg.delete()
                await ctx.send(file=discord.File(file_path))
                link_url = None
            else:
                # Upload to PixelDrain
                link_url = await self.upload_to_pixeldrain(file_path)
                await progress_msg.delete()
                if link_url:
                    await ctx.send(f"✅ Uploaded to PixelDrain: {link_url}")
                else:
                    await ctx.send("❌ Upload to PixelDrain failed.")
        except Exception as e:
            await progress_msg.edit(content=f"❌ Upload error: {e}")
            return
        finally:
            try:
                os.remove(file_path)
            except Exception:
                pass

        # Log usage if log channel set
        log_channel_id = await self.config.log_channel()
        if log_channel_id:
            log_channel = self.bot.get_channel(log_channel_id)
            if log_channel:
                user = ctx.author
                msg = f"User {user} ({user.id}) downloaded: {link}\nFormat: {format_choice}, Quality: {quality}"
                if link_url:
                    msg += f"\nPixelDrain link: {link_url}"
                else:
                    msg += f"\nUploaded directly to Discord."
                try:
                    await log_channel.send(msg)
                except Exception:
                    pass

    async def download_youtube(self, link, format_choice, quality, progress_msg):
        import yt_dlp
        loop = asyncio.get_event_loop()

        def progress_hook(d):
            if d['status'] == 'downloading':
                total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded_bytes = d.get('downloaded_bytes', 0)
                if total_bytes > 0:
                    percent = int(downloaded_bytes / total_bytes * 100)
                    asyncio.run_coroutine_threadsafe(
                        progress_msg.edit(content=f"Downloading... {percent}%"), self.bot.loop
                    )
            elif d['status'] == 'finished':
                asyncio.run_coroutine_threadsafe(
                    progress_msg.edit(content="Download finished, processing..."), self.bot.loop
                )

        def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                outtmpl = os.path.join(tmpdir, "ytdl_%(title)s.%(ext)s")
                ydl_opts = {
                    'format': f'bestaudio/best' if format_choice == 'mp3' else f'bestvideo[height<={quality}]+bestaudio/best',
                    'outtmpl': outtmpl,
                    'noplaylist': True,
                    'quiet': True,
                    'progress_hooks': [progress_hook],
                }
                if format_choice == 'mp3':
                    ydl_opts['postprocessors'] = [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }]
                import yt_dlp
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(link, download=True)
                    filename = ydl.prepare_filename(info)
                    if format_choice == 'mp3':
                        filename = filename.rsplit('.', 1)[0] + '.mp3'
                    # Copy file out of temp before context ends
                    dest = os.path.join(os.getcwd(), os.path.basename(filename))
                    shutil.copyfile(filename, dest)
                    return dest

        try:
            filepath = await loop.run_in_executor(None, run)
            return filepath, None
        except Exception:
            return None, traceback.format_exc()

    async def upload_to_pixeldrain(self, filepath):
        url = "https://pixeldrain.com/api/file"
        try:
            async with aiohttp.ClientSession() as session:
                with open(filepath, "rb") as f:
                    data = f.read()
                async with session.post(url, data=data) as resp:
                    if resp.status != 200:
                        return None
                    j = await resp.json()
                    file_id = j.get("id")
                    if not file_id:
                        return None
                    return f"https://pixeldrain.com/u/{file_id}"
        except Exception:
            return None
