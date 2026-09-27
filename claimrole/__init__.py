from redbot.core.bot import Red
from .claimrole import ClaimRole


async def setup(bot: Red) -> None:
    cog = ClaimRole(bot)
    await bot.add_cog(cog)
