import discord
from redbot.core import commands
import aiohttp
import logging

# Configuration
OWNER_ID = 426878496468500493
# Switch to the /hooks/wake endpoint for main-session injection
OPENCLAW_HOOK_URL = "https://hooks.2b.sablinova.com/hooks/wake"
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
        if message.author.bot:
            return

        if self.bot.user not in message.mentions and not (message.reference and message.reference.resolved and message.reference.resolved.author == self.bot.user):
            return

        if message.author.id != OWNER_ID:
            return

        content = message.content.replace(f"<@{self.bot.user.id}>", "").replace(f"<@!{self.bot.user.id}>", "").strip()
        if not content:
            content = "Hello Sabby"

        log.info(f"Sabby triggered by {message.author}: {content}")

        # Send to OpenClaw /hooks/wake (Main Session Injection)
        channel_id = str(message.channel.id)
        
        # We format the text so Sabby knows where to reply
        # Context: [RedBot Relay] ChannelID: <id> | User: <name> | Message: <content>
        formatted_text = f"[RedBot Relay] ChannelID: {channel_id} | User: {message.author.name} | Message: {content}"

        payload = {
            "text": formatted_text,
            "mode": "now"
        }

        headers = {
            "Authorization": f"Bearer {OPENCLAW_HOOK_TOKEN}",
            "Content-Type": "application/json"
        }

        try:
            async with self.session.post(OPENCLAW_HOOK_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    response_text = await resp.text()
                    log.error(f"Sabby wake failed: {resp.status} - {response_text}")
                    await message.add_reaction("⚠️")
                else:
                    # Success: Sabby's main brain received the event.
                    # She will see the system log and should proactively reply.
                    await message.add_reaction("🧠")

        except Exception as e:
            log.error(f"Sabby connection error: {e}", exc_info=True)
            await message.add_reaction("❌")

async def setup(bot):
    await bot.add_cog(Sabby(bot))
