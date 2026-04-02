from redbot.core.bot import Red

from .pubhelper import SabPubHelper


async def setup(bot: Red) -> None:
    """Load the SabPubHelper cog."""
    cog = SabPubHelper(bot)
    await bot.add_cog(cog)
    # Register slash command with the bot's tree
    bot.tree.add_command(cog.re9cc)


async def teardown(bot: Red) -> None:
    """Unload the SabPubHelper cog."""
    # Remove slash command from tree
    bot.tree.remove_command("re9cc")
