"""
TipModal Cog for Red-DiscordBot.
Provides an interactive button panel and Discord modal for streamlined tip verification.
"""

import logging
from typing import Optional

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

    extra_info = discord.ui.TextInput(
        label="Extra Info / Gifting User ID (Optional)",
        placeholder="Gift recipient UserID, account change details, or type 'None'",
        default="None",
        max_length=500,
        style=discord.TextStyle.paragraph,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle modal submission by posting a public ticket embed and ephemeral receipt."""
        submitter = interaction.user
        game_name = self.game.value.strip()
        tip_method = self.method.value.strip()
        proof_details = self.proof_id.value.strip()
        extra = self.extra_info.value.strip() or "None"

        # Ephemeral confirmation receipt for the user
        await interaction.response.send_message(
            f"✅ **Tip details submitted successfully!**\n"
            f"Your request for **{game_name}** has been recorded.\n"
            f"Please attach your screenshot proof in this channel now.",
            ephemeral=True,
        )

        # Public verification ticket embed sent into the channel
        embed = discord.Embed(
            title="🧾 Tip Verification Submission",
            color=discord.Color.green(),
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

        embed.add_field(
            name="📌 Next Step: Upload Screenshot Proof",
            value=(
                "Please upload **1 screenshot** in this channel showing your payment proof:\n"
                "• **PayPal 1-Time Tip**: Note + game name (censor personal info)\n"
                "• **PayPal Pub Pass**: Profile ID starting with `I-xxxxxxx`\n"
                "• **Patreon**: Display name matching your active subscription\n"
                "• **Steam**: Confirmation from Azam Direct Messages"
            ),
            inline=False,
        )
        embed.set_footer(text=f"User ID: {submitter.id} • TipModal Verification System")

        channel = interaction.channel
        if channel:
            await channel.send(embed=embed, view=SubmissionGuidanceView())


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
                "Simply drag and drop or upload your image directly to this channel!"
            ),
        )
        await interaction.response.send_message(embed=guidance_embed, ephemeral=True)


class TipButtonView(discord.ui.View):
    """Persistent button view that opens the Tip Verification Modal."""

    def __init__(self):
        super().__init__(timeout=None)

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
        modal = TipSubmissionModal()
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
    """Interactive modal and button showcase for tip verifications."""

    def __init__(self, bot: Red):
        self.bot = bot

    async def cog_load(self) -> None:
        """Register persistent views on startup so buttons survive restarts."""
        self.bot.add_view(TipButtonView())
        self.bot.add_view(SubmissionGuidanceView())
        logger.info("TipModal persistent views registered.")

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
                "Click **Fill Tip Verification Form** below to start. Once submitted, upload your verification screenshot in this channel."
            ),
        )
        embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild and ctx.guild.icon else None)
        embed.set_footer(text="TipModal Verification • Click below to open form")

        view = TipButtonView()
        await ctx.send(embed=embed, view=view)
