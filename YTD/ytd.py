import os
import discord
import asyncio
import traceback
import tempfile
from redbot.core import commands

class YTD(commands.Cog):
    """YouTube Downloader Cog"""

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

    @commands.hybrid_command(name="ytd", with_app_command=True)
    async def ytd(self, ctx: commands.Context, link: str = None, format_choice: str = "mp3", quality: str = "best"):
        """
        Download YouTube video or audio.

        `/ytd` will open a modal form.
        `-ytd <link> [format] [quality]` will download directly.
        """
        # Slash command → open modal form
        if ctx.interaction is not None and link is None:
            await ctx.interaction.response.send_modal(YTDModal(self))
            return

        # Text fallback command: `-ytd <link> mp3 720`
        if not link:
            await ctx.send("❌ Please provide a YouTube link.\nUsage: `-ytd <link> [mp3|mp4] [quality]`")
            return

        if format_choice not in ["mp3", "mp4"]:
            await ctx.send("❌ Invalid format. Use `mp3` or `mp4`.")
            return

        await ctx.send(f"⏬ Downloading `{format_choice}` at `{quality}` quality...")
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
            print("[YTD DEBUG] Error log:\n", debug_log)
            await ctx.send("❌ Download failed. Check bot console logs.", ephemeral=False)

    async def download_youtube(self, link, format_choice, quality):
        import yt_dlp

        tmp_dir = tempfile.gettempdir()
        output_template = os.path.join(tmp_dir, "ytdl_%(title)s.%(ext)s")
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
                    abs_path = os.path.abspath(filename)
                    return abs_path if os.path.exists(abs_path) else None, "\n".join(log)
            except Exception:
                log.append(traceback.format_exc())
                return None, "\n".join(log)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, run)


class YTDModal(discord.ui.Modal, title="YouTube Downloader"):
    link = discord.ui.TextInput(label="YouTube Link", style=discord.TextStyle.short, required=True)
    format_choice = discord.ui.TextInput(label="Format (mp3 or mp4)", default="mp3", required=True)
    quality = discord.ui.TextInput(label="Quality (e.g. 720, 1080, best)", default="best", required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        link = self.link.value.strip()
        fmt = self.format_choice.value.strip().lower()
        qual = self.quality.value.strip()

        if fmt not in ["mp3", "mp4"]:
            await interaction.followup.send("❌ Invalid format. Use `mp3` or `mp4`.", ephemeral=True)
            return

        await interaction.followup.send(f"⏬ Downloading `{fmt}` at `{qual}` quality...", ephemeral=True)
        file_path, debug_log = await self.cog.download_youtube(link, fmt, qual)

        if file_path:
            try:
                await interaction.followup.send(file=discord.File(file_path))
            except Exception as e:
                await interaction.followup.send(f"⚠️ File too large or error occurred: `{e}`", ephemeral=True)
            finally:
                try:
                    os.remove(file_path)
                except:
                    pass
        else:
            print("[YTD DEBUG] Error log:\n", debug_log)
            await interaction.followup.send("❌ Download failed. Check bot logs for debug info.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        print("[YTD ERROR] Modal error:", traceback.format_exc())
        await interaction.followup.send("Something went wrong. Check the logs.", ephemeral=True)
