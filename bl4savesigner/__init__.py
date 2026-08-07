from .bl4resign import BL4Helper
from redbot.core.bot import Red

async def setup(bot: Red):
    await bot.add_cog(BL4Helper(bot))