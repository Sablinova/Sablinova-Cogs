"""
Unit tests for InviteRole Cog, duration parsing, invite code extraction,
cache synchronization, and member join role assignment.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import discord
from redbot.core import commands
from inviterole.inviterole import (
    InviteRole,
    parse_duration,
    format_duration,
    extract_invite_code,
)


# =============================================================================
# Helper Utility Tests
# =============================================================================

def test_parse_duration_valid():
    assert parse_duration("0") == 0
    assert parse_duration("never") == 0
    assert parse_duration("infinite") == 0
    assert parse_duration("permanent") == 0
    assert parse_duration(None) == 0
    assert parse_duration("") == 0
    assert parse_duration("30m") == 1800
    assert parse_duration("12h") == 43200
    assert parse_duration("1d") == 86400
    assert parse_duration("7d") == 604800
    assert parse_duration("1d12h30m") == 86400 + 43200 + 1800
    assert parse_duration("3600") == 3600


def test_parse_duration_invalid_and_limits():
    with pytest.raises(commands.BadArgument):
        parse_duration("8d")  # Exceeds 7 days

    with pytest.raises(commands.BadArgument):
        parse_duration("604801")  # Exceeds 7 days in seconds

    with pytest.raises(commands.BadArgument):
        parse_duration("invalid_str")

    with pytest.raises(commands.BadArgument):
        parse_duration("10x")


def test_format_duration():
    assert format_duration(0) == "Permanent (Never expires)"
    assert format_duration(-1) == "Permanent (Never expires)"
    assert format_duration(86400) == "1d"
    assert format_duration(90000) == "1d 1h"
    assert format_duration(60) == "1m"


def test_extract_invite_code():
    assert extract_invite_code("AbCdEf") == "AbCdEf"
    assert extract_invite_code("https://discord.gg/AbCdEf") == "AbCdEf"
    assert extract_invite_code("http://discord.com/invite/AbCdEf") == "AbCdEf"
    assert extract_invite_code("discord.gg/AbCdEf/") == "AbCdEf"
    assert extract_invite_code("  https://discord.gg/xyz123  ") == "xyz123"


# =============================================================================
# Fixtures for Discord Mocks
# =============================================================================

@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 123456789
    bot.is_owner = MagicMock(return_value=False)
    bot.wait_until_ready = AsyncMock()
    bot.guilds = []
    return bot


@pytest.fixture
def mock_guild():
    guild = MagicMock(spec=discord.Guild)
    guild.id = 99887766
    guild.name = "Test Guild"
    guild.owner_id = 111111111

    # Default role (@everyone)
    default_role = MagicMock(spec=discord.Role)
    default_role.id = 99887766
    default_role.name = "@everyone"
    guild.default_role = default_role

    # Bot Member
    me = MagicMock(spec=discord.Member)
    me.id = 123456789
    me.guild_permissions = discord.Permissions(manage_roles=True, manage_guild=True)

    bot_top_role = MagicMock(spec=discord.Role)
    bot_top_role.position = 100
    bot_top_role.name = "Bot Master"
    me.top_role = bot_top_role
    guild.me = me

    guild.invites = AsyncMock(return_value=[])
    guild.get_role = MagicMock(return_value=None)
    guild.get_channel = MagicMock(return_value=None)
    return guild


@pytest.fixture
def mock_author():
    author = MagicMock(spec=discord.Member)
    author.id = 222222222
    author.name = "Admin"
    author_top_role = MagicMock(spec=discord.Role)
    author_top_role.position = 90
    author.top_role = author_top_role
    return author


@pytest.fixture
def mock_ctx(mock_guild, mock_author, mock_bot):
    ctx = MagicMock(spec=commands.Context)
    ctx.bot = mock_bot
    ctx.guild = mock_guild
    ctx.author = mock_author
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 555555555
    channel.mention = "<#555555555>"
    channel.permissions_for = MagicMock(return_value=discord.Permissions(create_instant_invite=True))
    channel.create_invite = AsyncMock()
    ctx.channel = channel
    ctx.send = AsyncMock()
    return ctx


# =============================================================================
# Cog Functional & Join Logic Tests
# =============================================================================

@pytest.mark.asyncio
async def test_role_hierarchy_validation(mock_bot, mock_ctx, mock_guild):
    cog = InviteRole(mock_bot)

    # 1. Reject @everyone
    err = cog._validate_role_assignable(mock_ctx, mock_guild.default_role)
    assert err is not None
    assert "@everyone" in err

    # 2. Reject role higher than bot
    high_role = MagicMock(spec=discord.Role)
    high_role.position = 110
    high_role.name = "Supreme"
    high_role.mention = "@Supreme"
    high_role.is_bot_managed.return_value = False
    high_role.is_integration.return_value = False
    err = cog._validate_role_assignable(mock_ctx, high_role)
    assert err is not None
    assert "higher than or equal to my highest role" in err

    # 3. Reject role higher than invoker
    mod_role = MagicMock(spec=discord.Role)
    mod_role.position = 95
    mod_role.name = "HigherThanAuthor"
    mod_role.mention = "@HigherThanAuthor"
    mod_role.is_bot_managed.return_value = False
    mod_role.is_integration.return_value = False
    err = cog._validate_role_assignable(mock_ctx, mod_role)
    assert err is not None
    assert "higher than or equal to your highest role" in err

    # 4. Accept role lower than both
    valid_role = MagicMock(spec=discord.Role)
    valid_role.position = 50
    valid_role.name = "MemberRole"
    valid_role.is_bot_managed.return_value = False
    valid_role.is_integration.return_value = False
    err = cog._validate_role_assignable(mock_ctx, valid_role)
    assert err is None


@pytest.mark.asyncio
async def test_on_member_join_use_count_increment(mock_bot, mock_guild):
    cog = InviteRole(mock_bot)

    # Pre-populate cache: INV_A was used 3 times
    cog._invite_cache[mock_guild.id] = {"INV_A": 3, "INV_B": 0}

    # Target role
    target_role = MagicMock(spec=discord.Role)
    target_role.id = 7777777
    target_role.position = 50
    target_role.name = "VipRole"
    target_role.is_bot_managed.return_value = False
    target_role.is_integration.return_value = False
    mock_guild.get_role.side_effect = lambda rid: target_role if rid == 7777777 else None

    # Configured invite in guild
    configured = {
        "INV_A": {
            "role_ids": [7777777],
            "channel_id": 555555555,
            "max_uses": 0,
            "max_age": 0,
            "total_joined": 3,
            "note": "VIP Link",
        }
    }
    # Set config directly via Redbot config
    await cog.config.guild(mock_guild).invites.set(configured)

    # Simulate live invites: INV_A uses incremented from 3 to 4
    inv_a = MagicMock()
    inv_a.code = "INV_A"
    inv_a.uses = 4

    inv_b = MagicMock()
    inv_b.code = "INV_B"
    inv_b.uses = 0

    mock_guild.invites.return_value = [inv_a, inv_b]

    new_member = MagicMock(spec=discord.Member)
    new_member.bot = False
    new_member.guild = mock_guild
    new_member.id = 33333333
    new_member.add_roles = AsyncMock()

    # Trigger join event
    await cog.on_member_join(new_member)

    # Member should be assigned target_role
    new_member.add_roles.assert_called_once()
    args, kwargs = new_member.add_roles.call_args
    assert target_role in args
    assert "INV_A" in kwargs.get("reason", "")

    # Config stats should be updated
    saved_invites = await cog.config.guild(mock_guild).invites()
    assert saved_invites["INV_A"]["total_joined"] == 4

    # Cache updated
    assert cog._invite_cache[mock_guild.id]["INV_A"] == 4


@pytest.mark.asyncio
async def test_on_member_join_single_use_vanished(mock_bot, mock_guild):
    cog = InviteRole(mock_bot)

    # Single use invite that was in cache with 0 uses
    cog._invite_cache[mock_guild.id] = {"INV_SINGLE": 0}

    target_role = MagicMock(spec=discord.Role)
    target_role.id = 8888888
    target_role.position = 50
    target_role.name = "OneTimeRole"
    target_role.is_bot_managed.return_value = False
    target_role.is_integration.return_value = False
    mock_guild.get_role.side_effect = lambda rid: target_role if rid == 8888888 else None

    configured = {
        "INV_SINGLE": {
            "role_ids": [8888888],
            "channel_id": 555555555,
            "max_uses": 1,
            "max_age": 0,
            "total_joined": 0,
            "note": "Single use VIP key",
        }
    }

    await cog.config.guild(mock_guild).invites.set(configured)

    # Discord has purged INV_SINGLE because max_uses was reached! Live invites is empty.
    mock_guild.invites.return_value = []

    new_member = MagicMock(spec=discord.Member)
    new_member.bot = False
    new_member.guild = mock_guild
    new_member.id = 44444444
    new_member.add_roles = AsyncMock()

    await cog.on_member_join(new_member)

    new_member.add_roles.assert_called_once()
    args, kwargs = new_member.add_roles.call_args
    assert target_role in args
    assert "INV_SINGLE" in kwargs.get("reason", "")


@pytest.mark.asyncio
async def test_inviterole_create_command(mock_bot, mock_ctx, mock_guild):
    cog = InviteRole(mock_bot)

    target_role = MagicMock(spec=discord.Role)
    target_role.id = 12345
    target_role.position = 50
    target_role.name = "NewbieRole"
    target_role.mention = "@NewbieRole"
    target_role.color = discord.Color.blue()
    target_role.is_bot_managed.return_value = False
    target_role.is_integration.return_value = False

    created_invite = MagicMock()
    created_invite.code = "NEW_INVITE_123"
    mock_ctx.channel.create_invite.return_value = created_invite

    # Call command callback
    await cog.create_invite_role.callback(
        cog,
        mock_ctx,
        role=target_role,
        max_uses=10,
        duration="1d",
        channel=mock_ctx.channel,
        note="Welcome invite",
    )

    # Verify invite created on channel
    mock_ctx.channel.create_invite.assert_awaited_once_with(
        max_age=86400,
        max_uses=10,
        unique=True,
        reason=f"InviteRole created by {mock_ctx.author} ({mock_ctx.author.id}) for role @NewbieRole",
    )

    # Verify config stored
    invites = await cog.config.guild(mock_guild).invites()
    assert "NEW_INVITE_123" in invites
    assert invites["NEW_INVITE_123"]["role_ids"] == [12345]
    assert invites["NEW_INVITE_123"]["max_uses"] == 10
    assert invites["NEW_INVITE_123"]["max_age"] == 86400
    assert invites["NEW_INVITE_123"]["note"] == "Welcome invite"

    # Verify confirmation sent
    mock_ctx.send.assert_awaited_once()
    embed = mock_ctx.send.call_args[1].get("embed")
    assert embed is not None
    assert "NEW_INVITE_123" in embed.description or any("NEW_INVITE_123" in f.value for f in embed.fields)


@pytest.mark.asyncio
async def test_inviterole_link_and_unlink_command(mock_bot, mock_ctx, mock_guild):
    cog = InviteRole(mock_bot)

    target_role = MagicMock(spec=discord.Role)
    target_role.id = 54321
    target_role.position = 40
    target_role.name = "LinkedRole"
    target_role.mention = "@LinkedRole"
    target_role.is_bot_managed.return_value = False
    target_role.is_integration.return_value = False

    existing_invite = MagicMock()
    existing_invite.code = "EXISTING_CODE"
    existing_invite.uses = 5
    existing_invite.max_uses = 100
    existing_invite.max_age = 0
    existing_invite.channel = mock_ctx.channel
    existing_invite.delete = AsyncMock()

    mock_guild.invites.return_value = [existing_invite]

    # Link invite
    await cog.link_existing_invite.callback(
        cog,
        mock_ctx,
        invite_code_or_url="https://discord.gg/EXISTING_CODE",
        role=target_role,
        note="Community Link",
    )

    invites = await cog.config.guild(mock_guild).invites()
    assert "EXISTING_CODE" in invites
    assert invites["EXISTING_CODE"]["role_ids"] == [54321]

    # Add second role
    second_role = MagicMock(spec=discord.Role)
    second_role.id = 98765
    second_role.position = 30
    second_role.name = "BonusRole"
    second_role.mention = "@BonusRole"
    second_role.is_bot_managed.return_value = False
    second_role.is_integration.return_value = False

    await cog.add_role_to_invite.callback(
        cog,
        mock_ctx,
        invite_code_or_url="EXISTING_CODE",
        role=second_role,
    )

    invites = await cog.config.guild(mock_guild).invites()
    assert invites["EXISTING_CODE"]["role_ids"] == [54321, 98765]

    # Remove first role
    await cog.remove_role_from_invite.callback(
        cog,
        mock_ctx,
        invite_code_or_url="EXISTING_CODE",
        role=target_role,
    )

    invites = await cog.config.guild(mock_guild).invites()
    assert invites["EXISTING_CODE"]["role_ids"] == [98765]

    # Unlink with Discord deletion
    await cog.unlink_invite.callback(
        cog,
        mock_ctx,
        invite_code_or_url="EXISTING_CODE",
        delete_discord_invite=True,
    )

    invites = await cog.config.guild(mock_guild).invites()
    assert "EXISTING_CODE" not in invites
    existing_invite.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_inviterole_logchannel_and_toggledm(mock_bot, mock_ctx, mock_guild):
    cog = InviteRole(mock_bot)

    # Set log channel
    test_ch = MagicMock(spec=discord.TextChannel)
    test_ch.id = 667788
    test_ch.mention = "<#667788>"
    test_ch.permissions_for.return_value = discord.Permissions(send_messages=True)

    await cog.set_log_channel.callback(cog, mock_ctx, channel=test_ch)
    assert await cog.config.guild(mock_guild).log_channel_id() == 667788

    # Clear log channel
    await cog.set_log_channel.callback(cog, mock_ctx, channel=None)
    assert await cog.config.guild(mock_guild).log_channel_id() is None

    # Toggle DM
    assert await cog.config.guild(mock_guild).dm_user() is False
    await cog.toggle_dm.callback(cog, mock_ctx)
    assert await cog.config.guild(mock_guild).dm_user() is True

    # Set DM template
    await cog.set_dm_message.callback(cog, mock_ctx, message="Hey {mention}, welcome!")
    assert await cog.config.guild(mock_guild).dm_message() == "Hey {mention}, welcome!"


# =============================================================================
# ThreadGate Feature Tests
# =============================================================================

@pytest.mark.asyncio
async def test_threadgate_add_remove_commands(mock_bot, mock_ctx, mock_guild):
    cog = InviteRole(mock_bot)

    target_role = MagicMock(spec=discord.Role)
    target_role.id = 1565428384937287821
    target_role.position = 45
    target_role.name = "fmhy"
    target_role.mention = "@fmhy"
    target_role.is_bot_managed.return_value = False
    target_role.is_integration.return_value = False

    test_channel = MagicMock(spec=discord.TextChannel)
    test_channel.id = 1565423109584978071
    test_channel.mention = "<#1565423109584978071>"

    # Add threadgate
    await cog.threadgate_add.callback(
        cog,
        mock_ctx,
        channel=test_channel,
        role=target_role,
    )

    gates = await cog.config.guild(mock_guild).threadgates()
    assert str(test_channel.id) in gates
    assert gates[str(test_channel.id)]["role_id"] == 1565428384937287821
    assert cog._threadgate_cache[mock_guild.id][test_channel.id] == 1565428384937287821

    # Remove threadgate
    await cog.threadgate_remove.callback(
        cog,
        mock_ctx,
        channel=test_channel,
    )

    gates = await cog.config.guild(mock_guild).threadgates()
    assert str(test_channel.id) not in gates
    assert test_channel.id not in cog._threadgate_cache.get(mock_guild.id, {})


@pytest.mark.asyncio
async def test_threadgate_revoke_on_thread_close(mock_bot, mock_guild):
    cog = InviteRole(mock_bot)

    channel_id = 1565423109584978071
    role_id = 1565428384937287821

    # Setup threadgate cache
    cog._threadgate_cache[mock_guild.id] = {channel_id: role_id}

    target_role = MagicMock(spec=discord.Role)
    target_role.id = role_id
    target_role.position = 45
    target_role.name = "fmhy"
    mock_guild.get_role.side_effect = lambda rid: target_role if rid == role_id else None

    # Tracked member with fmhy role
    user_id = 77889900
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.bot = False
    member.roles = [target_role]
    member.remove_roles = AsyncMock()
    mock_guild.get_member.side_effect = lambda uid: member if uid == user_id else None

    thread_id = 11223344
    parent_channel = MagicMock(spec=discord.TextChannel)
    parent_channel.id = channel_id

    # Simulate thread member join
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = thread_id
    mock_thread.parent_id = channel_id
    mock_thread.guild = mock_guild
    mock_thread.name = "ticket-user123"

    tm = MagicMock(spec=discord.ThreadMember)
    tm.id = user_id
    tm.thread = mock_thread

    await cog.on_thread_member_join(tm)

    # Verify user is tracked
    assert user_id in cog._active_thread_users[mock_guild.id][thread_id]

    # Simulate thread update: closing/archiving thread
    before_thread = MagicMock(spec=discord.Thread)
    before_thread.id = thread_id
    before_thread.parent_id = channel_id
    before_thread.guild = mock_guild
    before_thread.archived = False
    before_thread.locked = False

    after_thread = MagicMock(spec=discord.Thread)
    after_thread.id = thread_id
    after_thread.parent_id = channel_id
    after_thread.guild = mock_guild
    after_thread.name = "ticket-user123"
    after_thread.archived = True
    after_thread.locked = False

    await cog.on_thread_update(before_thread, after_thread)

    # Member should have lost the fmhy role!
    member.remove_roles.assert_awaited_once_with(
        target_role,
        reason=f"InviteRole ThreadGate: Thread #ticket-user123 ({thread_id}) closed/archived in parent channel",
    )

    # Active tracking should be cleaned up
    assert thread_id not in cog._active_thread_users.get(mock_guild.id, {})


@pytest.mark.asyncio
async def test_threadgate_revoke_on_raw_thread_delete(mock_bot, mock_guild):
    cog = InviteRole(mock_bot)

    channel_id = 1565423109584978071
    role_id = 1565428384937287821

    cog._threadgate_cache[mock_guild.id] = {channel_id: role_id}
    mock_bot.get_guild.return_value = mock_guild

    target_role = MagicMock(spec=discord.Role)
    target_role.id = role_id
    target_role.position = 45
    target_role.name = "fmhy"
    mock_guild.get_role.side_effect = lambda rid: target_role if rid == role_id else None

    user_id = 88990011
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.bot = False
    member.roles = [target_role]
    member.remove_roles = AsyncMock()
    mock_guild.get_member.side_effect = lambda uid: member if uid == user_id else None

    thread_id = 99887766

    # Pre-register user in active_thread_users
    await cog._register_thread_users(mock_guild, thread_id, channel_id, role_id, [user_id])
    assert user_id in cog._active_thread_users[mock_guild.id][thread_id]

    # Trigger raw thread delete
    payload = MagicMock(spec=discord.RawThreadDeleteEvent)
    payload.guild_id = mock_guild.id
    payload.thread_id = thread_id
    payload.parent_id = channel_id

    await cog.on_raw_thread_delete(payload)

    # Member should have lost the fmhy role!
    member.remove_roles.assert_awaited_once_with(
        target_role,
        reason=f"InviteRole ThreadGate: Thread #{thread_id} deleted in parent channel",
    )

    # Active tracking should be cleaned up
    assert thread_id not in cog._active_thread_users.get(mock_guild.id, {})


@pytest.mark.asyncio
async def test_on_member_join_fmhy_unverified_sends_dm(mock_bot, mock_guild):
    """Test that joiners with FMHY role must verify before receiving the role."""
    cog = InviteRole(mock_bot)
    FMHY_ROLE_ID = 1565428384937287821
    fmhy_role = MagicMock(spec=discord.Role)
    fmhy_role.id = FMHY_ROLE_ID
    fmhy_role.position = 10
    fmhy_role.name = "Fmhy"
    fmhy_role.is_bot_managed.return_value = False
    fmhy_role.is_integration.return_value = False

    mock_guild.get_role.side_effect = lambda rid: fmhy_role if rid == FMHY_ROLE_ID else None

    # Setup configured invite
    await cog.config.guild(mock_guild).invites.set({
        "testCode": {
            "role_ids": [FMHY_ROLE_ID],
            "channel_id": 111,
            "max_uses": 0,
            "total_joined": 0,
        }
    })
    cog._invite_cache[mock_guild.id] = {"testCode": 0}

    inv = MagicMock(spec=discord.Invite)
    inv.code = "testCode"
    inv.uses = 1
    mock_guild.invites = AsyncMock(return_value=[inv])

    member = MagicMock(spec=discord.Member)
    member.guild = mock_guild
    member.bot = False
    member.id = 55667788
    member.mention = "<@55667788>"
    member.add_roles = AsyncMock()
    member.send = AsyncMock()

    # User is NOT verified
    cog._check_oauth_verified = AsyncMock(return_value=False)

    await cog.on_member_join(member)

    # Should NOT have assigned FMHY role
    member.add_roles.assert_not_awaited()
    # Should have sent DM with verify link
    member.send.assert_awaited_once()
    assert "fmhy.pubslounge.xyz/verify" in member.send.call_args[0][0]


@pytest.mark.asyncio
async def test_on_member_join_fmhy_verified_assigns_role(mock_bot, mock_guild):
    """Test that pre-verified joiners receive the FMHY role immediately."""
    cog = InviteRole(mock_bot)
    FMHY_ROLE_ID = 1565428384937287821
    fmhy_role = MagicMock(spec=discord.Role)
    fmhy_role.id = FMHY_ROLE_ID
    fmhy_role.position = 10
    fmhy_role.name = "Fmhy"
    fmhy_role.is_bot_managed.return_value = False
    fmhy_role.is_integration.return_value = False

    mock_guild.get_role.side_effect = lambda rid: fmhy_role if rid == FMHY_ROLE_ID else None

    # Setup configured invite
    await cog.config.guild(mock_guild).invites.set({
        "testCode": {
            "role_ids": [FMHY_ROLE_ID],
            "channel_id": 111,
            "max_uses": 0,
            "total_joined": 0,
        }
    })
    cog._invite_cache[mock_guild.id] = {"testCode": 0}

    inv = MagicMock(spec=discord.Invite)
    inv.code = "testCode"
    inv.uses = 1
    mock_guild.invites = AsyncMock(return_value=[inv])

    member = MagicMock(spec=discord.Member)
    member.guild = mock_guild
    member.bot = False
    member.id = 55667788
    member.mention = "<@55667788>"
    member.add_roles = AsyncMock()
    member.send = AsyncMock()

    # User IS verified
    cog._check_oauth_verified = AsyncMock(return_value=True)

    await cog.on_member_join(member)

    # Should have assigned FMHY role!
    member.add_roles.assert_awaited_once_with(
        fmhy_role,
        reason="InviteRole: Joined via invite link testCode",
    )
