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
    interaction.response.is_done.return_value = False
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
    assert "You got the **Gamer** role!" in msg
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
    assert mock_interaction.response.send_message.call_args[1].get("ephemeral") is True


@pytest.mark.asyncio
async def test_claim_role_anti_abuse_cooldown(mock_bot, mock_guild, mock_interaction):
    cog = ClaimRole(mock_bot)

    target_role_1 = MagicMock(spec=discord.Role)
    target_role_1.id = 555666777
    target_role_1.name = "Gamer"
    target_role_1.position = 50

    target_role_2 = MagicMock(spec=discord.Role)
    target_role_2.id = 888999111
    target_role_2.name = "VIP"
    target_role_2.position = 40

    mock_guild.get_role.side_effect = lambda rid: target_role_1 if rid == 555666777 else target_role_2

    # First click succeeds
    await cog.handle_role_toggle(mock_interaction, target_role_1.id)
    assert mock_interaction.user.add_roles.await_count == 1

    # Immediate second click on the SAME button hits anti-abuse rate limit with snowtime
    mock_interaction.response.send_message.reset_mock()
    await cog.handle_role_toggle(mock_interaction, target_role_1.id)

    mock_interaction.response.send_message.assert_awaited_once()
    msg = mock_interaction.response.send_message.call_args[0][0]
    assert "Cooldown active for this button!" in msg
    assert "<t:" in msg and ":R>" in msg
    assert mock_interaction.response.send_message.call_args[1].get("ephemeral") is True
    assert mock_interaction.user.add_roles.await_count == 1

    # Click on a DIFFERENT button succeeds without hitting cooldown (per-button rate limiting)
    mock_interaction.response.send_message.reset_mock()
    await cog.handle_role_toggle(mock_interaction, target_role_2.id)
    assert mock_interaction.user.add_roles.await_count == 2
    second_msg = mock_interaction.response.send_message.call_args[0][0]
    assert "You got the **VIP** role!" in second_msg


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


@pytest.mark.asyncio
async def test_claimrole_langpanel_command(mock_bot, mock_guild):
    cog = ClaimRole(mock_bot)

    # Setup guild roles
    role_names = [
        "English Speaker", "PT BR Speaker", "Tagalog Speaker",
        "Hindi Speaker", "Indonesian Speaker", "Arabic Speaker", "French Speaker"
    ]
    mock_roles = []
    for idx, name in enumerate(role_names, 1):
        r = MagicMock(spec=discord.Role)
        r.id = 1000 + idx
        r.name = name
        r.mention = f"<@&{1000 + idx}>"
        mock_roles.append(r)
    mock_guild.roles = mock_roles

    ctx = MagicMock()
    ctx.guild = mock_guild
    ctx.channel = MagicMock(spec=discord.TextChannel)
    ctx.message.attachments = []
    ctx.send = AsyncMock()

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 555444333
    channel.mention = "<#555444333>"

    fake_msg = MagicMock()
    fake_msg.id = 99112233
    channel.send = AsyncMock(return_value=fake_msg)

    banner_url = "https://cdn.discordapp.com/attachments/banner.png"
    await cog.claimrole_langpanel.callback(cog, ctx, channel, banner_url=banner_url)

    channel.send.assert_awaited_once()
    send_kwargs = channel.send.call_args[1]

    # Must be either native file attachment or text-free image embed
    if "file" in send_kwargs:
        assert isinstance(send_kwargs["file"], discord.File)
        assert "embed" not in send_kwargs or send_kwargs["embed"] is None
    else:
        embed = send_kwargs["embed"]
        assert embed.title is None
        assert embed.description is None

    view = send_kwargs["view"]
    assert len(view.children) == 7

    button_labels = [b.label for b in view.children]
    assert "English" in button_labels
    assert "Português" in button_labels
    assert "Tagalog" in button_labels
    assert "Hindi" in button_labels
    assert "Indonesian" in button_labels
    assert "Arabic" in button_labels
    assert "Français" in button_labels

    ctx.send.assert_awaited_once()
    response = ctx.send.call_args[0][0]
    assert "Language role panel deployed" in response
    assert "Added 7 buttons" in response


@pytest.mark.asyncio
async def test_claimrole_imagepanel_command(mock_bot, mock_guild):
    cog = ClaimRole(mock_bot)

    ctx = MagicMock()
    ctx.guild = mock_guild
    ctx.channel = MagicMock(spec=discord.TextChannel)
    ctx.clean_prefix = "-"
    ctx.message.attachments = []
    ctx.send = AsyncMock()

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 777666555
    channel.mention = "<#777666555>"

    fake_msg = MagicMock()
    fake_msg.id = 44556677
    channel.send = AsyncMock(return_value=fake_msg)

    img_url = "https://example.com/banner.png"
    await cog.claimrole_imagepanel.callback(cog, ctx, channel, image_url=img_url)

    channel.send.assert_awaited_once()
    send_kwargs = channel.send.call_args[1]

    if "file" in send_kwargs:
        assert isinstance(send_kwargs["file"], discord.File)
    else:
        embed = send_kwargs["embed"]
        assert embed.title is None
        assert embed.description is None
        assert embed.image.url == img_url

    ctx.send.assert_awaited_once()
    response = ctx.send.call_args[0][0]
    assert "Picture panel posted in" in response
    assert "44556677" in response


@pytest.mark.asyncio
async def test_claimrole_cooldown_command(mock_bot, mock_guild):
    cog = ClaimRole(mock_bot)

    ctx = MagicMock()
    ctx.guild = mock_guild
    ctx.send = AsyncMock()

    # Default should be 60.0
    default_cd = await cog.config.guild(mock_guild).cooldown_seconds()
    assert default_cd == 60.0

    # Setting custom cooldown to 120s
    await cog.claimrole_cooldown.callback(cog, ctx, seconds=120.0)
    ctx.send.assert_awaited_once()
    assert "120.0s" in ctx.send.call_args[0][0]

    updated_cd = await cog.config.guild(mock_guild).cooldown_seconds()
    assert updated_cd == 120.0



