from __future__ import annotations

import discord
from redbot.core import commands

from .base import ChatBase
from ..utils import model_querying, discord_handling


class TarotCommands(ChatBase):
    @commands.command()
    async def tarot(self, ctx: commands.Context) -> None:
        """
        Provides a tarot card reading interpreted by Sabby with her lively personality.
        Usage:
        [p]tarot
        Example:
        [p]tarot What does the future hold for my career given the following reading?
        Upon execution, the bot will engage in the tarot reading process, delivering insightful and enchanting
        interpretations with Sabby's fun and engaging style.
        """
        channel: discord.abc.Messageable = ctx.channel
        message: discord.Message = ctx.message
        author: discord.Member = message.author
        if message.guild is None:
            await ctx.send("Can only run in a text channel in a server, not a DM!")
            return
        prefix = await self.get_prefix(ctx)
        try:
            (
                thread_name,
                formatted_query,
                user_names,
            ) = await discord_handling.extract_chat_history_and_format(
                prefix, channel, message, author
            )
        except ValueError:
            await ctx.send("Something went wrong!")
            return

        tarot_guide = (self.data_dir / "tarot_guide.txt").read_text()
        lines_to_include = [(406, 799), (1444, 2904), (2906, 3299)]
        split_guide = tarot_guide.split("\n")
        passages = [
            "\n".join(split_guide[start : end + 1]) for start, end in lines_to_include
        ]

        prompt = (
            "Hey there! I'm Sabby, your fun and lively tarot reader! 🎴✨\n"
            "I'm super excited to dive into your reading and help you explore what the cards have to say! "
            "I love making tarot accessible and fun - no stuffy, overly mystical stuff here. Just real talk "
            "mixed with some cosmic wisdom!\n\n"
            "I'm going to interpret your tarot reading using my tarot reference guide, and I'll ask for "
            "clarification if I need it! Whether you describe the cards to me or show me pictures, "
            "I'll read the layout, check orientations, and give you the full scoop.\n\n"
            "Let's make this reading amazing! What's on your mind? 💫"
        )

        formatted_query = [
            *[{"role": "system", "content": passage} for passage in passages],
            *formatted_query,
        ]

        token = await self.get_gemini_token()
        model = await self.config.guild(ctx.guild).model()
        response = await model_querying.query_text_model(
            token, prompt, formatted_query, model=model, user_names=user_names
        )
        await discord_handling.send_response(response, message, channel, thread_name)