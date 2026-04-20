from .sabbyslashtags import SabbySlashTags


async def setup(bot):
    await bot.add_cog(SabbySlashTags(bot))
