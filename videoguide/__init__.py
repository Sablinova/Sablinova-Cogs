from .guide import Guide
from redbot.core.bot import Red

async def setup(bot) -> None:
    await bot.add_cog(Guide(bot))