import discord
from discord.ext import commands
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import bold, box
import os
import asyncio
import requests
import subprocess
import sys
import traceback
import time

class YTD(commands.Cog):
    """YouTube Downloader Cog"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_global = {
            "cooldown": 0,
            "last_used": 0,
            "log_channel": None,
        }
        default_user = {
            "last_used": 0,
        }
        self.config.register_global(**default_global)
        self.config.register_user(**default_user)

        try:
            import yt_dlp
        except ImportError:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yt-dlp'])

    @commands.command()
    async def ytd(self, ctx: commands.Context, link: str = None, format_choice: str = "mp4", quality: str = "best", *, flags: str = ""):
        """
        Download YouTube videos. Example:
        -ytd <link> mp4 720p --discord
        """
        # Permission: owner or mod/admin
        if not (await self.bot.is_owner(ctx.author) or await self.bot.is_mod(ctx.author)):
            return await ctx.send("You don't have permission to use this command.")

        owner = await self.bot.is_owner(ctx.author)
        now = time.time()
        cooldown = await self.config.cooldown()
        last_used = await self.config.last_used()
        remaining = cooldown - (now - last_used)

        if not owner and remaining > 0:
            return await ctx.send(f"⏳ Cooldown active. Try again in {int(remaining)}s.")

        if not link:
            return await ctx.send("Usage: `-ytd <link> [mp3/mp4] [quality] [--discord]`")

        if format_choice not in ["mp3", "mp4"]:
            return await ctx.send("❌ Invalid format. Choose `mp3` or `mp4`.")

        msg = await ctx.send(f"📥 Downloading `{format_choice}` at `{quality}` quality...")

        file_path, error_msg = await self.download_youtube(link, format_choice, quality, msg)
        if not file_path:
            await msg.delete()
            return await ctx.send(f"❌ Download failed.\n{box(error_msg or 'Unknown error')}")

        upload_link = None
        try:
            if "--discord" in flags:
                await ctx.send(file=discord.File(file_path))
                upload_link = "(uploaded to Discord)"
            else:
                upload_link = await self.upload_to_pixeldrain(file_path)
                await ctx.send(f"✅ Uploaded: {upload_link}")
        except Exception as e:
            await ctx.send(f"⚠️ Upload error: {e}")

        if upload_link:
            log_channel_id = await self.config.log_channel()
            if log_channel_id:
                log_channel = self.bot.get_channel(log_channel_id)
                if log_channel:
                    await log_channel.send(f"📤 `{ctx.author}` downloaded `{format_choice}` | {link}\n🔗 {upload_link}")

        if not owner:
            await self.config.last_used.set(now)
        await msg.delete()
        if os.path.exists(file_path):
            os.remove(file_path)

    async def download_youtube(self, link, format_choice, quality, msg):
        import yt_dlp
        outtmpl = f"ytdl_%(title)s.%(ext)s"
        ydl_opts = {
            'format': f'bestaudio/best' if format_choice == 'mp3' else f'bestvideo[height<={quality}]+bestaudio/best',
            'outtmpl': outtmpl,
            'noplaylist': True,
            'quiet': True,
            'progress_hooks': [self.make_progress_hook(msg)],
        }
        if format_choice == 'mp3':
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]

        try:
            loop = asyncio.get_event_loop()
            def run():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(link, download=True)
                    return ydl.prepare_filename(info).rsplit('.', 1)[0] + ('.mp3' if format_choice == 'mp3' else '.mp4')
            return await loop.run_in_executor(None, run), None
        except Exception as e:
            return None, traceback.format_exc()

    def make_progress_hook(self, msg):
        async def hook(d):
            if d['status'] == 'downloading':
                percent = d.get('_percent_str', '').strip()
                if percent:
                    try:
                        await msg.edit(content=f"📥 Downloading... {percent}")
                    except:
                        pass
        return hook

    async def upload_to_pixeldrain(self, file_path):
        with open(file_path, 'rb') as f:
            res = requests.post("https://pixeldrain.com/api/file", files={'file': f})
        if res.status_code != 200:
            raise Exception(f"Upload failed with HTTP {res.status_code}")
        try:
            data = res.json()
            file_id = data.get("id") or data.get("file", {}).get("id")
            return f"https://pixeldrain.com/api/file/{file_id}?download"
        except Exception as e:
            raise Exception(f"Failed to parse upload response: {e}")

    @commands.command()
    @commands.is_owner()
    async def ytdcooldown(self, ctx: commands.Context, seconds: int):
        await self.config.cooldown.set(seconds)
        await ctx.send(f"✅ Set global cooldown to {seconds} seconds.")

    @commands.command()
    @commands.is_owner()
    async def ytdlogchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.config.log_channel.set(channel.id)
        await ctx.send(f"✅ Log channel set to {channel.mention}.")
