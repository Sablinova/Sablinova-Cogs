"""
ClaimRole Cog for Red-DiscordBot.
Provides customizable button role panels with emojis, colors, webhook support,
and anti-abuse rate limiting and hierarchy safety.
"""

import asyncio
import logging
import re
import time
from typing import Dict, List, Optional, Tuple, Union

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

logger = logging.getLogger("red.sablinova.claimrole")

# Color / Style mapping for Discord UI buttons
STYLE_MAP = {
    "blurple": discord.ButtonStyle.primary,
    "blue": discord.ButtonStyle.primary,
    "primary": discord.ButtonStyle.primary,
    "green": discord.ButtonStyle.success,
    "success": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
    "danger": discord.ButtonStyle.danger,
    "gray": discord.ButtonStyle.secondary,
    "grey": discord.ButtonStyle.secondary,
    "secondary": discord.ButtonStyle.secondary,
}

STYLE_NAMES = {
    discord.ButtonStyle.primary: "blurple",
    discord.ButtonStyle.success: "green",
    discord.ButtonStyle.danger: "red",
    discord.ButtonStyle.secondary: "gray",
}


def parse_button_style(style_str: Optional[str]) -> discord.ButtonStyle:
    """Parse a user-provided string into a discord.ButtonStyle."""
    if not style_str:
        return discord.ButtonStyle.secondary
    return STYLE_MAP.get(style_str.lower().strip(), discord.ButtonStyle.secondary)


def parse_emoji_string(guild: Optional[discord.Guild], emoji_str: Optional[str]) -> Optional[Union[discord.Emoji, discord.PartialEmoji, str]]:
    """Parse custom or standard unicode emoji string safely."""
    if not emoji_str or emoji_str.lower() in ("none", "no"):
        return None

    emoji_str = emoji_str.strip()

    # Custom emoji format: <:name:id> or <a:name:id>
    custom_match = re.match(r"^<a?:([a-zA-Z0-9_]+):([0-9]+)>$", emoji_str)
    if custom_match:
        emoji_id = int(custom_match.group(2))
        if guild:
            guild_emoji = guild.get_emoji(emoji_id)
            if guild_emoji:
                return guild_emoji
        try:
            return discord.PartialEmoji.from_str(emoji_str)
        except Exception:
            return None

    # Check by numeric ID
    if emoji_str.isdigit() and guild:
        guild_emoji = guild.get_emoji(int(emoji_str))
        if guild_emoji:
            return guild_emoji

    # Standard Unicode emoji
    return emoji_str


class ClaimRoleDynamicButton(discord.ui.DynamicItem[discord.ui.Button], template=r"claimrole:toggle:(?P<role_id>[0-9]+)"):
    """Persistent dynamic button that handles role claiming even across restarts."""

    def __init__(
        self,
        role_id: int,
        label: Optional[str] = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: Optional[Union[discord.Emoji, discord.PartialEmoji, str]] = None,
    ):
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                emoji=emoji,
                custom_id=f"claimrole:toggle:{role_id}",
            )
        )
        self.role_id = role_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        role_id = int(match.group("role_id"))
        return cls(role_id=role_id, label=item.label, style=item.style, emoji=item.emoji)

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("ClaimRole")
        if cog and hasattr(cog, "handle_role_toggle"):
            await cog.handle_role_toggle(interaction, self.role_id)
        else:
            await interaction.response.send_message("❌ Role claim service is currently unavailable.", ephemeral=True)


class ClaimRoleView(discord.ui.View):
    """Persistent view holding a set of role claim buttons."""

    def __init__(self, buttons_data: Optional[List[dict]] = None, guild: Optional[discord.Guild] = None):
        super().__init__(timeout=None)
        if buttons_data:
            for b in buttons_data:
                role_id = int(b["role_id"])
                label = b.get("label") or None
                style = parse_button_style(b.get("style", "secondary"))
                emoji = parse_emoji_string(guild, b.get("emoji"))
                btn = discord.ui.Button(
                    label=label,
                    style=style,
                    emoji=emoji,
                    custom_id=f"claimrole:toggle:{role_id}",
                )
                btn.callback = self.make_callback(role_id)
                self.add_item(btn)

    def make_callback(self, role_id: int):
        async def _callback(interaction: discord.Interaction):
            cog = interaction.client.get_cog("ClaimRole")
            if cog and hasattr(cog, "handle_role_toggle"):
                await cog.handle_role_toggle(interaction, role_id)
            else:
                await interaction.response.send_message("❌ Role claim service is currently unavailable.", ephemeral=True)
        return _callback


class ClaimRole(commands.Cog):
    """Interactive button role claim system with anti-abuse protection and webhook styling."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=98127391823719, force_registration=True)

        default_guild = {
            "panels": {},  # message_id_str -> panel_dict
            "cooldown_seconds": 2.5,
        }
        self.config.register_guild(**default_guild)

        # Anti-abuse tracking: (user_id, guild_id) -> timestamp
        self._user_cooldowns: Dict[Tuple[int, int], float] = {}
        # Concurrency protection: user_id -> asyncio.Lock
        self._user_locks: Dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        """Register persistent views and dynamic items on cog load."""
        try:
            self.bot.add_dynamic_items(ClaimRoleDynamicButton)
            logger.info("ClaimRoleDynamicButton registered with bot.")
        except Exception as e:
            logger.warning("Could not register DynamicItem: %s", e)

        # Re-register stored views for all guild panels
        try:
            all_guilds = await self.config.all_guilds()
            count = 0
            for guild_id, gdata in all_guilds.items():
                guild = self.bot.get_guild(int(guild_id))
                panels = gdata.get("panels", {})
                for msg_id_str, pdata in panels.items():
                    buttons_data = pdata.get("buttons", [])
                    if buttons_data:
                        view = ClaimRoleView(buttons_data, guild=guild)
                        self.bot.add_view(view, message_id=int(msg_id_str))
                        count += 1
            logger.info("Re-registered %d ClaimRole panel views across servers.", count)
        except Exception as err:
            logger.warning("Error re-registering ClaimRole views: %s", err)

    def get_user_lock(self, user_id: int) -> asyncio.Lock:
        """Return or create a mutex lock for a user to prevent race conditions."""
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    async def handle_role_toggle(self, interaction: discord.Interaction, role_id: int) -> None:
        """Core anti-abuse role claim logic executed when a button is clicked."""
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ This button can only be used in a server.", ephemeral=True)
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            member = guild.get_member(member.id)
            if not member:
                await interaction.response.send_message("❌ Could not resolve your server member profile.", ephemeral=True)
                return

        # 1. Anti-abuse rate limiting check
        cooldown_setting = await self.config.guild(guild).cooldown_seconds()
        user_key = (member.id, guild.id)
        now = time.monotonic()
        last_click = self._user_cooldowns.get(user_key, 0.0)
        elapsed = now - last_click

        if elapsed < cooldown_setting:
            remaining = cooldown_setting - elapsed
            await interaction.response.send_message(
                f"⏳ Please slow down! You can click again in **{remaining:.1f}s**.",
                ephemeral=True,
            )
            return

        self._user_cooldowns[user_key] = now

        # 2. Acquire user lock to prevent concurrent double-click races
        lock = self.get_user_lock(member.id)
        async with lock:
            role = guild.get_role(role_id)
            if not role:
                await interaction.response.send_message("❌ This role no longer exists in this server.", ephemeral=True)
                return

            # Check bot permissions
            if not guild.me.guild_permissions.manage_roles:
                await interaction.response.send_message(
                    "⚠️ I do not have the **Manage Roles** permission in this server.",
                    ephemeral=True,
                )
                return

            # Check hierarchy safety
            bot_pos = getattr(guild.me.top_role, "position", 0)
            role_pos = getattr(role, "position", 0)
            if bot_pos <= role_pos:
                await interaction.response.send_message(
                    f"⚠️ I cannot assign **{role.name}** because it is higher than or equal to my highest role ({guild.me.top_role.name}).\n"
                    "Please ask a server administrator to move my bot role higher in Server Settings > Roles.",
                    ephemeral=True,
                )
                return

            try:
                if role in member.roles:
                    await member.remove_roles(role, reason=f"ClaimRole button: toggled off by {member}")
                    await interaction.response.send_message(
                        f"❌ Removed the **{role.name}** role from you.",
                        ephemeral=True,
                    )
                else:
                    await member.add_roles(role, reason=f"ClaimRole button: toggled on by {member}")
                    await interaction.response.send_message(
                        f"✅ You have been given the **{role.name}** role!",
                        ephemeral=True,
                    )
            except discord.Forbidden:
                await interaction.response.send_message(
                    f"⚠️ Discord blocked this action. Please check that my role is above **{role.name}** and that I have permission to manage members.",
                    ephemeral=True,
                )
            except Exception as err:
                logger.error("Error toggling role %d for user %d: %s", role_id, member.id, err)
                await interaction.response.send_message("⚠️ An unexpected error occurred while toggling your role.", ephemeral=True)

    @commands.group(name="claimrole", aliases=["buttonrole", "cr"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def claimrole_group(self, ctx: commands.Context) -> None:
        """Manage button role claim panels with anti-abuse protection."""
        pass

    @claimrole_group.command(name="post", aliases=["create", "send"])
    async def claimrole_post(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        role: discord.Role,
        color: Optional[str] = "green",
        emoji: Optional[str] = None,
        *,
        label: Optional[str] = None,
    ) -> None:
        """
        Post a quick single-role claim panel in a channel.

        Parameters:
        - channel: Target text channel
        - role: The role to give or remove
        - color: Button color (green, blurple, red, gray)
        - emoji: Emoji for the button (or 'none')
        - label: Button text label (defaults to role name)
        """
        btn_label = label or role.name
        btn_style = parse_button_style(color)
        btn_emoji = parse_emoji_string(ctx.guild, emoji)

        embed = discord.Embed(
            title=f"🎭 Role Claim: {role.name}",
            description=f"Click the button below to claim or remove the **{role.mention}** role.",
            color=role.color if role.color.value else discord.Color.blurple(),
        )
        embed.set_footer(text="Click once to claim • Click again to remove")

        button_data = {
            "role_id": role.id,
            "label": btn_label,
            "style": color or "green",
            "emoji": emoji if emoji and emoji.lower() != "none" else None,
        }

        view = ClaimRoleView([button_data], guild=ctx.guild)
        msg = await channel.send(embed=embed, view=view)

        # Save to config
        async with self.config.guild(ctx.guild).panels() as panels:
            panels[str(msg.id)] = {
                "channel_id": channel.id,
                "message_id": msg.id,
                "title": embed.title,
                "buttons": [button_data],
            }

        await ctx.send(f"✅ Role claim panel posted in {channel.mention} (Message ID: `{msg.id}`).")

    @claimrole_group.command(name="panel")
    async def claimrole_panel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        title: str,
        *,
        description: str,
    ) -> None:
        """
        Create a multi-button role claim panel embed.
        Use '[p]claimrole addbutton' to add buttons to it!
        """
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.dark_theme(),
        )
        embed.set_footer(text="Click buttons below to claim or remove roles")
        msg = await channel.send(embed=embed)

        async with self.config.guild(ctx.guild).panels() as panels:
            panels[str(msg.id)] = {
                "channel_id": channel.id,
                "message_id": msg.id,
                "title": title,
                "buttons": [],
            }

        await ctx.send(
            f"✅ Panel created in {channel.mention} (Message ID: `{msg.id}`).\n"
            f"Add buttons to it using: `{ctx.clean_prefix}claimrole addbutton {channel.mention} {msg.id} @Role [color] [emoji] [label]`"
        )

    @claimrole_group.command(name="addbutton", aliases=["add"])
    async def claimrole_addbutton(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        message_id: int,
        role: discord.Role,
        color: Optional[str] = "secondary",
        emoji: Optional[str] = None,
        *,
        label: Optional[str] = None,
    ) -> None:
        """
        Add a role button to an existing panel or bot message.

        Parameters:
        - channel: The channel where the message is located
        - message_id: The ID of the target message
        - role: The role to attach
        - color: Button color (green, blurple, red, gray)
        - emoji: Emoji to display on the button (or 'none')
        - label: Button text (defaults to role name)
        """
        try:
            target_msg = await channel.fetch_message(message_id)
        except discord.NotFound:
            await ctx.send("❌ Message not found. Please verify the channel and message ID.")
            return
        except discord.Forbidden:
            await ctx.send("❌ I do not have permission to view messages in that channel.")
            return

        if target_msg.author.id != self.bot.user.id and not target_msg.webhook_id:
            await ctx.send("❌ I can only attach buttons to messages sent by me or my webhooks.")
            return

        async with self.config.guild(ctx.guild).panels() as panels:
            pdata = panels.get(str(message_id), {
                "channel_id": channel.id,
                "message_id": message_id,
                "title": target_msg.embeds[0].title if target_msg.embeds else "Role Claim",
                "buttons": [],
            })

            existing_buttons = pdata.get("buttons", [])
            if len(existing_buttons) >= 25:
                await ctx.send("❌ Discord limits messages to a maximum of 25 buttons (5 rows of 5).")
                return

            # Check if role already on this panel
            for b in existing_buttons:
                if b["role_id"] == role.id:
                    await ctx.send(f"⚠️ A button for **{role.name}** already exists on this panel.")
                    return

            new_button = {
                "role_id": role.id,
                "label": label or role.name,
                "style": color or "secondary",
                "emoji": emoji if emoji and emoji.lower() != "none" else None,
            }
            existing_buttons.append(new_button)
            pdata["buttons"] = existing_buttons
            panels[str(message_id)] = pdata

        view = ClaimRoleView(existing_buttons, guild=ctx.guild)
        if target_msg.webhook_id:
            edited = False
            try:
                webhooks = await channel.webhooks()
                for wh in webhooks:
                    if wh.id == target_msg.webhook_id:
                        await wh.edit_message(message_id, view=view)
                        edited = True
                        break
            except Exception:
                pass
            if not edited:
                await target_msg.edit(view=view)
        else:
            await target_msg.edit(view=view)
        self.bot.add_view(view, message_id=message_id)

        await ctx.send(f"✅ Added button for **{role.name}** ({color}) to message `{message_id}`.")

    @claimrole_group.command(name="removebutton", aliases=["delbutton"])
    async def claimrole_removebutton(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        message_id: int,
        role: discord.Role,
    ) -> None:
        """Remove a role button from an existing panel."""
        try:
            target_msg = await channel.fetch_message(message_id)
        except Exception:
            target_msg = None

        async with self.config.guild(ctx.guild).panels() as panels:
            pdata = panels.get(str(message_id))
            if not pdata:
                await ctx.send("❌ No configured panel found for that message ID.")
                return

            existing = pdata.get("buttons", [])
            updated = [b for b in existing if b["role_id"] != role.id]
            if len(existing) == len(updated):
                await ctx.send(f"❌ No button for **{role.name}** was found on that panel.")
                return

            pdata["buttons"] = updated
            panels[str(message_id)] = pdata

        if target_msg:
            view = ClaimRoleView(updated, guild=ctx.guild) if updated else None
            if target_msg.webhook_id:
                edited = False
                try:
                    webhooks = await channel.webhooks()
                    for wh in webhooks:
                        if wh.id == target_msg.webhook_id:
                            await wh.edit_message(message_id, view=view)
                            edited = True
                            break
                except Exception:
                    pass
                if not edited:
                    await target_msg.edit(view=view)
            else:
                await target_msg.edit(view=view)

        await ctx.send(f"✅ Removed button for **{role.name}** from message `{message_id}`.")

    @claimrole_group.command(name="webhook")
    async def claimrole_webhook(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        webhook_name: str,
        avatar_url: str,
        role: discord.Role,
        color: Optional[str] = "green",
        emoji: Optional[str] = None,
        *,
        label: Optional[str] = None,
    ) -> None:
        """
        Post a role claim button using a custom Webhook name and avatar!

        Parameters:
        - channel: Target channel
        - webhook_name: Custom display name (e.g. "Role Selector")
        - avatar_url: Custom image URL for the avatar
        - role: The role to claim
        - color: Button color (green, blurple, red, gray)
        - emoji: Emoji for the button
        - label: Text label for the button
        """
        if not channel.permissions_for(ctx.guild.me).manage_webhooks:
            await ctx.send("❌ I need the **Manage Webhooks** permission in that channel to create custom webhooks.")
            return

        btn_label = label or role.name
        btn_style = parse_button_style(color)
        btn_emoji = parse_emoji_string(ctx.guild, emoji)

        button_data = {
            "role_id": role.id,
            "label": btn_label,
            "style": color or "green",
            "emoji": emoji if emoji and emoji.lower() != "none" else None,
        }

        view = ClaimRoleView([button_data], guild=ctx.guild)

        embed = discord.Embed(
            title=f"🎭 {webhook_name}: {role.name}",
            description=f"Click the button below to claim or remove the **{role.mention}** role.",
            color=role.color if role.color.value else discord.Color.teal(),
        )
        embed.set_footer(text="Click once to claim • Click again to remove")

        # Find or create a webhook
        webhooks = await channel.webhooks()
        webhook = None
        for wh in webhooks:
            if wh.user and wh.user.id == self.bot.user.id:
                webhook = wh
                break

        if not webhook:
            webhook = await channel.create_webhook(name=webhook_name, reason="ClaimRole custom panel")

        clean_avatar = avatar_url if avatar_url.startswith(("http://", "https://")) else None

        msg = await webhook.send(
            username=webhook_name,
            avatar_url=clean_avatar,
            embed=embed,
            view=view,
            wait=True,
        )

        async with self.config.guild(ctx.guild).panels() as panels:
            panels[str(msg.id)] = {
                "channel_id": channel.id,
                "message_id": msg.id,
                "title": embed.title,
                "buttons": [button_data],
            }

        await ctx.send(
            f"✅ Webhook role panel posted in {channel.mention} as **{webhook_name}**!\n"
            f"Message ID: `{msg.id}`"
        )

    @claimrole_group.command(name="fromjson", aliases=["discohook", "import"])
    async def claimrole_fromjson(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        *,
        json_input: Optional[str] = None,
    ) -> None:
        """
        Import and post a message designed on Discohook (discohook.org).

        You can paste the Discohook JSON directly or attach a .json file!
        Once posted, use '[p]claimrole addbutton' to attach role claim buttons to it.
        """
        import json

        raw_json = json_input or ""
        if ctx.message.attachments:
            att = ctx.message.attachments[0]
            if att.filename.lower().endswith(".json") or att.content_type in ("application/json", "text/plain"):
                try:
                    file_bytes = await att.read()
                    raw_json = file_bytes.decode("utf-8")
                except Exception as e:
                    await ctx.send(f"❌ Failed to read attached file: {e}")
                    return

        if not raw_json.strip():
            await ctx.send(
                "❌ Please provide Discohook JSON! Paste it in the command or attach a `.json` file.\n"
                "Tip: In Discohook (discohook.org), click **Copy JSON** at the bottom of the page."
            )
            return

        clean_json = raw_json.strip()
        codeblock_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_json)
        if codeblock_match:
            clean_json = codeblock_match.group(1).strip()

        try:
            data = json.loads(clean_json)
        except json.JSONDecodeError as err:
            await ctx.send(f"❌ Invalid JSON format: `{err}`. Please verify your Discohook payload.")
            return

        content = data.get("content")
        embed_dicts = data.get("embeds", [])
        username = data.get("username")
        avatar_url = data.get("avatar_url")

        embeds = []
        for ed in embed_dicts[:10]:
            try:
                embeds.append(discord.Embed.from_dict(ed))
            except Exception as e:
                logger.warning("Could not parse embed from JSON: %s", e)

        if not content and not embeds:
            await ctx.send("❌ The provided JSON has no text content or embeds to post.")
            return

        use_webhook = bool(username or avatar_url)
        msg = None

        if use_webhook and channel.permissions_for(ctx.guild.me).manage_webhooks:
            try:
                webhooks = await channel.webhooks()
                webhook = None
                for wh in webhooks:
                    if wh.user and wh.user.id == self.bot.user.id:
                        webhook = wh
                        break
                if not webhook:
                    wh_name = username or "Role Panel"
                    webhook = await channel.create_webhook(name=wh_name, reason="ClaimRole Discohook Import")

                msg = await webhook.send(
                    content=content,
                    embeds=embeds,
                    username=username or "Role Panel",
                    avatar_url=avatar_url,
                    wait=True,
                )
            except Exception as wh_err:
                logger.warning("Webhook send failed, falling back to standard bot send: %s", wh_err)
                use_webhook = False

        if not msg:
            try:
                msg = await channel.send(content=content, embeds=embeds)
            except discord.Forbidden:
                await ctx.send("❌ I do not have permission to send messages or embeds in that channel.")
                return

        async with self.config.guild(ctx.guild).panels() as panels:
            panels[str(msg.id)] = {
                "channel_id": channel.id,
                "message_id": msg.id,
                "title": embeds[0].title if embeds else "Discohook Panel",
                "buttons": [],
            }

        await ctx.send(
            f"✅ **Discohook layout posted successfully in {channel.mention}!**\n"
            f"Message ID: `{msg.id}`\n\n"
            f"**To add role buttons, run:**\n"
            f"`{ctx.clean_prefix}claimrole addbutton {channel.mention} {msg.id} @Role [color] [emoji] [label]`"
        )

    @claimrole_group.command(name="langpanel", aliases=["languages", "lang"])
    async def claimrole_langpanel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        banner_url: Optional[str] = None,
    ) -> None:
        """
        Deploy the complete language role claim panel with banner and flag buttons in one command!

        Parameters:
        - channel: Target channel to post the panel
        - banner_url: Optional custom banner image URL
        """
        default_banner = "https://cdn.discordapp.com/attachments/1455330274232500461/1553860826816057485/qq173kj.png?ex=6abac92a&is=6ab977aa&hm=68a42cf995abc0623b7d4743b2b71d9463e95e8540e37227aed8294873addaf7&"
        banner = banner_url or default_banner

        lang_specs = [
            {"name": "English Speaker", "label": "English", "emoji": "🇺🇸", "style": "blurple"},
            {"name": "PT BR Speaker", "label": "Português", "emoji": "🇧🇷", "style": "green"},
            {"name": "Tagalog Speaker", "label": "Tagalog", "emoji": "🇵🇭", "style": "blurple"},
            {"name": "Hindi Speaker", "label": "Hindi", "emoji": "🇮🇳", "style": "green"},
            {"name": "Indonesian Speaker", "label": "Indonesian", "emoji": "🇮🇩", "style": "red"},
            {"name": "Arabic Speaker", "label": "Arabic", "emoji": "🇸🇦", "style": "green"},
            {"name": "French Speaker", "label": "Français", "emoji": "🇫🇷", "style": "blurple"},
        ]

        buttons_data = []
        found_roles = []
        missing_roles = []

        for spec in lang_specs:
            matched = None
            for r in ctx.guild.roles:
                if r.name.lower() == spec["name"].lower():
                    matched = r
                    break
            if matched:
                buttons_data.append({
                    "role_id": matched.id,
                    "label": spec["label"],
                    "style": spec["style"],
                    "emoji": spec["emoji"],
                })
                found_roles.append(matched)
            else:
                missing_roles.append(spec["name"])

        if not buttons_data:
            roles_list = ", ".join([f"`{s['name']}`" for s in lang_specs])
            await ctx.send(
                f"❌ None of the language roles were found in this server!\n"
                f"Please create the roles first in Server Settings > Roles:\n{roles_list}"
            )
            return

        embed = discord.Embed(
            title="🌐 Select Your Language Roles",
            description=(
                "Choose your native or preferred languages to unlock international chat channels!\n\n"
                "• 🇺🇸 **English**: English Speaker\n"
                "• 🇧🇷 **Português**: PT BR Speaker\n"
                "• 🇵🇭 **Tagalog**: Tagalog Speaker\n"
                "• 🇮🇳 **Hindi**: Hindi Speaker\n"
                "• 🇮🇩 **Indonesian**: Indonesian Speaker\n"
                "• 🇸🇦 **Arabic**: Arabic Speaker\n"
                "• 🇫🇷 **Français**: French Speaker\n\n"
                "*Click once to claim a role. Click again anytime to remove it.*"
            ),
            color=discord.Color.dark_theme(),
        )
        if banner and banner.startswith(("http://", "https://")):
            embed.set_image(url=banner)
        embed.set_footer(text="Click buttons below to toggle roles • 2.5s anti-spam protection")

        view = ClaimRoleView(buttons_data, guild=ctx.guild)
        msg = await channel.send(embed=embed, view=view)
        self.bot.add_view(view, message_id=msg.id)

        async with self.config.guild(ctx.guild).panels() as panels:
            panels[str(msg.id)] = {
                "channel_id": channel.id,
                "message_id": msg.id,
                "title": embed.title,
                "buttons": buttons_data,
            }

        response_txt = f"✅ **Language role panel deployed in {channel.mention}!** (Message ID: `{msg.id}`)\n"
        response_txt += f"Added {len(buttons_data)} buttons for: " + ", ".join([r.mention for r in found_roles])
        if missing_roles:
            response_txt += f"\n⚠️ Missing roles not found in server: " + ", ".join([f"`{m}`" for m in missing_roles])
        await ctx.send(response_txt)

    @claimrole_group.command(name="cooldown")
    async def claimrole_cooldown(self, ctx: commands.Context, seconds: float) -> None:
        """
        Set anti-abuse button spam cooldown per user (in seconds).
        Default is 2.5 seconds. Recommended: 1.5 to 5.0 seconds.
        """
        if seconds < 0.5 or seconds > 30.0:
            await ctx.send("❌ Cooldown must be between 0.5 and 30 seconds.")
            return

        await self.config.guild(ctx.guild).cooldown_seconds.set(seconds)
        await ctx.send(f"✅ Anti-abuse button cooldown set to **{seconds:.1f}s**.")

    @claimrole_group.command(name="list")
    async def claimrole_list(self, ctx: commands.Context) -> None:
        """List all active role claim panels configured in this server."""
        panels = await self.config.guild(ctx.guild).panels()
        if not panels:
            await ctx.send("ℹ️ No role claim panels are currently configured in this server.")
            return

        embed = discord.Embed(
            title=f"📋 Active Role Claim Panels ({len(panels)})",
            color=discord.Color.blurple(),
        )

        for msg_id_str, pdata in list(panels.items())[:20]:
            chan = ctx.guild.get_channel(pdata.get("channel_id", 0))
            chan_name = f"#{chan.name}" if chan else "Unknown Channel"
            btns = pdata.get("buttons", [])
            roles_txt = ", ".join([f"<@&{b['role_id']}>" for b in btns]) if btns else "No buttons yet"
            embed.add_field(
                name=f"Panel: {pdata.get('title', 'Untitled')} (`{msg_id_str}`)",
                value=f"**Channel**: {chan_name}\n**Roles**: {roles_txt}",
                inline=False,
            )

        await ctx.send(embed=embed)
