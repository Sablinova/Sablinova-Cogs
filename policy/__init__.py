from redbot.core.bot import Red

from .policy import Policy


async def setup(bot: Red) -> None:
    """Load the Policy cog."""
    await bot.add_cog(Policy(bot))
