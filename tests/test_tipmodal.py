"""
Unit tests for the TipModal Cog and interactive UI components.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest
import discord
from tipmodal.tipmodal import TipModal, TipSubmissionModal, TipButtonView, SubmissionGuidanceView


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
    channel.send = AsyncMock()
    interaction.channel = channel

    return interaction


@pytest.mark.asyncio
async def test_tip_submission_modal_properties():
    modal = TipSubmissionModal()
    assert modal.title == "Tip & Pass Verification"
    assert modal.method.label == "Tip Method & Pass Tier"
    assert modal.game.label == "Requested Game Name"
    assert modal.proof_id.label == "Profile ID / Note / Patreon Display Name"
    assert modal.extra_info.label == "Extra Info / Gifting User ID (Optional)"
    assert modal.method.required is True
    assert modal.game.required is True
    assert modal.proof_id.required is True
    assert modal.extra_info.required is False


@pytest.mark.asyncio
async def test_tip_submission_modal_on_submit(mock_interaction):
    modal = TipSubmissionModal()
    modal.method._value = "1 Time Tip"
    modal.game._value = "Crimson Desert"
    modal.proof_id._value = "Note: Sablinova / Crimson Desert"
    modal.extra_info._value = "None"

    await modal.on_submit(mock_interaction)

    # Verify ephemeral response to user
    mock_interaction.response.send_message.assert_awaited_once()
    sent_msg = mock_interaction.response.send_message.call_args[0][0]
    assert "Tip details submitted successfully!" in sent_msg
    assert "Crimson Desert" in sent_msg
    assert mock_interaction.response.send_message.call_args[1].get("ephemeral") is True

    # Verify public embed sent into channel
    mock_interaction.channel.send.assert_awaited_once()
    kwargs = mock_interaction.channel.send.call_args[1]
    embed = kwargs.get("embed")
    assert embed is not None
    assert embed.title == "🧾 Tip Verification Submission"
    fields = {f.name: f.value for f in embed.fields}
    assert "👤 Submitter" in fields
    assert fields["💳 Tip Method"] == "1 Time Tip"
    assert fields["🎮 Requested Game"] == "Crimson Desert"
    assert fields["🔑 Verification ID / Note"] == "`Note: Sablinova / Crimson Desert`"
    assert fields["📝 Extra Info / Gifting"] == "*None specified*"
    assert "📌 Next Step: Upload Screenshot Proof" in fields


@pytest.mark.asyncio
async def test_tip_button_view_persistent_ids():
    view = TipButtonView()
    custom_ids = [item.custom_id for item in view.children if hasattr(item, "custom_id")]
    assert "tipmodal:open_form" in custom_ids
    assert "tipmodal:view_instructions" in custom_ids
    assert view.is_persistent() is True


@pytest.mark.asyncio
async def test_submission_guidance_view_persistent():
    view = SubmissionGuidanceView()
    custom_ids = [item.custom_id for item in view.children if hasattr(item, "custom_id")]
    assert "tipmodal:screenshot_help" in custom_ids
    assert view.is_persistent() is True


@pytest.mark.asyncio
async def test_cog_load_registers_views(mock_bot):
    cog = TipModal(mock_bot)
    await cog.cog_load()
    assert mock_bot.add_view.call_count == 2
