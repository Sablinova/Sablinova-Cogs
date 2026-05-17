import importlib
from redbot.core.bot import Red

from . import savesigner
importlib.reload(savesigner)

from .pubhelper import SabPubHelper


async def setup(bot: Red) -> None:
    """Load the SabPubHelper cog."""
    await bot.add_cog(SabPubHelper(bot))
