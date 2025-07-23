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

    @commands.command(help="Download YouTube video or audio. Usage: -ytd <link>. You'll be prompted to select format and quality via buttons.")
    async def ytd(self, ctx, link: str = None):
        """
        Download YouTube video or audio.

        Usage:
        -ytd <link>
        After running, you'll get an embed with buttons to select format (mp3/mp4) and quality (720p/1080p/best).
        """
        if not link:
            await ctx.send("Please provide a YouTube link. Usage: `-ytd <link>`")
            return

        embed = discord.Embed(title="YouTube Downloader", description=f"Link: {link}", color=discord.Color.red())
        embed.add_field(name="Format", value="Choose mp3 for audio or mp4 for video.", inline=False)
        embed.add_field(name="Quality", value="Choose quality: 720p, 1080p, best, etc.", inline=False)
        embed.add_field(name="Help", value="Use `-ytd <link>` to start. Then select format and quality using the buttons below.", inline=False)
        embed.set_footer(text="Click a button below to select format and quality.")

        view = YTDView(link, self)
        await ctx.send(embed=embed, view=view)

    @commands.command(name="ytdhelp", help="Show help for the YouTube Downloader cog.")
    async def ytdhelp(self, ctx):
        """
        Show help for the YouTube Downloader cog.
        """
        embed = discord.Embed(title="YouTube Downloader Help", color=discord.Color.green())
        embed.add_field(name="Usage", value="`-ytd <link>`\nStarts an interactive download session.", inline=False)
        embed.add_field(name="How it works", value="After you provide a link, you'll get an embed with buttons to select format (mp3/mp4) and quality (720p/1080p/best). The bot will download and send the file if possible.", inline=False)
        embed.add_field(name="Debugging", value="If something fails, you'll get a detailed error message in Discord and the bot console.", inline=False)
        embed.set_footer(text="Made by Sablinova. Powered by yt-dlp.")
        await ctx.send(embed=embed)

class YTDView(discord.ui.View):
    def __init__(self, link, cog):
        super().__init__(timeout=60)
        self.link = link
        self.cog = cog
        self.format_choice = None
        self.quality_choice = None

    @discord.ui.button(label="MP3", style=discord.ButtonStyle.primary)
    async def mp3_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.format_choice = "mp3"
        await interaction.response.send_message("Choose quality: 720p, 1080p, best, etc.", ephemeral=True)

    @discord.ui.button(label="MP4", style=discord.ButtonStyle.primary)
    async def mp4_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.format_choice = "mp4"
        await interaction.response.send_message("Choose quality: 720p, 1080p, best, etc.", ephemeral=True)

    @discord.ui.button(label="720p", style=discord.ButtonStyle.secondary)
    async def q720p_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.quality_choice = "720"
        await self._download(interaction)

    @discord.ui.button(label="1080p", style=discord.ButtonStyle.secondary)
    async def q1080p_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.quality_choice = "1080"
        await self._download(interaction)

    @discord.ui.button(label="Best", style=discord.ButtonStyle.secondary)
    async def qbest_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.quality_choice = "best"
        await self._download(interaction)

    async def _download(self, interaction):
        if not self.format_choice or not self.quality_choice:
            await interaction.response.send_message("Please select both format and quality.", ephemeral=True)
            return
        await interaction.response.send_message(f"Downloading... Format: {self.format_choice}, Quality: {self.quality_choice}", ephemeral=True)
        file_path, error_msg = await self.cog.download_youtube_debug(self.link, self.format_choice, self.quality_choice)
        if file_path:
            try:
                await interaction.followup.send(file=discord.File(file_path))
            except Exception as e:
                await interaction.followup.send(f"File too large to send or error occurred: {e}")
        else:
            await interaction.followup.send(f"Download failed. Error: {error_msg}")


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
