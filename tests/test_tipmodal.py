"""
Unit tests for the TipModal Cog, interactive UI components, and automated screenshot binding.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest
import discord
from tipmodal.tipmodal import (
    TipModal,
    TipSubmissionModal,
    TipButtonView,
    SubmissionGuidanceView,
    StaffReviewView,
)


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.add_view = MagicMock()
    return bot


@pytest.fixture
def mock_interaction():
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = 426878496468500493
    interaction.user.display_name = "Sablinova"
    interaction.user.mention = "<@426878496468500493>"
    interaction.user.display_avatar.url = "https://cdn.discordapp.com/avatars/426878496468500493/abc.png"

    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()

    channel = MagicMock()
    ticket_msg = MagicMock()
    ticket_msg.id = 9988776655
    channel.send = AsyncMock(return_value=ticket_msg)
    interaction.channel = channel

    return interaction


@pytest.mark.asyncio
async def test_tip_submission_modal_properties(mock_bot):
    cog = TipModal(mock_bot)
    modal = TipSubmissionModal(cog=cog)
    assert modal.title == "Tip & Pass Verification"
    assert modal.method.label == "Tip Method & Pass Tier"
    assert modal.game.label == "Requested Game Name"
    assert modal.proof_id.label == "Profile ID / Note / Patreon Display Name"
    assert modal.screenshot_link.label == "Screenshot Link (Optional)"
    assert modal.extra_info.label == "Extra Info / Gifting User ID (Optional)"
    assert modal.method.required is True
    assert modal.game.required is True
    assert modal.proof_id.required is True
    assert modal.screenshot_link.required is False
    assert modal.extra_info.required is False


@pytest.mark.asyncio
async def test_tip_submission_modal_on_submit_with_image_url(mock_bot, mock_interaction):
    cog = TipModal(mock_bot)
    modal = TipSubmissionModal(cog=cog)
    modal.method._value = "1 Time Tip"
    modal.game._value = "Crimson Desert"
    modal.proof_id._value = "Note: Sablinova / Crimson Desert"
    modal.screenshot_link._value = "https://i.imgur.com/sample_proof.png"
    modal.extra_info._value = "None"

    await modal.on_submit(mock_interaction)

    mock_interaction.response.send_message.assert_awaited_once()
    sent_msg = mock_interaction.response.send_message.call_args[0][0]
    assert "Tip details submitted successfully!" in sent_msg
    assert "screenshot link has been recorded" in sent_msg

    mock_interaction.channel.send.assert_awaited_once()
    kwargs = mock_interaction.channel.send.call_args[1]
    embed = kwargs.get("embed")
    assert embed is not None
    assert embed.image.url == "https://i.imgur.com/sample_proof.png"
    fields = {f.name: f.value for f in embed.fields}
    assert "🟢 **Ready for Staff Review**" in fields["📊 Status"]


@pytest.mark.asyncio
async def test_tip_submission_modal_on_submit_pending_upload(mock_bot, mock_interaction):
    cog = TipModal(mock_bot)
    modal = TipSubmissionModal(cog=cog)
    modal.method._value = "PayPal Pass"
    modal.game._value = "Crimson Desert"
    modal.proof_id._value = "I-12345678"
    modal.screenshot_link._value = ""
    modal.extra_info._value = "None"

    await modal.on_submit(mock_interaction)

    key = (mock_interaction.user.id, mock_interaction.channel.id)
    assert key in cog.pending_submissions
    assert cog.pending_submissions[key]["message_id"] == 9988776655
    assert cog.pending_submissions[key]["game_name"] == "Crimson Desert"


@pytest.mark.asyncio
async def test_on_message_auto_binds_screenshot(mock_bot):
    cog = TipModal(mock_bot)
    user_id = 426878496468500493
    channel_id = 1122334455
    ticket_msg_id = 9988776655

    cog.pending_submissions[(user_id, channel_id)] = {
        "message_id": ticket_msg_id,
        "created_at": 100000000000.0,
        "game_name": "Crimson Desert",
    }

    message = MagicMock()
    message.author.bot = False
    message.author.id = user_id
    message.guild = MagicMock()
    message.channel.id = channel_id
    message.reply = AsyncMock()
    message.add_reaction = AsyncMock()

    attachment = MagicMock()
    attachment.content_type = "image/png"
    attachment.filename = "paypal_proof.png"
    attachment.url = "https://cdn.discordapp.com/attachments/1122334455/9988776655/paypal_proof.png"
    message.attachments = [attachment]

    mock_ticket_msg = MagicMock()
    existing_embed = discord.Embed(title="🧾 Tip Verification Submission")
    existing_embed.add_field(name="📊 Status", value="⏳ **Awaiting Screenshot Proof**", inline=False)
    existing_embed.add_field(name="📌 Next Step: Upload Screenshot Proof", value="Upload below", inline=False)
    mock_ticket_msg.embeds = [existing_embed]
    mock_ticket_msg.edit = AsyncMock()

    message.channel.fetch_message = AsyncMock(return_value=mock_ticket_msg)

    await cog.on_message(message)

    # Verify pending submission was consumed
    assert (user_id, channel_id) not in cog.pending_submissions

    # Verify ticket embed was updated with image URL
    mock_ticket_msg.edit.assert_awaited_once()
    updated_embed = mock_ticket_msg.edit.call_args[1]["embed"]
    assert updated_embed.image.url == attachment.url
    updated_fields = {f.name: f.value for f in updated_embed.fields}
    assert "🟢 **Ready for Staff Review** (Proof attached)" in updated_fields["📊 Status"]
    assert "📌 Next Step: Upload Screenshot Proof" not in updated_fields

    # Verify user received a confirmation reply
    message.reply.assert_awaited_once()
    assert "Screenshot attached!" in message.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_staff_review_view_persistent():
    view = StaffReviewView()
    custom_ids = [item.custom_id for item in view.children if hasattr(item, "custom_id")]
    assert "tipmodal:staff_approve" in custom_ids
    assert "tipmodal:staff_resubmit" in custom_ids
    assert view.is_persistent() is True


@pytest.mark.asyncio
async def test_cog_load_registers_views(mock_bot):
    cog = TipModal(mock_bot)
    await cog.cog_load()
    assert mock_bot.add_view.call_count == 3
