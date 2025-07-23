import os
import discord
import asyncio
import traceback
import tempfile
from redbot.core import commands, Config
from datetime import datetime

class YTD(commands.Cog):
    """YouTube Downloader Cog"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=142020230713)
        self.config.register_global(cooldown_seconds=300)
        self.config.register_global(log_channel=None)
        self.last_used = None

        try:
            import yt_dlp
        except ImportError:
            import subprocess, sys
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yt-dlp'])
                print("yt-dlp installed.")
            except Exception as e:
                print("yt-dlp installation failed:", e)

    def is_admin_or_owner():
        async def predicate(ctx):
            if await ctx.bot.is_owner(ctx.author):
                return True
            if ctx.guild:
                perms = ctx.author.guild_permissions
                return perms.administrator or perms.manage_messages
            return False
        return commands.check(predicate)

    @commands.hybrid_command(name="ytd", with_app_command=True)
    @is_admin_or_owner()
    async def ytd(self, ctx: commands.Context, link: str = None, format_choice: str = "mp3", quality: str = "best"):
        """Download YouTube video/audio (mp3 or mp4). Run with no args to open a modal."""
        now = datetime.utcnow()
        cooldown = await self.config.cooldown_seconds()

        if not await self.bot.is_owner(ctx.author):
            if self.last_used and (now - self.last_used).total_seconds() < cooldown:
                wait_time = cooldown - (now - self.last_used).total_seconds()
                mins, secs = divmod(int(wait_time), 60)
                return await ctx.send(f"⏳ This command is on cooldown. Try again in {mins}m {secs}s.")
            self.last_used = now

        if ctx.interaction is not None and link is None:
            await ctx.interaction.response.send_modal(YTDModal(self))
            return

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
                await self.send_log(ctx, link, format_choice, quality, file_path)
                try:
                    os.remove(file_path)
                except:
                    pass
        else:
            await ctx.send("❌ Download failed. Check logs.")
            print("[YTD ERROR]", debug_log)
            await self.send_log(ctx, link, format_choice, quality, None)

    @commands.command()
    @commands.is_owner()
    async def ytdcooldown(self, ctx, time: str):
        """Owner only: Set cooldown (e.g. 5m, 30s, 1h)."""
        try:
            seconds = self.parse_time_to_seconds(time)
            await self.config.cooldown_seconds.set(seconds)
            await ctx.send(f"✅ Cooldown set to {time} ({seconds} seconds).")
        except Exception:
            await ctx.send("❌ Invalid format. Use like `30s`, `5m`, or `1h`.")

    def parse_time_to_seconds(self, text):
        units = {"s": 1, "m": 60, "h": 3600}
        num = int(text[:-1])
        unit = text[-1].lower()
        return num * units[unit]

    @commands.command()
    @commands.is_owner()
    async def ytdlogchannel(self, ctx, channel: discord.TextChannel = None):
        """Owner only: Set log channel to send YTD usage logs. Omit to clear."""
        if channel:
            await self.config.log_channel.set(channel.id)
            await ctx.send(f"✅ YTD log channel set to {channel.mention}")
        else:
            await self.config.log_channel.set(None)
            await ctx.send("❌ Log channel cleared.")

    async def send_log(self, ctx, link, fmt, quality, file_path):
        try:
            log_id = await self.config.log_channel()
            if not log_id:
                return
            channel = self.bot.get_channel(log_id)
            if not channel:
                return
            msg = (
                f"📥 **YTD Used**\n"
                f"👤 {ctx.author} (`{ctx.author.id}`)\n"
                f"🌐 Server: {ctx.guild.name if ctx.guild else 'DM'}\n"
                f"🔗 Link: {link}\n"
                f"🎞️ Format: `{fmt}` | Quality: `{quality}`"
            )
            if file_path and os.path.exists(file_path):
                await channel.send(msg, file=discord.File(file_path))
            else:
                await channel.send(msg + "\n⚠️ File not sent.")
        except Exception as e:
            print("[YTD LOG ERROR]", e)

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
                    return os.path.abspath(filename) if os.path.exists(filename) else None, "\n".join(log)
            except Exception:
                log.append(traceback.format_exc())
                return None, "\n".join(log)

        return await asyncio.get_event_loop().run_in_executor(None, run)

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

        await interaction.followup.send(f"⏬ Downloading `{fmt}` at `{qual}`...", ephemeral=True)
        file_path, debug_log = await self.cog.download_youtube(link, fmt, qual)

        if file_path:
            try:
                await interaction.followup.send(file=discord.File(file_path))
            except Exception as e:
                await interaction.followup.send(f"⚠️ Error sending file: `{e}`", ephemeral=True)
            finally:
                await self.cog.send_log(interaction, link, fmt, qual, file_path)
                try:
                    os.remove(file_path)
                except:
                    pass
        else:
            print("[YTD MODAL ERROR]", debug_log)
            await interaction.followup.send("❌ Download failed. Check logs.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print("[YTD MODAL EXCEPTION]", traceback.format_exc())
        await interaction.followup.send("❌ Unexpected error occurred. Check logs.", ephemeral=True)
