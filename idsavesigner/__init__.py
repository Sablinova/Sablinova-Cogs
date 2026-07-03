from .idsaveresign import IdSaveResign, setup  # noqa: F401
from redbot.core.bot import Red

async def setup(bot: Red):
    await bot.add_cog(IdSaveResign(bot))