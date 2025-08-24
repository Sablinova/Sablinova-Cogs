from .bridge import Bridge


async def setup(bot):
    await bot.add_cog(Bridge(bot))
