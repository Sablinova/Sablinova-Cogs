from .sabmutemessage import SabMuteMessage


async def setup(bot):
    await bot.add_cog(SabMuteMessage(bot))
