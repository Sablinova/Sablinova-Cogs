from .ytd import YTD

def setup(bot):
    bot.add_cog(YTD(bot))
