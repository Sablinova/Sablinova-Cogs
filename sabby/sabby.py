import discord
from redbot.core import commands
import aiohttp
import logging

# Configuration
OWNER_ID = 426878496468500493
OPENCLAW_HOOK_URL = "https://hooks.2b.sablinova.com/hooks/agent"
OPENCLAW_HOOK_TOKEN = "b1c7d2aa6977e410eead37f21daaca39f38355f44e5a8bcff089e4a22dc156cf"

log = logging.getLogger("red.sablinova.sabby")

class Sabby(commands.Cog):
    """
    Routes mentions from Sol to Sabby (OpenClaw AI) and posts replies back.
    """

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        self.bot.loop.create_task(self.session.close())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 1. Ignore bots
        if message.author.bot:
            return

        # 2. Check if bot is mentioned
        if self.bot.user not in message.mentions and not (message.reference and message.reference.resolved and message.reference.resolved.author == self.bot.user):
            return

        # 3. Only respond to the owner (whitelist)
        if message.author.id != OWNER_ID:
            log.debug(f"Sabby ignored message from {message.author.id} (not owner)")
            return

        # 4. Prepare content (strip mention)
        content = message.content.replace(f"<@{self.bot.user.id}>", "").replace(f"<@!{self.bot.user.id}>", "").strip()
        
        # If empty (just a ping), use a default greeting or context
        if not content:
            content = "Hello Sabby"

        log.info(f"Sabby triggered by {message.author}: {content}")

        # 5. Send to OpenClaw Webhook
        channel_id = str(message.channel.id)
        
        # Construct payload for OpenClaw /hooks/agent
        payload = {
            "message": content,
            "name": "RedBot-Relay",
            "deliver": True, # OpenClaw will reply via its own Discord connection
            "channel": "discord",
            "to": f"channel:{channel_id}",
            "wakeMode": "now"
        }

        headers = {
            "Authorization": f"Bearer {OPENCLAW_HOOK_TOKEN}",
            "Content-Type": "application/json"
        }

        try:
            log.debug(f"Posting to {OPENCLAW_HOOK_URL} with payload: {payload}")
            async with self.session.post(OPENCLAW_HOOK_URL, json=payload, headers=headers) as resp:
                response_text = await resp.text()
                if resp.status not in (200, 202):
                    log.error(f"Sabby hook failed: {resp.status} - {response_text}")
                    await message.add_reaction("⚠️")
                    await message.channel.send(f"⚠️ **Sabby Error:** HTTP {resp.status}\n`{response_text}`")
                else:
                    log.info(f"Sabby hook success: {resp.status}")
                    await message.add_reaction("👀")

        except Exception as e:
            log.error(f"Sabby hook exception: {e}", exc_info=True)
            await message.add_reaction("❌")
            await message.channel.send(f"⚠️ **Sabby Connection Error:** {e}")

async def setup(bot):
    await bot.add_cog(Sabby(bot))
