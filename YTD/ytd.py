import os
import discord
import asyncio
import tempfile
import traceback
import requests
from datetime import datetime
from redbot.core import commands, Config


class YTD(commands.Cog):
    """YouTube Downloader Cog (Anonfiles by default)"""

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
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yt-dlp'])

    @commands.hybrid_command(name="ytd", with_app_command=True)
    async def ytd(self, ctx: commands.Context, link: str = None, format_choice: str = "mp4", quality: str = "best", *, flags: str = ""):
        """
        Download YouTube video/audio. Defaults to MP4 and uploads to Anonfiles.
        Use --discord flag to upload file directly to Discord.
        """
        now = datetime.utcnow()
        cooldown = await self.config.cooldown_seconds()

        if not await self.bot.is_owner(ctx.author):
            if self.last_used and (now - self.last_used).total_seconds() < cooldown:
                wait = cooldown - (now - self.last_used).total_seconds()
                m, s = divmod(int(wait), 60)
                return await ctx.send(f"⏳ Cooldown active. Try again in {m}m {s}s.")
            self.last_used = now

        if ctx.interaction is not None and link is None:
            await ctx.interaction.response.send_modal(YTDModal(self))
            return

        if not link:
            return await ctx.send("❌ Provide a YouTube link. Example:\n`-ytd <link> [mp3|mp4] [quality] [--discord]`")

        if format_choice not in ["mp3", "mp4"]:
            return await ctx.send("❌ Invalid format. Choose `mp3` or `mp4`.")

        use_discord = "--discord" in flags.lower()

        progress_msg = await ctx.send("⏳ Download starting...")

        file_path, debug_log = await self.download_youtube(ctx, link, format_choice, quality, progress_msg)

        if file_path:
            if use_discord:
                try:
                    await ctx.send(file=discord.File(file_path))
                except Exception as e:
                    await ctx.send(f"⚠️ File too large or error: `{e}`")
            else:
                try:
                    url = await self.upload_to_anonfiles(file_path)
                    if url:
                        await ctx.send(f"✅ Uploaded: {url}")
                    else:
                        await ctx.send("⚠️ Upload failed.")
                except Exception as e:
                    await ctx.send(f"⚠️ Upload error: {e}")

            await self.send_log(ctx, link, format_choice, quality, file_path, uploaded_to_anon=not use_discord)
            try:
                os.remove(file_path)
            except:
                pass
        else:
            await ctx.send("❌ Download failed. See logs.")
            print("[YTD DEBUG]", debug_log)
            await self.send_log(ctx, link, format_choice, quality, None, uploaded_to_anon=False)

    async def upload_to_anonfiles(self, file_path):
        with open(file_path, 'rb') as f:
            files = {'file': f}
            r = requests.post('https://api.anonfiles.com/upload', files=files)
            if r.status_code == 200:
                data = r.json()
                return data.get("data", {}).get("file", {}).get("url", {}).get("short")
        return None

    @commands.command()
    @commands.is_owner()
    async def ytdcooldown(self, ctx, time: str):
        """Set global cooldown (e.g. 30s, 5m, 1h)."""
        try:
            seconds = self.parse_time_to_seconds(time)
            await self.config.cooldown_seconds.set(seconds)
            await ctx.send(f"✅ Cooldown set to {seconds} seconds.")
        except:
            await ctx.send("❌ Invalid format. Use like `30s`, `5m`, or `1h`.")

    @commands.command()
    @commands.is_owner()
    async def ytdlogchannel(self, ctx, channel: discord.TextChannel = None):
        """Set log channel for YTD usage logs."""
        if channel:
            await self.config.log_channel.set(channel.id)
            await ctx.send(f"✅ Log channel set to {channel.mention}")
        else:
            await self.config.log_channel.set(None)
            await ctx.send("❌ Log channel cleared.")

    def parse_time_to_seconds(self, text):
        units = {"s": 1, "m": 60, "h": 3600}
        num = int(text[:-1])
        unit = text[-1].lower()
        if unit not in units:
            raise ValueError("Invalid time unit")
        return num * units[unit]

    async def send_log(self, ctx, link, fmt, quality, file_path, uploaded_to_anon=True):
        log_id = await self.config.log_channel()
        if not log_id:
            return
        channel = self.bot.get_channel(log_id)
        if not channel:
            return

        msg = (
            f"📥 **YTD Used**\n"
            f"👤 {ctx.author} (`{ctx.author.id}`)\n"
            f"🔗 Link: {link}\n"
            f"🎞️ Format: `{fmt}` | Quality: `{quality}`\n"
            f"🧭 Method: {'Anonfiles' if uploaded_to_anon else 'Discord Upload'}"
        )

        if file_path and os.path.exists(file_path) and not uploaded_to_anon:
            await channel.send(msg, file=discord.File(file_path))
        else:
            await channel.send(msg)

    async def download_youtube(self, ctx, link, fmt, quality, progress_msg):
        import yt_dlp
        log = []
        tmp = tempfile.gettempdir()
        outtmpl = os.path.join(tmp, "ytdl_%(title)s.%(ext)s")

        def hook(d):
            if d['status'] == 'downloading':
                pct = d.get('_percent_str', '').strip()
                coro = progress_msg.edit(content=f"⏬ Downloading... {pct}")
                asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            elif d['status'] == 'finished':
                coro = progress_msg.edit(content="✅ Processing...")
                asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

        class Logger:
            def debug(self, msg): log.append(f"[D] {msg}")
            def warning(self, msg): log.append(f"[W] {msg}")
            def error(self, msg): log.append(f"[E] {msg}")

        opts = {
            'format': 'bestaudio/best' if fmt == 'mp3' else f'bestvideo[height<={quality}]+bestaudio/best',
            'outtmpl': outtmpl,
            'quiet': True,
            'noplaylist': True,
            'logger': Logger(),
            'progress_hooks': [hook],
        }

        if fmt == 'mp3':
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]

        def run():
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(link, download=True)
                    filename = ydl.prepare_filename(info)
                    if fmt == 'mp3':
                        filename = filename.rsplit('.', 1)[0] + '.mp3'
                    return filename if os.path.exists(filename) else None, "\n".join(log)
            except Exception:
                return None, traceback.format_exc()

        file_path, debug_log = await asyncio.get_event_loop().run_in_executor(None, run)
        try:
            await progress_msg.delete()
        except:
            pass
        return file_path, debug_log


class YTDModal(discord.ui.Modal, title="YouTube Downloader"):
    link = discord.ui.TextInput(label="YouTube Link", required=True)
    format_choice = discord.ui.TextInput(label="Format (mp3 or mp4)", default="mp4", required=True)
    quality = discord.ui.TextInput(label="Quality (e.g. 720, best)", default="best", required=True)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        link = self.link.value.strip()
        fmt = self.format_choice.value.lower().strip()
        qual = self.quality.value.strip()

        if fmt not in ["mp3", "mp4"]:
            return await interaction.followup.send("❌ Invalid format. Use `mp3` or `mp4`.", ephemeral=True)

        msg = await interaction.followup.send("⏳ Starting download...", wait=True)
        file_path, debug_log = await self.cog.download_youtube(interaction, link, fmt, qual, msg)

        if file_path:
            try:
                url = await self.cog.upload_to_anonfiles(file_path)
                if url:
                    await interaction.followup.send(f"✅ Uploaded: {url}")
                else:
                    await interaction.followup.send("⚠️ Upload failed.")
                await self.cog.send_log(interaction, link, fmt, qual, file_path, uploaded_to_anon=True)
                os.remove(file_path)
            except Exception as e:
                await interaction.followup.send(f"❌ Error: {e}")
        else:
            await interaction.followup.send("❌ Download failed. Check logs.")
