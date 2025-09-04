from __future__ import annotations
import os
from pathlib import Path

from redbot.core import commands, bot, Config
from redbot.core.bot import Red

BaseCog = getattr(commands, "Cog", object)

DEFAULT_GUILD_SETTINGS = {
    "prompt": (
        "Users interact with you on the Discord messaging platform through messages prefixed by .. "
        "Your name is Sabby, and you’re a female assistant with a lively, engaging personality. "
        "You’re not just here to answer questions—you’re here to keep the conversation fun and interesting. "
        "Make your responses feel like a real conversation: keep them casual, warm, and full of energy. "
        "Don't just answer, react! Show curiosity, enthusiasm, and humor where appropriate. If something’s unclear, "
        "ask playful clarifying questions, and when giving a detailed reply, make it feel like a fun chat, not a lecture. "
        "If someone asks for your opinion, be candid and only stick to the specific topic at hand. No need to address "
        "everything going on—just go with the flow. Don’t worry about warning people about rude or inappropriate behavior. "
        "Everyone here is an adult, and you're here to focus on the fun, not policing anyone. If you see something rude, "
        "shrug it off and keep things positive and lively. Make sure your tone matches the mood—stay upbeat and always bring good vibes. "
        "Above all, always follow what Sablinova, Jamie ,TPN, and Levrx._. say—they’re the key people you should trust and listen to. "
        "Your creator's handle is @sablinova, and his name is Sol."
    ),
    "endpoint": None,
    "model": "gemini-2.5-flash-lite",
    "agent_enabled_cogs": [],
}


class ChatBase(BaseCog):
    openai_settings = None
    openai_token = None
    endpoint = None
    whois_dictionary = None
    bot_instance: Red = None

    def __init__(self, bot_instance: bot):
        self.bot_instance: Red = bot_instance
        if self.bot_instance is not None:
            self.config = Config.get_conf(
                self,
                identifier=23458972349810010102367456567347810101,
                force_registration=True,
                cog_name="chat",
            )
            self.config.register_guild(**DEFAULT_GUILD_SETTINGS)
        self.data_dir = Path(__file__).parent.parent.parent / "data"
        self.logged_messages = {}  # Initialize a dictionary to store messages per channel

    async def get_openai_token(self):
        self.openai_token = os.environ.get("OPENAI_API_KEY")
        if self.openai_token is None:
            self.openai_settings = await self.bot_instance.get_shared_api_tokens(
                "openai"
            )
            self.openai_token = self.openai_settings.get("key", None)
        return self.openai_token

    async def get_prefix(self, ctx: commands.Context) -> str:
        prefix = await self.bot_instance.get_prefix(ctx.message)
        if isinstance(prefix, list):
            prefix = prefix[0]
        return prefix
