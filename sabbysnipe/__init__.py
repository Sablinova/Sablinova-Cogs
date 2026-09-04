from redbot.core.bot import Red
from .sabbysnipe import SabbySnipe


async def setup(bot: Red) -> None:
    cog = SabbySnipe(bot)
    await bot.add_cog(cog)
