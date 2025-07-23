import discord
from redbot.core import commands

class YTD(commands.Cog):
    """YouTube Downloader Cog"""
    def __init__(self, bot):
        self.bot = bot
        # Check and install yt-dlp if missing
        try:
            import yt_dlp
        except ImportError:
            import subprocess, sys
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yt-dlp'])
                bot.loop.create_task(self._notify_install_success())
            except Exception:
                bot.loop.create_task(self._notify_install_fail())

    async def _notify_install_success(self):
        # Optionally notify bot owner or log channel
        print("yt-dlp was installed automatically.")

    async def _notify_install_fail(self):
        print("yt-dlp installation failed. Please install manually with 'pip install yt-dlp'.")

    @commands.command()
    async def ytd(self, ctx):
        """Download YouTube video or audio."""
        await ctx.send("Please provide the YouTube link.")
        def check_link(m):
            return m.author == ctx.author and m.channel == ctx.channel
        try:
            link_msg = await ctx.bot.wait_for('message', check=check_link, timeout=60)
            link = link_msg.content.strip()
        except Exception:
            await ctx.send("Timed out waiting for link.")
            return

        await ctx.send("What format do you want? (mp3 for audio, mp4 for video)")
        def check_format(m):
            return m.author == ctx.author and m.channel == ctx.channel
        try:
            format_msg = await ctx.bot.wait_for('message', check=check_format, timeout=30)
            format_choice = format_msg.content.lower().strip()
            if format_choice not in ["mp3", "mp4"]:
                await ctx.send("Invalid format. Please choose mp3 or mp4.")
                return
        except Exception:
            await ctx.send("Timed out waiting for format.")
            return

        await ctx.send("What quality do you want? (e.g. 720p, 1080p, best)")
        def check_quality(m):
            return m.author == ctx.author and m.channel == ctx.channel
        try:
            quality_msg = await ctx.bot.wait_for('message', check=check_quality, timeout=30)
            quality = quality_msg.content.strip()
        except Exception:
            await ctx.send("Timed out waiting for quality.")
            return

        await ctx.send(f"Downloading... Format: {format_choice}, Quality: {quality}")
        file_path = await self.download_youtube(link, format_choice, quality)
        if file_path:
            try:
                await ctx.send(file=discord.File(file_path))
            except Exception:
                await ctx.send("File too large to send or error occurred.")
        else:
            await ctx.send("Download failed.")

    async def download_youtube(self, link, format_choice, quality):
        import yt_dlp
        import asyncio
        import os
        outtmpl = f"ytdl_%(title)s.%(ext)s"
        ydl_opts = {
            'format': f'bestaudio/best' if format_choice == 'mp3' else f'bestvideo[height<={quality}]+bestaudio/best',
            'outtmpl': outtmpl,
            'noplaylist': True,
            'quiet': True,
        }
        if format_choice == 'mp3':
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        try:
            loop = asyncio.get_event_loop()
            def run_dl():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(link, download=True)
                    if format_choice == 'mp3':
                        filename = ydl.prepare_filename(info).rsplit('.', 1)[0] + '.mp3'
                    else:
                        filename = ydl.prepare_filename(info)
                    return filename if os.path.exists(filename) else None
            file_path = await loop.run_in_executor(None, run_dl)
            return file_path
        except Exception as e:
            print(f"Error downloading: {e}")
            return None
