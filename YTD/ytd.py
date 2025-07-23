import os
import discord
import asyncio
import traceback
from redbot.core import commands

class YTD(commands.Cog):
    """
    YouTube Downloader Cog

    Usage:
    -ytd <link> [mp3|mp4] [quality]
    Example: -ytd https://youtube.com/watch?v=xxxx mp3 best
    """

    def __init__(self, bot):
        self.bot = bot
        try:
            import yt_dlp
        except ImportError:
            import subprocess, sys
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yt-dlp'])
                print("yt-dlp installed.")
            except Exception as e:
                print("yt-dlp installation failed:", e)

    @commands.command(name="ytd", help="Download YouTube video/audio: -ytd <link> [mp3|mp4] [quality]")
    async def ytd(self, ctx, link: str = None, format_choice: str = "mp3", quality: str = "best"):
        if not link:
            await ctx.send("❌ Please provide a YouTube link.\nUsage: `-ytd <link> [mp3|mp4] [quality]`")
            return

        if format_choice not in ["mp3", "mp4"]:
            await ctx.send("❌ Invalid format. Choose `mp3` or `mp4`.")
            return

        await ctx.send(f"⏬ Downloading...\n**Format:** {format_choice}\n**Quality:** {quality}")
        file_path, debug_log = await self.download_youtube(link, format_choice, quality)

        if file_path:
            try:
                await ctx.send(file=discord.File(file_path))
            except Exception as e:
                await ctx.send(f"⚠️ File too large or error occurred: `{e}`")
            finally:
                try:
                    os.remove(file_path)
                except:
                    pass
        else:
            await self.send_debug(ctx, debug_log)

    async def download_youtube(self, link, format_choice, quality):
        import yt_dlp

        output_template = "ytdl_%(title)s.%(ext)s"
        log = []

        class Logger:
            def debug(self, msg): log.append(f"[DEBUG] {msg}")
            def warning(self, msg): log.append(f"[WARNING] {msg}")
            def error(self, msg): log.append(f"[ERROR] {msg}")

        ydl_opts = {
            'format': 'bestaudio/best' if format_choice == 'mp3' else f'bestvideo[height<={quality}]+bestaudio/best',
            'outtmpl': output_template,
            'noplaylist': True,
            'quiet': True,
            'logger': Logger(),
        }

        if format_choice == 'mp3':
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]

        def run():
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(link, download=True)
                    filename = ydl.prepare_filename(info)
                    if format_choice == 'mp3':
                        filename = filename.rsplit('.', 1)[0] + '.mp3'
                    return filename if os.path.exists(filename) else None, "\n".join(log)
            except Exception:
                log.append(traceback.format_exc())
                return None, "\n".join(log)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, run)

    async def send_debug(self, ctx, log_text):
        if not log_text:
            await ctx.send("❌ Download failed. No debug output.")
            return

        chunks = [log_text[i:i+1900] for i in range(0, len(log_text), 1900)]
        await ctx.send("❌ Download failed. Sending debug log:")
        for chunk in chunks:
            await ctx.send(f"```py\n{chunk}\n```")
