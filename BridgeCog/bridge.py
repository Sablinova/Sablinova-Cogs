import discord
from discord.ext import commands
from redbot.core import Config, checks, commands
import logging
from typing import Dict, Any

class Bridge(commands.Cog):
    """
    A cog to create and manage message bridges between text channels.

    This cog allows authorized users to bridge two channels together,
    so messages sent in one channel are automatically forwarded to the other.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_global(bridges={}, next_bridge_id=1, authorized_users=[])
        self.webhook_cache: Dict[int, discord.Webhook] = {}
        self.logger = logging.getLogger("red.bridge")

    async def _is_authorized(self, ctx: commands.Context) -> bool:
        """
        Check if the user is authorized to use bridge commands.
        Owners are always authorized.
        """
        if await self.bot.is_owner(ctx.author):
            return True
        authorized_users = await self.config.authorized_users()
        return ctx.author.id in authorized_users

    def _break_mentions(self, text: str) -> str:
        """
        Break mentions to avoid accidental pings.
        """
        return text.replace("@", "@ ")

    def _process_emojis(self, text: str) -> str:
        """
        Convert custom emojis into markdown-style image links.
        """
        return discord.utils.escape_mentions(text)

    async def _get_or_create_webhook(self, channel: discord.TextChannel) -> discord.Webhook:
        """
        Get or create a webhook for the given channel.

        Caches the webhook for efficiency.
        """
        key = self._get_webhook_cache_key(channel)
        if key in self.webhook_cache:
            return self.webhook_cache[key]

        webhooks = await channel.webhooks()
        webhook = discord.utils.get(webhooks, name="Bridge")
        if webhook is None:
            try:
                webhook = await channel.create_webhook(name="Bridge")
            except discord.Forbidden:
                self.logger.error("Failed to create webhook in %s", channel.name)
                return None
        self.webhook_cache[key] = webhook
        return webhook

    def _get_webhook_cache_key(self, channel: discord.TextChannel) -> int:
        """
        Generate a cache key for a webhook in a given channel.
        """
        return channel.id

    @commands.group()
    async def bridge(self, ctx: commands.Context):
        """
        Manage channel bridges.

        Use subcommands to create, remove, list, or clear bridges.
        """
        pass

    @bridge.command(name="authorize")
    async def bridge_authorize(self, ctx: commands.Context, user: discord.User):
        """
        Authorize a user to use bridge commands.

        **Arguments:**
        - `user`: The Discord user to authorize.

        **Examples:**
        - `[p]bridge authorize @Sol`
        - `[p]bridge authorize 123456789012345678`

        **Details:**
        - Owners always have access by default.
        - Authorized users can manage bridges just like the owner.
        """
        if not await self.bot.is_owner(ctx.author):
            return await ctx.send("Only the bot owner can authorize users.")

        async with self.config.authorized_users() as users:
            if user.id not in users:
                users.append(user.id)
        await ctx.send(f"{user} has been authorized.")

    @bridge.command(name="deauthorize")
    async def bridge_deauthorize(self, ctx: commands.Context, user: discord.User):
        """
        Remove a user's authorization for bridge commands.

        **Arguments:**
        - `user`: The Discord user to deauthorize.

        **Examples:**
        - `[p]bridge deauthorize @Sol`
        - `[p]bridge deauthorize 123456789012345678`

        **Details:**
        - Owners cannot be deauthorized.
        - The user will lose access to bridge setup, removal, and listing.
        """
        if not await self.bot.is_owner(ctx.author):
            return await ctx.send("Only the bot owner can deauthorize users.")

        async with self.config.authorized_users() as users:
            if user.id in users:
                users.remove(user.id)
        await ctx.send(f"{user} has been deauthorized.")

    @bridge.command(name="authorized")
    async def bridge_authorized(self, ctx: commands.Context):
        """
        Show the list of currently authorized users.

        **Examples:**
        - `[p]bridge authorized`

        **Details:**
        - Displays all users who can use bridge commands.
        - Owners are always implicitly authorized.
        """
        users = await self.config.authorized_users()
        if not users:
            await ctx.send("No authorized users.")
            return
        user_mentions = [f"<@{uid}>" for uid in users]
        await ctx.send("Authorized users: " + ", ".join(user_mentions))

    @bridge.command(name="setup")
    async def bridge_setup(self, ctx: commands.Context, channel1: discord.TextChannel, channel2: discord.TextChannel):
        """
        Set up a bridge between two channels.

        **Arguments:**
        - `channel1`: The first channel to bridge.
        - `channel2`: The second channel to bridge.

        **Examples:**
        - `[p]bridge setup #general #random`

        **Details:**
        - Prevents bridging the same channel with itself.
        - Prevents overlapping bridges.
        """
        if not await self._is_authorized(ctx):
            return await ctx.send("You are not authorized to use this command.")

        if channel1.id == channel2.id:
            return await ctx.send("You cannot bridge the same channel.")

        bridges = await self.config.bridges()
        for b in bridges.values():
            if channel1.id in (b["channel1_id"], b["channel2_id"]) or channel2.id in (b["channel1_id"], b["channel2_id"]):
                return await ctx.send("One of these channels is already bridged.")

        bridge_id = await self.config.next_bridge_id()
        await self.config.bridges.set_raw(bridge_id, value={
            "channel1_id": channel1.id,
            "channel2_id": channel2.id,
            "channel1_name": channel1.name,
            "channel2_name": channel2.name,
        })
        await self.config.next_bridge_id.set(bridge_id + 1)
        await ctx.send(f"Bridge {bridge_id} created between {channel1.mention} and {channel2.mention}.")

    @bridge.command(name="remove")
    async def bridge_remove(self, ctx: commands.Context, bridge_id: int):
        """
        Remove a bridge by its ID.

        **Arguments:**
        - `bridge_id`: The ID of the bridge to remove.

        **Examples:**
        - `[p]bridge remove 1`
        """
        if not await self._is_authorized(ctx):
            return await ctx.send("You are not authorized to use this command.")

        bridges = await self.config.bridges()
        if str(bridge_id) not in bridges:
            return await ctx.send("Invalid bridge ID.")

        bridge = bridges[str(bridge_id)]
        self.webhook_cache.pop(bridge["channel1_id"], None)
        self.webhook_cache.pop(bridge["channel2_id"], None)

        async with self.config.bridges() as bridges:
            del bridges[str(bridge_id)]
        await ctx.send(f"Bridge {bridge_id} removed.")

    @bridge.command(name="list")
    async def bridge_list(self, ctx: commands.Context):
        """
        List all active bridges.

        **Examples:**
        - `[p]bridge list`
        """
        if not await self._is_authorized(ctx):
            return await ctx.send("You are not authorized to use this command.")

        bridges = await self.config.bridges()
        if not bridges:
            return await ctx.send("No active bridges.")

        lines = []
        for bridge_id, bridge in bridges.items():
            channel1 = self.bot.get_channel(bridge["channel1_id"])
            channel2 = self.bot.get_channel(bridge["channel2_id"])
            c1_name = channel1.mention if channel1 else bridge.get("channel1_name", "Deleted")
            c2_name = channel2.mention if channel2 else bridge.get("channel2_name", "Deleted")
            lines.append(f"ID {bridge_id}: {c1_name} <-> {c2_name}")
        await ctx.send("\n".join(lines))

    @bridge.command(name="clear")
    async def bridge_clear(self, ctx: commands.Context):
        """
        Remove all bridges.

        **Examples:**
        - `[p]bridge clear`
        """
        if not await self._is_authorized(ctx):
            return await ctx.send("You are not authorized to use this command.")

        await self.config.bridges.clear()
        self.webhook_cache.clear()
        await ctx.send("All bridges cleared.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Forward messages across bridged channels.

        - Sanitizes mentions.
        - Handles replies with context.
        - Processes stickers and attachments.
        """
        if message.author.bot:
            return

        bridges = await self.config.bridges()
        for bridge_id, bridge in bridges.items():
            if message.channel.id == bridge["channel1_id"]:
                target_channel_id = bridge["channel2_id"]
            elif message.channel.id == bridge["channel2_id"]:
                target_channel_id = bridge["channel1_id"]
            else:
                continue

            target_channel = self.bot.get_channel(target_channel_id)
            if not target_channel:
                continue

            webhook = await self._get_or_create_webhook(target_channel)
            if not webhook:
                continue

            content = self._break_mentions(message.content)
            content = self._process_emojis(content)

            if message.reference:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                if ref_msg:
                    content = f"> **{ref_msg.author.display_name}:** {ref_msg.content}\n{content}"

            if message.stickers:
                for sticker in message.stickers:
                    content += f"\n[Sticker: {sticker.name}]({sticker.url})"

            for attachment in message.attachments:
                content += f"\n📎 [{attachment.filename}]({attachment.url})"

            try:
                await webhook.send(
                    content=content,
                    username=message.author.display_name,
                    avatar_url=message.author.display_avatar.url,
                )
            except Exception as e:
                self.logger.error("Failed to send message through webhook: %s", e)
