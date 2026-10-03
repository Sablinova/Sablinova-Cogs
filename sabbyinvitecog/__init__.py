from .sabbyinvitecog import SabbyInviteCog


async def setup(bot):
    await bot.add_cog(SabbyInviteCog(bot))
