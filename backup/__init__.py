from .backup import Backup

async def setup(bot) -> None:
    await bot.add_cog(Backup(bot))
