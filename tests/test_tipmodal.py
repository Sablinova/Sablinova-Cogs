"""
Unit tests for the TipModal Cog, interactive UI components, and automated screenshot binding (both DM and Channel).
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
    interaction.user.send = AsyncMock()

    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()

    channel = MagicMock()
    channel.id = 1122334455
    channel.name = "verification"
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
async def test_tip_submission_modal_on_submit_sends_dm_and_tracks_pending(mock_bot, mock_interaction):
    cog = TipModal(mock_bot)
    modal = TipSubmissionModal(cog=cog)
    modal.method._value = "PayPal Pass"
    modal.game._value = "Crimson Desert"
    modal.proof_id._value = "I-12345678"
    modal.screenshot_link._value = ""
    modal.extra_info._value = "None"

    await modal.on_submit(mock_interaction)

    user_id = mock_interaction.user.id
    assert user_id in cog.pending_submissions
    assert cog.pending_submissions[user_id]["message_id"] == 9988776655
    assert cog.pending_submissions[user_id]["channel_id"] == 1122334455
    assert cog.pending_submissions[user_id]["game_name"] == "Crimson Desert"

    # Verify DM was sent to the user
    mock_interaction.user.send.assert_awaited_once()
    dm_kwargs = mock_interaction.user.send.call_args[1]
    assert "embed" in dm_kwargs
    assert "Private Screenshot Upload" in dm_kwargs["embed"].title

    # Verify ephemeral response directed user to check DMs
    ephemeral_msg = mock_interaction.response.send_message.call_args[0][0]
    assert "Check your DMs" in ephemeral_msg


@pytest.mark.asyncio
async def test_tip_submission_modal_dm_forbidden_fallback(mock_bot, mock_interaction):
    cog = TipModal(mock_bot)
    mock_interaction.user.send.side_effect = discord.Forbidden(MagicMock(), "Cannot send messages to this user")

    modal = TipSubmissionModal(cog=cog)
    modal.method._value = "PayPal Pass"
    modal.game._value = "Crimson Desert"
    modal.proof_id._value = "I-12345678"
    modal.screenshot_link._value = ""
    modal.extra_info._value = "None"

    await modal.on_submit(mock_interaction)

    # Ephemeral message notifies user that DMs are closed and to upload in channel
    ephemeral_msg = mock_interaction.response.send_message.call_args[0][0]
    assert "your DMs appear closed" in ephemeral_msg


@pytest.mark.asyncio
async def test_on_message_dm_screenshot_binds_to_channel_ticket(mock_bot):
    cog = TipModal(mock_bot)
    user_id = 426878496468500493
    channel_id = 1122334455
    ticket_msg_id = 9988776655

    cog.pending_submissions[user_id] = {
        "message_id": ticket_msg_id,
        "channel_id": channel_id,
        "created_at": 100000000000.0,
        "game_name": "Crimson Desert",
    }

    # Simulate message sent in DM (guild is None)
    message = MagicMock()
    message.author.bot = False
    message.author.id = user_id
    message.guild = None
    message.reply = AsyncMock()
    message.add_reaction = AsyncMock()

    attachment = MagicMock()
    attachment.content_type = "image/jpeg"
    attachment.filename = "paypal_proof.jpg"
    attachment.url = "https://cdn.discordapp.com/attachments/dm_channel/paypal_proof.jpg"
    message.attachments = [attachment]

    mock_ticket_channel = MagicMock()
    mock_ticket_channel.name = "verification"
    mock_ticket_msg = MagicMock()
    existing_embed = discord.Embed(title="🧾 Tip Verification Submission")
    existing_embed.add_field(name="📊 Status", value="⏳ **Awaiting Screenshot Proof**", inline=False)
    existing_embed.add_field(name="📌 Next Step: Upload Screenshot Proof", value="Upload below", inline=False)
    mock_ticket_msg.embeds = [existing_embed]
    mock_ticket_msg.edit = AsyncMock()
    mock_ticket_channel.fetch_message = AsyncMock(return_value=mock_ticket_msg)

    mock_bot.get_channel = MagicMock(return_value=mock_ticket_channel)

    await cog.on_message(message)

    # Verify pending submission was consumed
    assert user_id not in cog.pending_submissions

    # Verify ticket embed in the server channel was updated with the DM screenshot
    mock_ticket_msg.edit.assert_awaited_once()
    updated_embed = mock_ticket_msg.edit.call_args[1]["embed"]
    assert updated_embed.image.url == attachment.url
    updated_fields = {f.name: f.value for f in updated_embed.fields}
    assert "Proof sent privately in DM" in updated_fields["📊 Status"]
    assert "📌 Next Step: Upload Screenshot Proof" not in updated_fields

    # Verify user received a confirmation in DM
    message.reply.assert_awaited_once()
    assert "Screenshot received and attached securely!" in message.reply.call_args[0][0]


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
