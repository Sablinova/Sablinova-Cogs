import discord
from redbot.core import commands, Config
from aiohttp import web
import aiohttp
import logging
import asyncio

# Configuration Defaults
DEFAULT_PORT = 5000
OWNER_ID = 426878496468500493
OPENCLAW_HOOK_URL = "https://hooks.2b.sablinova.com/hooks/wake"
OPENCLAW_HOOK_TOKEN = "b1c7d2aa6977e410eead37f21daaca39f38355f44e5a8bcff089e4a22dc156cf"

log = logging.getLogger("red.sablinova.sabby")

class Sabby(commands.Cog):
    """
    Two-way bridge between Sol and Sabby (OpenClaw AI).
    - Listens for mentions -> forwards to Sabby.
    - Runs a webserver -> listens for replies/commands from Sabby.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=4268784964, force_registration=True)
        self.config.register_global(
            port=DEFAULT_PORT, 
            openclaw_url=OPENCLAW_HOOK_URL, 
            token=OPENCLAW_HOOK_TOKEN,
            public_url=None
        )
        self.session = aiohttp.ClientSession()
        
        # Webserver setup
        self.app = web.Application()
        self.app.router.add_post("/reply", self.handle_reply)
        self.runner = None
        self.site = None
        
        # Start server loop
        self.bot.loop.create_task(self.start_server())

    def cog_unload(self):
        self.bot.loop.create_task(self.stop_server())
        self.bot.loop.create_task(self.session.close())

    async def start_server(self):
        port = await self.config.port()
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "0.0.0.0", port)
        await self.site.start()
        log.info(f"Sabby Bridge listening on 0.0.0.0:{port}")

    async def stop_server(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

    async def handle_reply(self, request):
        """
        Handle incoming POST from Sabby (OpenClaw).
        Payload: { "channel_id": <int>, "content": <str>, "command": <bool> }
        """
        try:
            data = await request.json()
            channel_id = int(data.get("channel_id", 0))
            content = data.get("content", "")
            is_command = data.get("command", False)

            if not channel_id or not content:
                return web.Response(text="Missing channel_id or content", status=400)

            channel = self.bot.get_channel(channel_id)
            if not channel:
                return web.Response(text="Channel not found", status=404)

            if is_command:
                # Execute as a command (simple text sending for now, but could be ctx.invoke)
                # For safety, we just send the text, but if it starts with prefix, Red might ignore bot messages.
                # To truly execute commands, we need to mock a context.
                # For now, let's just send the message.
                await channel.send(content)
            else:
                # Just a normal reply
                await channel.send(content)

            return web.Response(text="OK", status=200)

        except Exception as e:
            log.error(f"Failed to handle Sabby reply: {e}", exc_info=True)
            return web.Response(text=str(e), status=500)

    @commands.group()
    async def sabbyconf(self, ctx):
        """Configure Sabby bridge settings."""
        pass

    @sabbyconf.command()
    async def port(self, ctx, port: int):
        """Set the port for the incoming webserver (Default: 5000)."""
        await self.config.port.set(port)
        await ctx.send(f"Port set to {port}. Reload cog to apply.")

    @sabbyconf.command()
    async def url(self, ctx, url: str):
        """Set the public URL that Sabby should reply to (e.g. https://sabby.sablinova.com/reply)."""
        await self.config.public_url.set(url)
        await ctx.send(f"Reply URL set to: `{url}`")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if self.bot.user not in message.mentions and not (message.reference and message.reference.resolved and message.reference.resolved.author == self.bot.user):
            return

        if message.author.id != OWNER_ID:
            return

        content = message.content.replace(f"<@{self.bot.user.id}>", "").replace(f"<@!{self.bot.user.id}>", "").strip()
        if not content:
            content = "Hello Sabby"

        # Construct payload for OpenClaw /hooks/wake
        channel_id = str(message.channel.id)
        public_url = await self.config.public_url()
        
        # Format the system event text
        formatted_text = f"[RedBot Relay] ChannelID: {channel_id} | User: {message.author.name} | Message: {content}"
        
        if public_url:
            formatted_text += f" | ReplyURL: {public_url}"

        payload = {
            "text": formatted_text,
            "mode": "now"
        }

        headers = {
            "Authorization": f"Bearer {await self.config.token()}",
            "Content-Type": "application/json"
        }

        try:
            url = await self.config.openclaw_url()
            async with self.session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    log.error(f"Sabby wake failed: {resp.status}")
                    await message.add_reaction("⚠️")
                else:
                    await message.add_reaction("🧠")

        except Exception as e:
            log.error(f"Sabby connection error: {e}")
            await message.add_reaction("❌")

async def setup(bot):
    await bot.add_cog(Sabby(bot))
