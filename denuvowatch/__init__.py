from redbot.core.bot import Red

from .denuvowatch import DenuvoWatch


async def setup(bot: Red) -> None:
    await bot.add_cog(DenuvoWatch(bot))
