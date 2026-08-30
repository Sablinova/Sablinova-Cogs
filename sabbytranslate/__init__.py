from redbot.core.bot import Red
from .sabbytranslate import SabbyTranslate


async def setup(bot: Red):
    cog = SabbyTranslate(bot)
    await bot.add_cog(cog)
