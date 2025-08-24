import discord
import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from redbot.core import commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.bridge")


class Bridge(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=1234567890, force_registration=True
        )

        default_global = {"bridges": {}, "next_bridge_id": 1}

        self.config.register_global(**default_global)

        self.webhook_cache: Dict[int, discord.Webhook] = {}

    async def cog_load(self):
        await self._initialize_webhooks()

    async def _initialize_webhooks(self):
        bridges = await self.config.bridges()
        for bridge_id, bridge_data in bridges.items():
            for webhook_key in ["webhook1", "webhook2"]:
                if webhook_key in bridge_data and bridge_data[webhook_key]:
                    try:
                        webhook = discord.Webhook.from_url(
                            bridge_data[webhook_key],
                            session=self.bot.http._HTTPClient__session,
                        )
                        cache_key = int(bridge_id) * 10 + (
                            1 if webhook_key == "webhook1" else 2
                        )
                        self.webhook_cache[cache_key] = webhook
                    except Exception as e:
                        log.error(
                            f"Failed to initialize webhook for bridge {bridge_id}: {e}"
                        )

    async def _get_or_create_webhook(
        self, channel: discord.TextChannel, name: str = "Bridge Webhook"
    ) -> Optional[discord.Webhook]:
        try:
            webhooks = await channel.webhooks()
            for webhook in webhooks:
                if webhook.name == name and webhook.user == self.bot.user:
                    return webhook

            webhook = await channel.create_webhook(
                name=name, reason="Bridge channel setup"
            )
            return webhook
        except discord.Forbidden:
            log.error(f"No permission to manage webhooks in {channel.mention}")
            return None
        except Exception as e:
            log.error(f"Failed to create webhook in {channel.mention}: {e}")
            return None

    def _break_mentions(self, text: str) -> str:
        import re

        invis = "\u200b"

        # no pings
        text = re.sub(r"<@!?(\d+)>", rf"<@{invis}\1>", text)
        text = re.sub(r"<@&(\d+)>", rf"<@&{invis}\1>", text)

        # no @everyone or @here
        text = text.replace("@everyone", f"@{invis}everyone")
        text = text.replace("@here", f"@{invis}here")

        # no invite links
        text = re.sub(r"discord\.gg/(\w+)", r"discord\.gg/[REDACTED]", text)
        text = re.sub(
            r"discord\.com/invite/(\w+)", r"discord\.com/invite/[REDACTED]", text
        )

        return text

    def _process_emojis(self, text: str) -> str:
        import re

        def emoji_replacer(match):
            animated = match.group(1) == "a"
            name = match.group(2)
            emoji_id = match.group(3)

            if animated:
                emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.gif?size=48"
            else:
                emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.png?size=48"

            return f"[:{name}:]({emoji_url})"

        text = re.sub(r"<(a)?:(\w+):(\d+)>", emoji_replacer, text)

        return text

    def _get_webhook_cache_key(self, bridge_id: int, is_webhook1: bool) -> int:
        return bridge_id * 10 + (1 if is_webhook1 else 2)

    async def _send_webhook_message(
        self, webhook: discord.Webhook, message: discord.Message, server_name: str
    ):
        try:
            content = message.content

            if message.reference and message.reference.message_id:
                try:
                    referenced_msg = None
                    if message.reference.cached_message:
                        referenced_msg = message.reference.cached_message
                    else:
                        try:
                            channel = message.channel
                            referenced_msg = await channel.fetch_message(
                                message.reference.message_id
                            )
                        except:
                            pass

                    if referenced_msg:
                        original_author = referenced_msg.author.display_name
                        original_content = referenced_msg.content

                        if referenced_msg.author.bot and referenced_msg.webhook_id:
                            lines = original_content.split("\n")
                            cleaned_lines = []

                            for line in lines:
                                if (
                                    line.startswith("> **")
                                    and ":**" in line
                                    and len(cleaned_lines) == 0
                                ):
                                    continue
                                elif line.startswith("-# [User from "):
                                    continue
                                else:
                                    cleaned_lines.append(line)

                            original_content = "\n".join(cleaned_lines).strip()

                            if not original_content:
                                original_content = "*[bridged message]*"

                        if len(original_content) > 100:
                            original_content = original_content[:100] + "..."

                        if original_content:
                            reply_header = (
                                f"> **{original_author}:** {original_content}"
                            )
                        else:
                            reply_header = (
                                f"> **{original_author}:** *[attachment or embed]*"
                            )

                        if content:
                            content = f"{reply_header}\n{content}"
                        else:
                            content = reply_header
                    else:
                        reply_header = f"> *Replying to a message...*"
                        if content:
                            content = f"{reply_header}\n{content}"
                        else:
                            content = reply_header
                except Exception as e:
                    log.error(f"Failed to process reply: {e}")

            if message.attachments:
                attachment_info = []
                for attachment in message.attachments:
                    attachment_info.append(
                        f"📎 [{attachment.filename}]({attachment.url})"
                    )

                if content:
                    content += f"\n\n{' • '.join(attachment_info)}"
                else:
                    content = " • ".join(attachment_info)

            if message.stickers:
                sticker_info = []
                for sticker in message.stickers:
                    sticker_url = None
                    if sticker.format == discord.StickerFormatType.png:
                        sticker_url = sticker.url
                    elif sticker.format == discord.StickerFormatType.apng:
                        sticker_url = sticker.url
                    else:
                        sticker_url = sticker.url

                    sticker_info.append(
                        f"[Sticker: {sticker.name}]({sticker_url + '?size=160'})"
                    )

                if content:
                    content += f"\n\n{' • '.join(sticker_info)}"
                elif sticker_info:
                    content = " • ".join(sticker_info)

            if content:
                content += f"\n-# [User from {server_name}]"
            else:
                content = f"-# [User from {server_name}]"

            content = self._break_mentions(content)
            content = self._process_emojis(content)

            await webhook.send(
                content=content,
                username=message.author.display_name,
                avatar_url=message.author.display_avatar.url,
                wait=False,
            )

        except Exception as e:
            log.error(f"Failed to send webhook message: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        bridges = await self.config.bridges()

        for bridge_id, bridge_data in bridges.items():
            channel1_id = bridge_data.get("channel1")
            channel2_id = bridge_data.get("channel2")

            if message.channel.id == channel1_id:
                target_channel = self.bot.get_channel(channel2_id)
                if target_channel:
                    cache_key = self._get_webhook_cache_key(int(bridge_id), False)
                    webhook = self.webhook_cache.get(cache_key)
                    if webhook:
                        await self._send_webhook_message(
                            webhook, message, message.guild.name
                        )

            elif message.channel.id == channel2_id:
                target_channel = self.bot.get_channel(channel1_id)
                if target_channel:
                    cache_key = self._get_webhook_cache_key(int(bridge_id), True)
                    webhook = self.webhook_cache.get(cache_key)
                    if webhook:
                        await self._send_webhook_message(
                            webhook, message, message.guild.name
                        )

    @commands.group(name="bridge", aliases=["bdg"])  # Changed from "br" to "bdg"
    @checks.is_owner()
    async def bridge(self, ctx):
        """
        Bridge channels together to relay messages between them.
        
        This command group allows you to create, manage, and remove bridges
        between Discord channels. Messages sent in one bridged channel will
        be forwarded to the other channel using webhooks.
        """
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="🌉 Bridge Commands",
                description="Create and manage channel bridges to relay messages between channels.",
                color=discord.Color.blue(),
            )
            
            embed.add_field(
                name="📝 Setup",
                value="`[p]bridge setup <channel1> <channel2>`\n"
                      "Create a new bridge between two channels.\n"
                      "Requires webhook permissions in both channels.",
                inline=False,
            )
            
            embed.add_field(
                name="📋 List",
                value="`[p]bridge list`\n"
                      "Show all active bridges with their channel information.",
                inline=False,
            )
            
            embed.add_field(
                name="🗑️ Remove",
                value="`[p]bridge remove <bridge_id>`\n"
                      "Remove an existing bridge by its ID number.\n"
                      "Use `[p]bridge list` to find bridge IDs.",
                inline=False,
            )
            
            embed.add_field(
                name="ℹ️ How it works",
                value="• Messages are relayed using webhooks\n"
                      "• Replies, attachments, and stickers are supported\n"
                      "• Mentions are sanitized to prevent unwanted pings\n"
                      "• Each message shows which server it came from",
                inline=False,
            )
            
            embed.add_field(
                name="⚠️ Requirements",
                value="• Bot must have 'Manage Webhooks' permission\n"
                      "• Bot must be able to read messages in both channels\n"
                      "• Only bot owners can manage bridges",
                inline=False,
            )
            
            embed.set_footer(text="Use [p]help bridge <command> for detailed help on specific commands")
            
            await ctx.send(embed=embed)

    @bridge.command(name="setup")
    async def bridge_setup(self, ctx, channel1, channel2):
        """
        Create a bridge between two channels.
        
        Parameters:
        - channel1: First channel to bridge (mention, ID, or name)
        - channel2: Second channel to bridge (mention, ID, or name)
        
        Example:
        - `[p]bridge setup #general #announcements`
        - `[p]bridge setup 123456789 987654321`
        
        The bot will create webhooks in both channels and start relaying
        messages between them. Both channels can be on different servers.
        """
        if isinstance(channel1, str) and channel1.isdigit():
            channel1 = self.bot.get_channel(int(channel1))
        elif isinstance(channel1, int):
            channel1 = self.bot.get_channel(channel1)

        if isinstance(channel2, str) and channel2.isdigit():
            channel2 = self.bot.get_channel(int(channel2))
        elif isinstance(channel2, int):
            channel2 = self.bot.get_channel(channel2)

        if not channel1:
            return await ctx.send(
                "❌ First channel not found! Make sure the bot has access to it, along with webhook perms."
            )
        if not channel2:
            return await ctx.send(
                "❌ Second channel not found! Make sure the bot has access to it, along with webhook perms."
            )

        if not isinstance(channel1, discord.TextChannel):
            return await ctx.send("❌ First channel must be a text channel!")
        if not isinstance(channel2, discord.TextChannel):
            return await ctx.send("❌ Second channel must be a text channel!")
        if channel1.id == channel2.id:
            return await ctx.send("❌ Cannot bridge a channel to itself!")

        bridges = await self.config.bridges()
        for bridge_id, bridge_data in bridges.items():
            if channel1.id in [
                bridge_data.get("channel1"),
                bridge_data.get("channel2"),
            ] or channel2.id in [
                bridge_data.get("channel1"),
                bridge_data.get("channel2"),
            ]:
                return await ctx.send(
                    f"❌ One of these channels is already part of bridge #{bridge_id}"
                )

        webhook1 = await self._get_or_create_webhook(channel1)
        webhook2 = await self._get_or_create_webhook(channel2)

        if not webhook1:
            return await ctx.send(
                f"❌ Failed to create webhook in {channel1.mention}. Check bot permissions."
            )
        if not webhook2:
            return await ctx.send(
                f"❌ Failed to create webhook in {channel2.mention}. Check bot permissions."
            )

        bridge_id = await self.config.next_bridge_id()

        bridge_data = {
            "channel1": channel1.id,
            "channel2": channel2.id,
            "webhook1": webhook1.url,
            "webhook2": webhook2.url,
            "created_by": ctx.author.id,
            "channel1_name": f"{channel1.guild.name}#{channel1.name}",
            "channel2_name": f"{channel2.guild.name}#{channel2.name}",
        }

        async with self.config.bridges() as bridges:
            bridges[str(bridge_id)] = bridge_data

        await self.config.next_bridge_id.set(bridge_id + 1)

        cache_key1 = self._get_webhook_cache_key(bridge_id, True)
        cache_key2 = self._get_webhook_cache_key(bridge_id, False)
        self.webhook_cache[cache_key1] = webhook1
        self.webhook_cache[cache_key2] = webhook2

        embed = discord.Embed(
            title="✅ Bridge Created",
            description=f"Bridge #{bridge_id} established between:\n"
            f"📍 {channel1.mention} ({channel1.guild.name})\n"
            f"📍 {channel2.mention} ({channel2.guild.name})",
            color=discord.Color.green(),
        )

        await ctx.send(embed=embed)

    @bridge.command(name="list")
    async def bridge_list(self, ctx):
        """
        Show all active bridges.
        
        Displays a list of all currently active bridges with:
        - Bridge ID numbers
        - Channel names and server information
        - Status of each channel (accessible or not)
        
        Use the bridge IDs shown here with the remove command.
        """
        bridges = await self.config.bridges()

        if not bridges:
            return await ctx.send("No active bridges found.")

        embed = discord.Embed(title="Active Bridges", color=discord.Color.blue())

        for bridge_id, bridge_data in bridges.items():
            channel1 = self.bot.get_channel(bridge_data.get("channel1"))
            channel2 = self.bot.get_channel(bridge_data.get("channel2"))

            channel1_info = (
                f"{channel1.mention} ({channel1.guild.name})"
                if channel1
                else f"❌ {bridge_data.get('channel1_name', 'Unknown')}"
            )
            channel2_info = (
                f"{channel2.mention} ({channel2.guild.name})"
                if channel2
                else f"❌ {bridge_data.get('channel2_name', 'Unknown')}"
            )

            embed.add_field(
                name=f"Bridge #{bridge_id}",
                value=f"{channel1_info}\n⬆️⬇️\n{channel2_info}",
                inline=False,
            )

        await ctx.send(embed=embed)

    @bridge.command(name="remove", aliases=["delete", "rm"])
    async def bridge_remove(self, ctx, bridge_id: int):
        """
        Remove an active bridge.
        
        Parameters:
        - bridge_id: The ID number of the bridge to remove
        
        Example:
        - `[p]bridge remove 1`
        - `[p]bridge delete 2`
        
        This will stop message relaying between the bridged channels.
        Note: The webhooks may still exist in the channels but will
        no longer be used by the bridge.
        
        Use `[p]bridge list` to see bridge IDs.
        """
        bridges = await self.config.bridges()

        if str(bridge_id) not in bridges:
            return await ctx.send(f"❌ Bridge #{bridge_id} not found.")

        bridge_data = bridges[str(bridge_id)]

        cache_key1 = self._get_webhook_cache_key(bridge_id, True)
        cache_key2 = self._get_webhook_cache_key(bridge_id, False)
        self.webhook_cache.pop(cache_key1, None)
        self.webhook_cache.pop(cache_key2, None)

        async with self.config.bridges() as bridges:
            del bridges[str(bridge_id)]

        embed = discord.Embed(
            title="✅ Bridge Removed",
            description=f"Bridge #{bridge_id} has been removed.\n"
            f"Webhooks may still exist in the channels.",
            color=discord.Color.red(),
        )

        await ctx.send(embed=embed)
