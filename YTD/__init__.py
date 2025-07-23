from .ytd import YTD

async def setup(bot):
    await bot.add_cog(YTD(bot))