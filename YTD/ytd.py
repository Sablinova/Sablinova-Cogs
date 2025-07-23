import discord
from redbot.core import commands

class YTD(commands.Cog):
    """
    YouTube Downloader Cog

    Commands:
    -ytd <link>: Starts an interactive YouTube download session. You will be prompted to select format and quality via buttons.
    """
    def __init__(self, bot):
        self.bot = bot
        # Check and install yt-dlp if missing
        try:
            import yt_dlp
        except ImportError:
            import subprocess, sys
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yt-dlp'])
                print("yt-dlp was installed automatically.")
            except Exception:
                print("yt-dlp installation failed. Please install manually with 'pip install yt-dlp'.")

    # Removed async notification methods; using print statements instead

    @commands.command(help="Download YouTube video or audio. Usage: -ytd <link> [mp3|mp4] [quality]")
    async def ytd(self, ctx, link: str = None, format_choice: str = "mp3", quality: str = "best"):
        """
        Download YouTube video or audio.

        Usage:
        -ytd <link> [mp3|mp4] [quality]
        Example: -ytd https://youtube.com/watch?v=xxxx mp3 best
        """
        if not link:
            await ctx.send("Please provide a YouTube link. Usage: `-ytd <link> [mp3|mp4] [quality]`")
            return
        if format_choice not in ["mp3", "mp4"]:
            await ctx.send("Invalid format. Please choose mp3 or mp4.")
            return
        await ctx.send(f"Downloading... Format: {format_choice}, Quality: {quality}")
        file_path, error_msg = await self.download_youtube_debug(link, format_choice, quality)
        if file_path:
            try:
                await ctx.send(file=discord.File(file_path))
            except Exception as e:
                await ctx.send(f"File too large to send or error occurred: {e}")
        else:
            # Send error message in code blocks, chunked to 1990 chars
            if error_msg:
                chunks = [error_msg[i:i+1990] for i in range(0, len(error_msg), 1990)]
                for idx, chunk in enumerate(chunks):
                    prefix = "Download failed. Error:\n" if idx == 0 else "(cont.)\n"
                    await ctx.send(f"{prefix}```\n{chunk}\n```")
            else:
                await ctx.send("Download failed. Error: Unknown error")

    # Removed ytdhelp command and all button UI



    async def download_youtube_debug(self, link, format_choice, quality):
        import yt_dlp
        import asyncio
        import os
        import traceback
        outtmpl = f"ytdl_%(title)s.%(ext)s"
        ydl_opts = {
            'format': f'bestaudio/best' if format_choice == 'mp3' else f'bestvideo[height<={quality}]+bestaudio/best',
            'outtmpl': outtmpl,
            'noplaylist': True,
            'quiet': False,  # Make yt-dlp verbose
            'logger': YTDLogger(),
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
                try:
                    print(f"[YTD DEBUG] Downloading: link={link}, format={format_choice}, quality={quality}")
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(link, download=True)
                        print(f"[YTD DEBUG] yt-dlp info: {info}")
                        if format_choice == 'mp3':
                            filename = ydl.prepare_filename(info).rsplit('.', 1)[0] + '.mp3'
                        else:
                            filename = ydl.prepare_filename(info)
                        print(f"[YTD DEBUG] Downloaded file: {filename}")
                        return filename if os.path.exists(filename) else None, None
                except Exception as e:
                    tb = traceback.format_exc()
                    print(f"[YTD ERROR] Exception: {e}\nTraceback:\n{tb}")
                    return None, f"{e}\nTraceback:\n{tb}"
            file_path, error_msg = await loop.run_in_executor(None, run_dl)
            return file_path, error_msg
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[YTD ERROR] Outer Exception: {e}\nTraceback:\n{tb}")
            return None, f"{e}\nTraceback:\n{tb}"

class YTDLogger:
    def debug(self, msg):
        print(f"[yt-dlp DEBUG] {msg}")
    def warning(self, msg):
        print(f"[yt-dlp WARNING] {msg}")
    def error(self, msg):
        print(f"[yt-dlp ERROR] {msg}")
