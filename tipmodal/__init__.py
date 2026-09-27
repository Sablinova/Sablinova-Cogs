from redbot.core.bot import Red
from .tipmodal import TipModal


async def setup(bot: Red) -> None:
    cog = TipModal(bot)
    await bot.add_cog(cog)
