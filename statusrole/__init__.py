from .statusrole import StatusRole

async def setup(bot):
    await bot.add_cog(StatusRole(bot))
