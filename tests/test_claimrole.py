"""
Unit tests for ClaimRole Cog, anti-abuse cooldowns, and button role toggling.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest
import discord
from claimrole.claimrole import (
    ClaimRole,
    ClaimRoleView,
    ClaimRoleDynamicButton,
    parse_button_style,
    parse_emoji_string,
)


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 123456789
    bot.add_dynamic_items = MagicMock()
    bot.add_view = MagicMock()
    return bot


@pytest.fixture
def mock_guild():
    guild = MagicMock(spec=discord.Guild)
    guild.id = 99887766
    guild.name = "Test Guild"

    me = MagicMock(spec=discord.Member)
    me.id = 123456789
    me.guild_permissions = discord.Permissions(manage_roles=True)
    bot_top_role = MagicMock(spec=discord.Role)
    bot_top_role.position = 100
    bot_top_role.name = "Bot Master"
    me.top_role = bot_top_role
    guild.me = me

    return guild


@pytest.fixture
def mock_interaction(mock_guild):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = mock_guild

    member = MagicMock(spec=discord.Member)
    member.id = 426878496468500493
    member.display_name = "Sablinova"
    member.roles = []
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    interaction.user = member

    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()

    return interaction


def test_parse_button_style():
    assert parse_button_style("green") == discord.ButtonStyle.success
    assert parse_button_style("success") == discord.ButtonStyle.success
    assert parse_button_style("blurple") == discord.ButtonStyle.primary
    assert parse_button_style("blue") == discord.ButtonStyle.primary
    assert parse_button_style("red") == discord.ButtonStyle.danger
    assert parse_button_style("danger") == discord.ButtonStyle.danger
    assert parse_button_style("gray") == discord.ButtonStyle.secondary
    assert parse_button_style("secondary") == discord.ButtonStyle.secondary
    assert parse_button_style("invalid") == discord.ButtonStyle.secondary
    assert parse_button_style(None) == discord.ButtonStyle.secondary


def test_parse_emoji_string():
    assert parse_emoji_string(None, "🎮") == "🎮"
    assert parse_emoji_string(None, "none") is None
    assert parse_emoji_string(None, "") is None


@pytest.mark.asyncio
async def test_claim_role_toggle_add(mock_bot, mock_guild, mock_interaction):
    cog = ClaimRole(mock_bot)

    target_role = MagicMock(spec=discord.Role)
    target_role.id = 555666777
    target_role.name = "Gamer"
    target_role.position = 50
    mock_guild.get_role.return_value = target_role

    # User does NOT have role: should add
    await cog.handle_role_toggle(mock_interaction, target_role.id)

    mock_interaction.user.add_roles.assert_awaited_once_with(
        target_role,
        reason=f"ClaimRole button: toggled on by {mock_interaction.user}",
    )
    mock_interaction.response.send_message.assert_awaited_once()
    msg = mock_interaction.response.send_message.call_args[0][0]
    assert "You have been given the **Gamer** role!" in msg
    assert mock_interaction.response.send_message.call_args[1].get("ephemeral") is True


@pytest.mark.asyncio
async def test_claim_role_toggle_remove(mock_bot, mock_guild, mock_interaction):
    cog = ClaimRole(mock_bot)

    target_role = MagicMock(spec=discord.Role)
    target_role.id = 555666777
    target_role.name = "Gamer"
    target_role.position = 50
    mock_guild.get_role.return_value = target_role

    # User ALREADY has role: should remove
    mock_interaction.user.roles = [target_role]

    await cog.handle_role_toggle(mock_interaction, target_role.id)

    mock_interaction.user.remove_roles.assert_awaited_once_with(
        target_role,
        reason=f"ClaimRole button: toggled off by {mock_interaction.user}",
    )
    mock_interaction.response.send_message.assert_awaited_once()
    msg = mock_interaction.response.send_message.call_args[0][0]
    assert "Removed the **Gamer** role from you." in msg


@pytest.mark.asyncio
async def test_claim_role_anti_abuse_cooldown(mock_bot, mock_guild, mock_interaction):
    cog = ClaimRole(mock_bot)

    target_role = MagicMock(spec=discord.Role)
    target_role.id = 555666777
    target_role.name = "Gamer"
    target_role.position = 50
    mock_guild.get_role.return_value = target_role

    # First click succeeds
    await cog.handle_role_toggle(mock_interaction, target_role.id)
    assert mock_interaction.user.add_roles.await_count == 1

    # Immediate second click hits anti-abuse rate limit
    mock_interaction.response.send_message.reset_mock()
    await cog.handle_role_toggle(mock_interaction, target_role.id)

    mock_interaction.response.send_message.assert_awaited_once()
    msg = mock_interaction.response.send_message.call_args[0][0]
    assert "Please slow down!" in msg
    assert mock_interaction.user.add_roles.await_count == 1


@pytest.mark.asyncio
async def test_claim_role_hierarchy_check(mock_bot, mock_guild, mock_interaction):
    cog = ClaimRole(mock_bot)

    # Role higher than bot top role
    high_role = MagicMock(spec=discord.Role)
    high_role.id = 999111
    high_role.name = "Admin"
    high_role.position = 150
    mock_guild.get_role.return_value = high_role
    mock_guild.me.top_role.position = 100
    mock_guild.me.top_role.__le__ = MagicMock(return_value=True)

    await cog.handle_role_toggle(mock_interaction, high_role.id)

    mock_interaction.response.send_message.assert_awaited_once()
    msg = mock_interaction.response.send_message.call_args[0][0]
    assert "higher than or equal to my highest role" in msg
    assert mock_interaction.user.add_roles.await_count == 0


@pytest.mark.asyncio
async def test_claim_role_view_custom_ids():
    buttons = [
        {"role_id": 111, "label": "Gamer", "style": "green", "emoji": "🎮"},
        {"role_id": 222, "label": "Updates", "style": "blurple", "emoji": "🔔"},
        {"role_id": 333, "label": "VIP", "style": "red", "emoji": "🌟"},
    ]
    view = ClaimRoleView(buttons)
    assert len(view.children) == 3
    assert view.children[0].custom_id == "claimrole:toggle:111"
    assert view.children[0].style == discord.ButtonStyle.success
    assert view.children[1].custom_id == "claimrole:toggle:222"
    assert view.children[1].style == discord.ButtonStyle.primary
    assert view.children[2].custom_id == "claimrole:toggle:333"
    assert view.children[2].style == discord.ButtonStyle.danger
    assert view.is_persistent() is True


@pytest.mark.asyncio
async def test_claimrole_fromjson_command(mock_bot, mock_guild):
    cog = ClaimRole(mock_bot)

    ctx = MagicMock()
    ctx.guild = mock_guild
    ctx.clean_prefix = "-"
    ctx.message.attachments = []
    ctx.send = AsyncMock()

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 111222333
    channel.mention = "<#111222333>"
    channel.permissions_for.return_value = MagicMock(manage_webhooks=False)

    fake_msg = MagicMock()
    fake_msg.id = 8877665544
    channel.send = AsyncMock(return_value=fake_msg)

    discohook_json = """```json
    {
        "content": "Welcome to role selection!",
        "embeds": [
            {
                "title": "Choose your roles",
                "description": "Click below",
                "color": 3447003
            }
        ]
    }
    ```"""

    await cog.claimrole_fromjson.callback(cog, ctx, channel, json_input=discohook_json)
    channel.send.assert_awaited_once()
    send_kwargs = channel.send.call_args[1]
    assert send_kwargs["content"] == "Welcome to role selection!"
    assert len(send_kwargs["embeds"]) == 1
    assert send_kwargs["embeds"][0].title == "Choose your roles"

    ctx.send.assert_awaited_once()
    reply = ctx.send.call_args[0][0]
    assert "Discohook layout posted successfully" in reply
    assert "8877665544" in reply

