"""
TipModal Cog for Red-DiscordBot.
Provides an interactive button panel, Discord modal, and automated screenshot binding
supporting both channel uploads and private DM screenshot uploads.
"""

import logging
import time
from typing import Dict, Optional

import discord
from redbot.core import commands
from redbot.core.bot import Red

logger = logging.getLogger("red.sablinova.tipmodal")


class TipSubmissionModal(discord.ui.Modal, title="Tip & Pass Verification"):
    """Discord popup modal collecting tip details in a single form."""

    method = discord.ui.TextInput(
        label="Tip Method & Pass Tier",
        placeholder="e.g. 1 Time Tip, PayPal Pass (Gold), Patreon (Silver), Steam",
        default="1 Time Tip",
        min_length=3,
        max_length=64,
        style=discord.TextStyle.short,
        required=True,
    )

    game = discord.ui.TextInput(
        label="Requested Game Name",
        placeholder="e.g. Crimson Desert",
        min_length=2,
        max_length=100,
        style=discord.TextStyle.short,
        required=True,
    )

    proof_id = discord.ui.TextInput(
        label="Profile ID / Note / Patreon Display Name",
        placeholder="e.g. PayPal Profile ID (I-xxxxxxx), Note, or Patreon Name",
        min_length=2,
        max_length=150,
        style=discord.TextStyle.short,
        required=True,
    )

    screenshot_link = discord.ui.TextInput(
        label="Screenshot Link (Optional)",
        placeholder="Paste image link, or upload your image in DM / channel after submitting",
        max_length=300,
        style=discord.TextStyle.short,
        required=False,
    )

    extra_info = discord.ui.TextInput(
        label="Extra Info / Gifting User ID (Optional)",
        placeholder="Gift recipient UserID, account change details, or type 'None'",
        default="None",
        max_length=500,
        style=discord.TextStyle.paragraph,
        required=False,
    )

    def __init__(self, cog: "TipModal"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission by posting a public ticket embed and notifying user in DM."""
        submitter = interaction.user
        game_name = self.game.value.strip()
        tip_method = self.method.value.strip()
        proof_details = self.proof_id.value.strip()
        image_url = self.screenshot_link.value.strip() if self.screenshot_link.value else ""
        extra = self.extra_info.value.strip() or "None"

        has_image = bool(image_url and image_url.startswith(("http://", "https://")))

        # Public verification ticket embed sent into the channel
        embed = discord.Embed(
            title="🧾 Tip Verification Submission",
            color=discord.Color.green() if has_image else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(
            name=f"{submitter.display_name} ({submitter})",
            icon_url=submitter.display_avatar.url,
        )
        embed.add_field(name="👤 Submitter", value=f"{submitter.mention} (`{submitter.id}`)", inline=True)
        embed.add_field(name="💳 Tip Method", value=tip_method, inline=True)
        embed.add_field(name="🎮 Requested Game", value=game_name, inline=True)
        embed.add_field(name="🔑 Verification ID / Note", value=f"`{proof_details}`", inline=False)

        if extra and extra.lower() != "none":
            embed.add_field(name="📝 Extra Info / Gifting", value=extra, inline=False)
        else:
            embed.add_field(name="📝 Extra Info / Gifting", value="*None specified*", inline=False)

        if has_image:
            embed.set_image(url=image_url)
            embed.add_field(name="📊 Status", value="🟢 **Ready for Staff Review**", inline=False)
            view: Optional[discord.ui.View] = StaffReviewView()
        else:
            embed.add_field(
                name="📊 Status",
                value="⏳ **Awaiting Screenshot Proof** (Send in DM or upload below)",
                inline=False,
            )
            embed.add_field(
                name="📌 Next Step: Upload Screenshot Proof",
                value=(
                    "Please provide **1 screenshot** of your payment proof:\n"
                    "• **Private DM**: Reply with your screenshot directly to the bot in DMs\n"
                    "• **Channel**: Or upload the screenshot directly in this channel\n"
                    "• **Items needed**: Profile ID `I-xxxxxxx`, PayPal note, or Patreon name"
                ),
                inline=False,
            )
            view = SubmissionGuidanceView()

        embed.set_footer(text=f"User ID: {submitter.id} • TipModal Verification System")

        channel = interaction.channel
        ticket_msg = None
        if channel:
            ticket_msg = await channel.send(embed=embed, view=view)

        # Attempt to DM the user for private, secure screenshot submission
        dm_sent = False
        if not has_image and self.cog and ticket_msg:
            self.cog.pending_submissions[submitter.id] = {
                "message_id": ticket_msg.id,
                "channel_id": channel.id,
                "created_at": time.time(),
                "game_name": game_name,
            }

            try:
                chan_name = getattr(channel, "name", "verification-channel")
                dm_embed = discord.Embed(
                    title="🔒 Private Screenshot Upload for Tip Verification",
                    color=discord.Color.blue(),
                    description=(
                        f"Hey {submitter.display_name}! Your tip submission for **{game_name}** has been posted in #{chan_name}.\n\n"
                        "To protect your privacy and sensitive payment data, **reply to this DM with your screenshot image**.\n"
                        "The bot will automatically attach it to your verification card in the server!"
                    ),
                )
                dm_embed.add_field(
                    name="Guidelines",
                    value=(
                        "• Keep visible: Profile ID (`I-xxxxxxx`), Discord username, game name\n"
                        "• Censor / black out: Real names, email addresses, bank card numbers"
                    ),
                    inline=False,
                )
                dm_embed.set_footer(text="Reply to this DM with an image attachment to attach proof.")
                await submitter.send(embed=dm_embed)
                dm_sent = True
            except discord.Forbidden:
                dm_sent = False
            except Exception as e:
                logger.warning("Could not send DM to user %d: %s", submitter.id, e)
                dm_sent = False

        # Ephemeral confirmation receipt in the server channel
        if has_image:
            await interaction.response.send_message(
                f"✅ **Tip details submitted successfully!**\n"
                f"Your request for **{game_name}** with screenshot link has been recorded.",
                ephemeral=True,
            )
        elif dm_sent:
            await interaction.response.send_message(
                f"✅ **Tip details submitted successfully!**\n"
                f"📬 **Check your DMs**: I've sent you a Direct Message so you can privately and securely upload your screenshot proof.\n"
                f"(Alternatively, you can also drop your image directly in this channel).",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"✅ **Tip details submitted successfully!**\n"
                f"Your request for **{game_name}** has been recorded.\n"
                f"📸 **Next Step**: Drop or upload your screenshot proof in this channel below (your DMs appear closed).",
                ephemeral=True,
            )


class SubmissionGuidanceView(discord.ui.View):
    """Persistent helper view attached to submitted verification cards."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Screenshot Requirements",
        style=discord.ButtonStyle.secondary,
        emoji="📸",
        custom_id="tipmodal:screenshot_help",
    )
    async def screenshot_help_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Provide clear screenshot and censorship guidelines ephemerally."""
        guidance_embed = discord.Embed(
            title="📸 Screenshot Upload Guidelines",
            color=discord.Color.gold(),
            description=(
                "To protect your privacy and ensure swift verification:\n\n"
                "1. **Censor Personal Info**: Black out real names, email addresses, and bank card digits.\n"
                "2. **PayPal 1-Time Tip**: Keep the Discord username and requested game note visible.\n"
                "3. **PayPal Pub Pass**: Profile ID (`I-xxxxxxx`) must be visible (not transaction or invoice ID).\n"
                "4. **Patreon**: Show your Patreon account display name.\n"
                "5. **Steam**: Include screenshot of confirmation message from Azam DM.\n\n"
                "🔒 **Private Option**: You can send your image in a Direct Message to the bot!"
            ),
        )
        await interaction.response.send_message(embed=guidance_embed, ephemeral=True)


class StaffReviewView(discord.ui.View):
    """Persistent staff review view for approving or requesting changes on a ticket."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="tipmodal:staff_approve",
    )
    async def approve_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Allow staff to approve the submitted tip."""
        perms = interaction.user.guild_permissions if interaction.guild else None
        is_staff = perms and (perms.manage_messages or perms.administrator)
        if not is_staff:
            await interaction.response.send_message("❌ Only staff members can review submissions.", ephemeral=True)
            return

        msg = interaction.message
        if msg and msg.embeds:
            embed = msg.embeds[0]
            embed.color = discord.Color.green()
            new_fields = []
            for f in embed.fields:
                if f.name == "📊 Status":
                    new_fields.append(("📊 Status", f"✅ **Approved by {interaction.user.mention}**", False))
                else:
                    new_fields.append((f.name, f.value, f.inline))
            embed.clear_fields()
            for name, val, inline in new_fields:
                embed.add_field(name=name, value=val, inline=inline)

            for child in self.children:
                child.disabled = True

            await interaction.response.edit_message(embed=embed, view=self)
            await interaction.followup.send(f"✅ Submission approved by {interaction.user.mention}!", ephemeral=False)

    @discord.ui.button(
        label="Request Resubmit",
        style=discord.ButtonStyle.danger,
        emoji="⚠️",
        custom_id="tipmodal:staff_resubmit",
    )
    async def resubmit_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Allow staff to request a resubmission if proof is missing or invalid."""
        perms = interaction.user.guild_permissions if interaction.guild else None
        is_staff = perms and (perms.manage_messages or perms.administrator)
        if not is_staff:
            await interaction.response.send_message("❌ Only staff members can review submissions.", ephemeral=True)
            return

        msg = interaction.message
        if msg and msg.embeds:
            embed = msg.embeds[0]
            embed.color = discord.Color.red()
            new_fields = []
            for f in embed.fields:
                if f.name == "📊 Status":
                    new_fields.append(("📊 Status", f"⚠️ **Resubmission Requested by {interaction.user.mention}**", False))
                else:
                    new_fields.append((f.name, f.value, f.inline))
            embed.clear_fields()
            for name, val, inline in new_fields:
                embed.add_field(name=name, value=val, inline=inline)

            for child in self.children:
                child.disabled = True

            await interaction.response.edit_message(embed=embed, view=self)
            await interaction.followup.send(
                f"⚠️ {interaction.user.mention} requested a resubmission. Please check your details and proof screenshot.",
                ephemeral=False,
            )


class TipButtonView(discord.ui.View):
    """Persistent button view that opens the Tip Verification Modal."""

    def __init__(self, cog: Optional["TipModal"] = None):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Fill Tip Verification Form",
        style=discord.ButtonStyle.success,
        emoji="📝",
        custom_id="tipmodal:open_form",
    )
    async def open_modal_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Open the interactive tip modal when clicked."""
        modal = TipSubmissionModal(cog=self.cog)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="View Instructions",
        style=discord.ButtonStyle.secondary,
        emoji="ℹ️",
        custom_id="tipmodal:view_instructions",
    )
    async def instructions_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Show tip method guidelines ephemerally."""
        info_embed = discord.Embed(
            title="📋 Tip & Pass Verification Instructions",
            color=discord.Color.blurple(),
            description=(
                "**1. Patreon**:\n"
                "Include your Patreon display name and pass type (*Bronze, Silver, Gold*).\n"
                "Ensure your Discord account is linked to your Patreon.\n\n"
                "**2. PayPal 1-Time Tip**:\n"
                "Include your Discord username and requested game in the PayPal comment or note field.\n\n"
                "**3. PayPal Pub Pass**:\n"
                "Include your Profile ID found in your email receipt (starts with `I-xxxxxxx`) and pass type (*Bronze, Silver, Gold*).\n\n"
                "**4. Steam Wallet Code**:\n"
                "Send code directly to Azam in Direct Messages and wait for confirmation.\n\n"
                "**5. Steam Trade**:\n"
                "Include your Discord username in the trade offer message."
            ),
        )
        await interaction.response.send_message(embed=info_embed, ephemeral=True)


class TipModal(commands.Cog):
    """Interactive modal, button, and screenshot workflow for tip verifications."""

    def __init__(self, bot: Red):
        self.bot = bot
        # Mapping: user_id -> {"message_id": int, "channel_id": int, "created_at": float, "game_name": str}
        self.pending_submissions: Dict[int, dict] = {}

    async def cog_load(self) -> None:
        """Register persistent views on startup so buttons survive restarts."""
        self.bot.add_view(TipButtonView(cog=self))
        self.bot.add_view(SubmissionGuidanceView())
        self.bot.add_view(StaffReviewView())
        logger.info("TipModal persistent views registered.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Automatically detect when a user with a pending submission uploads a screenshot (in channel or in DM)."""
        if message.author.bot:
            return

        user_id = message.author.id
        pending = self.pending_submissions.get(user_id)
        if not pending:
            return

        is_dm = message.guild is None

        # If sent in a guild, make sure it's the right channel
        if not is_dm and message.channel.id != pending.get("channel_id"):
            return

        # Expire pending submissions older than 30 minutes
        if time.time() - pending.get("created_at", 0) > 1800:
            self.pending_submissions.pop(user_id, None)
            return

        # Check for image attachments
        image_attachments = [
            a for a in message.attachments
            if (a.content_type and a.content_type.startswith("image/"))
            or any(a.filename.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"))
        ]

        if not image_attachments:
            return

        # Consume pending submission
        self.pending_submissions.pop(user_id, None)
        screenshot = image_attachments[0]

        target_channel_id = pending["channel_id"]
        target_message_id = pending["message_id"]

        channel = self.bot.get_channel(target_channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(target_channel_id)
            except Exception as e:
                logger.warning("Could not find ticket channel %d: %s", target_channel_id, e)
                return

        try:
            ticket_msg = await channel.fetch_message(target_message_id)
            if ticket_msg and ticket_msg.embeds:
                embed = ticket_msg.embeds[0]
                embed.set_image(url=screenshot.url)
                embed.color = discord.Color.blue()

                status_text = "🟢 **Ready for Staff Review** (Proof sent privately in DM)" if is_dm else "🟢 **Ready for Staff Review** (Proof attached)"

                new_fields = []
                for f in embed.fields:
                    if f.name == "📊 Status":
                        new_fields.append(("📊 Status", status_text, False))
                    elif f.name.startswith("📌 Next Step"):
                        continue
                    else:
                        new_fields.append((f.name, f.value, f.inline))

                embed.clear_fields()
                for name, val, inline in new_fields:
                    embed.add_field(name=name, value=val, inline=inline)

                staff_view = StaffReviewView()
                await ticket_msg.edit(embed=embed, view=staff_view)

                try:
                    await message.add_reaction("📸")
                except Exception:
                    pass

                if is_dm:
                    chan_mention = f"#{channel.name}" if hasattr(channel, "name") else "the server channel"
                    await message.reply(
                        f"✅ **Screenshot received and attached securely!**\n"
                        f"Your verification card for **{pending.get('game_name', 'game')}** in {chan_mention} has been updated for staff review.",
                    )
                else:
                    await message.reply(
                        f"✅ **Screenshot attached!** Your tip submission for **{pending.get('game_name', 'game')}** is now ready for staff review.",
                        delete_after=15,
                    )
        except Exception as err:
            logger.warning("Failed to auto-bind screenshot to ticket %d: %s", target_message_id, err)

    @commands.hybrid_command(
        name="tipmodal",
        description="Post the tip verification panel with an interactive modal button.",
    )
    async def tipmodal_command(self, ctx: commands.Context) -> None:
        """Post the tip verification panel featuring an interactive modal button."""
        embed = discord.Embed(
            title="💳 Tip & Pass Verification",
            color=discord.Color.dark_purple(),
            description=(
                "Submit your tip and game verification details quickly using the interactive form below!\n\n"
                "**Accepted Tip Methods:**\n"
                "• **Patreon**: Display name and pass tier (*Bronze / Silver / Gold*)\n"
                "• **PayPal 1-Time Tip**: Note with Discord username and requested game\n"
                "• **PayPal Pub Pass**: Profile ID (`I-xxxxxxx`) and pass tier\n"
                "• **Steam Wallet / Trade**: Confirmation from Azam DM or trade offer\n\n"
                "Click **Fill Tip Verification Form** below to start.\n"
                "🔒 You will be able to upload your screenshot privately in DMs with the bot or directly in this channel."
            ),
        )
        embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild and ctx.guild.icon else None)
        embed.set_footer(text="TipModal Verification • Click below to open form")

        view = TipButtonView(cog=self)
        await ctx.send(embed=embed, view=view)
