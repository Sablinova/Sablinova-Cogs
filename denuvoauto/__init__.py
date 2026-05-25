from redbot.core.bot import Red

from .denuvoauto import Denuvoauto


async def setup(bot: Red) -> None:
    await bot.add_cog(Denuvoauto(bot))
