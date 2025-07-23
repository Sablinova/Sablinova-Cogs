import os
import discord
import asyncio
import traceback
import tempfile
from redbot.core import commands, app_commands

class YTD(commands.Cog):
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
    async def ytd(self, ctx: commands.Context):
        """Open a form to download YouTube video/audio."""
        await ctx.interaction.response.send_modal(YTDModal(self))

class YTDModal(discord.ui.Modal, title="YouTube Downloader"):
    link = discord.ui.TextInput(label="YouTube Link", style=discord.TextStyle.short, required=True)
    format_choice = discord.ui.TextInput(label="Format (mp3 or mp4)", default="mp3", required=True)
    quality = discord.ui.TextInput(label="Quality (720, 1080, best...)", default="best", required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        link = self.link.value.strip()
        fmt = self.format_choice.value.strip().lower()
        qual = self.quality.value.strip()

        if fmt not in ["mp3", "mp4"]:
            await interaction.followup.send("❌ Invalid format. Use `mp3` or `mp4`.")
            return

        await interaction.followup.send(f"⏬ Downloading `{fmt}` at `{qual}` quality...", ephemeral=True)
        file_path, debug_log = await self.cog.download_youtube(link, fmt, qual)

        if file_path:
            try:
                await interaction.followup.send(file=discord.File(file_path))
            except Exception as e:
                await interaction.followup.send(f"⚠️ File too large or error occurred: `{e}`")
            finally:
                try:
                    os.remove(file_path)
                except:
                    pass
        else:
            print("[YTD DEBUG] Error log:\n", debug_log)
            await interaction.followup.send("❌ Download failed. Check bot logs for details.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        print("[YTD ERROR] Modal error:", traceback.format_exc())
        await interaction.followup.send("Something went wrong. Check the logs.", ephemeral=True)

    async def on_timeout(self) -> None:
        print("[YTD Modal] Timed out")

# Include same download_youtube() as before
