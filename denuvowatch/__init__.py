from redbot.core.bot import Red

from .denuvowatch import DenuvoTracker


async def setup(bot: Red) -> None:
    await bot.add_cog(DenuvoTracker(bot))