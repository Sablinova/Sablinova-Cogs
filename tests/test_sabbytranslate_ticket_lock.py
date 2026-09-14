import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import pytest

from sabbytranslate.sabbytranslate import SabbyTranslate, LANGUAGES, normalize_language


class DummyChannel(MagicMock):
    def __init__(self, id_val: int, name: str, category_name: str = ""):
        super().__init__(spec=discord.TextChannel)
        self.id = id_val
        self.name = name
        self.mention = f"<#{id_val}>"
        self.category = DummyCategory(category_name) if category_name else None
        self.topic = ""


class DummyCategory:
    def __init__(self, name: str):
        self.name = name


class DummyUser:
    def __init__(self, id_val: int, name: str, bot: bool = False):
        self.id = id_val
        self.name = name
        self.display_name = name
        self.bot = bot
        self.mention = f"<@{id_val}>"


class DummyMessage:
    def __init__(self, id_val: int, author: DummyUser, channel: DummyChannel, content: str, reference=None):
        self.id = id_val
        self.author = author
        self.channel = channel
        self.guild = MagicMock()
        self.content = content
        self.webhook_id = None
        self.reference = reference
        self.reply = AsyncMock()


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.user.id = 999999999
    bot.get_valid_prefixes = AsyncMock(return_value=["!"])
    bot.get_cog = MagicMock(return_value=None)
    return bot


@pytest.fixture
def cog(mock_bot):
    with patch("redbot.core.Config.get_conf") as mock_get_conf:
        # Mock Redbot Config
        mock_conf = MagicMock()
        mock_get_conf.return_value = mock_conf
        cog_instance = SabbyTranslate(mock_bot)
        return cog_instance


def test_is_ticket_channel_detection(cog):
    ticket_channels = [
        DummyChannel(1001, "[VYS]🎫 yacinemechhad7 | Onimusha Way of the Sword", "steam"),
        DummyChannel(1002, "[vys] 🎫 yeezyzzx | Onimusha Way of the Sword", "steam"),
        DummyChannel(1003, "🎮 shishoestbb4747 | F1 24", "steam"),
        DummyChannel(1004, "🎫 moriark3479 | Crimson Desert Enhanced", "tickets"),
        DummyChannel(1005, "ticket-5541", "support"),
        DummyChannel(1006, "[VYS] daniseg4 | Crimson Desert Enhanced", "orders"),
    ]
    for ch in ticket_channels:
        assert cog.is_ticket_channel(ch) is True, f"Failed for {ch.name}"

    non_ticket_channels = [
        DummyChannel(2001, "general", "text channels"),
        DummyChannel(2002, "staff-chat", "staff"),
        DummyChannel(2003, "announcements", "info"),
        DummyChannel(2004, "bot-commands", "bots"),
    ]
    for ch in non_ticket_channels:
        assert cog.is_ticket_channel(ch) is False, f"False positive for {ch.name}"


@pytest.mark.asyncio
async def test_ticket_live_translation_first_lang_always_english(cog):
    ticket_ch = DummyChannel(3001, "[VYS]🎫 test_user | Game", "steam")

    # In a ticket, setting first_language to Arabic and second_language to None
    # should ensure first_lang is 'en' and second_lang is 'ar'
    channel_conf = {}

    mock_channel_config = MagicMock()
    async def fake_get_all():
        return dict(channel_conf)
    async def fake_set(data):
        channel_conf.clear()
        channel_conf.update(data)

    mock_channel_config.all = AsyncMock(side_effect=fake_get_all)
    mock_channel_config.set = AsyncMock(side_effect=fake_set)
    cog.config.channel = MagicMock(return_value=mock_channel_config)

    interaction = MagicMock()
    interaction.channel = ticket_ch
    interaction.response.send_message = AsyncMock()

    action_choice = MagicMock()
    action_choice.value = "start"

    # Start livetranslate specifying Arabic as first_language
    await cog.slash_livetranslate.callback(
        cog,
        interaction=interaction,
        action=action_choice,
        first_language="Arabic",
        second_language=None,
    )

    assert channel_conf.get("enabled") is True
    assert channel_conf.get("first_lang") == "en"
    assert channel_conf.get("second_lang") == "ar"
    assert channel_conf.get("is_ticket") is True
    assert channel_conf.get("locked_second_lang") is True
    assert channel_conf.get("ticket_initial_second_lang") == "ar"
    assert channel_conf.get("prompt_new_users") is False


@pytest.mark.asyncio
async def test_ticket_secondary_language_cannot_be_changed_after_activating(cog):
    ticket_ch = DummyChannel(3002, "🎫 support_user | Game", "tickets")

    channel_conf = {
        "enabled": True,
        "first_lang": "en",
        "second_lang": "ar",
        "reply_translate": True,
        "prompt_new_users": False,
        "is_ticket": True,
        "ticket_initial_second_lang": "ar",
        "locked_second_lang": True,
    }

    mock_channel_config = MagicMock()
    mock_channel_config.all = AsyncMock(return_value=dict(channel_conf))
    cog.config.channel = MagicMock(return_value=mock_channel_config)

    interaction = MagicMock()
    interaction.channel = ticket_ch
    interaction.response.send_message = AsyncMock()

    action_choice = MagicMock()
    action_choice.value = "start"

    # Attempt to change secondary language to Somali or Spanish while active
    await cog.slash_livetranslate.callback(
        cog,
        interaction=interaction,
        action=action_choice,
        first_language="English",
        second_language="Spanish",
    )

    # Must be rejected
    interaction.response.send_message.assert_called_once()
    call_args = interaction.response.send_message.call_args[0][0]
    assert "already active in this ticket" in call_args
    assert "locked once activated" in call_args
    assert channel_conf["second_lang"] == "ar"


@pytest.mark.asyncio
async def test_ticket_on_message_suppresses_third_language_translation(cog):
    ticket_ch = DummyChannel(3003, "[VYS]🎫 yacinemechhad7 | Game", "steam")

    channel_conf = {
        "enabled": True,
        "first_lang": "en",
        "second_lang": "ar",
        "reply_translate": True,
        "prompt_new_users": False,
        "is_ticket": True,
        "ticket_initial_second_lang": "ar",
        "locked_second_lang": True,
    }

    mock_channel_config = MagicMock()
    mock_channel_config.all = AsyncMock(return_value=dict(channel_conf))
    cog.config.channel = MagicMock(return_value=mock_channel_config)
    cog.translator.translate = AsyncMock(return_value="Translated message")

    user_staff = DummyUser(4001, "woodyy")
    user_vivvid = DummyUser(4002, "vivvid_")

    # 1. Vivvid sends text that detects as Somali ('so')
    msg_somali = DummyMessage(5001, user_vivvid, ticket_ch, "3a6tni da8ee8a")
    with patch("sabbytranslate.sabbytranslate.detect_language", return_value="so"):
        await cog.on_message(msg_somali)
        # Should NOT translate in ticket channels!
        msg_somali.reply.assert_not_called()

    # 2. Woodyy sends text that detects as Swedish ('sv')
    msg_swedish = DummyMessage(5002, user_staff, ticket_ch, "vivvid...")
    with patch("sabbytranslate.sabbytranslate.detect_language", return_value="sv"):
        await cog.on_message(msg_swedish)
        # Should NOT translate in ticket channels!
        msg_swedish.reply.assert_not_called()

    # 3. User sends Arabic text ('ar') -> should translate to English ('en')
    msg_arabic = DummyMessage(5003, user_vivvid, ticket_ch, "مرحبا شكرا لك")
    with patch("sabbytranslate.sabbytranslate.detect_language", return_value="ar"):
        await cog.on_message(msg_arabic)
        msg_arabic.reply.assert_called_once()
        cog.translator.translate.assert_called_with("مرحبا شكرا لك", target="en", source="ar")

    # 4. Staff sends English text ('en') -> should translate to Arabic ('ar')
    msg_english = DummyMessage(5004, user_staff, ticket_ch, "then try again")
    with patch("sabbytranslate.sabbytranslate.detect_language", return_value="en"):
        await cog.on_message(msg_english)
        msg_english.reply.assert_called_once()
        cog.translator.translate.assert_called_with("then try again", target="ar", source="en")


@pytest.mark.asyncio
async def test_ticket_no_language_prompt_view(cog):
    ticket_ch = DummyChannel(3004, "ticket-109", "tickets")

    channel_conf = {
        "enabled": True,
        "first_lang": "en",
        "second_lang": "ar",
        "reply_translate": True,
        "prompt_new_users": True,  # Even if true, ticket channel must suppress
        "is_ticket": True,
        "ticket_initial_second_lang": "ar",
        "locked_second_lang": True,
    }

    mock_channel_config = MagicMock()
    mock_channel_config.all = AsyncMock(return_value=dict(channel_conf))
    cog.config.channel = MagicMock(return_value=mock_channel_config)
    cog.config.user = MagicMock(return_value=MagicMock(preferred_language=AsyncMock(return_value=None)))

    user_new = DummyUser(4003, "new_member")
    msg = DummyMessage(5005, user_new, ticket_ch, "something foreign")
    ticket_ch.send = AsyncMock()

    with patch("sabbytranslate.sabbytranslate.detect_language", return_value="so"):
        await cog.on_message(msg)
        # Should not prompt user in ticket
        assert user_new.id not in cog._prompted_users
        ticket_ch.send.assert_not_called()


@pytest.mark.asyncio
async def test_ticket_prefix_command_locking(cog):
    ticket_ch = DummyChannel(3005, "[VYS]🎫 yeezyzzx | Game", "steam")

    channel_conf = {
        "enabled": True,
        "first_lang": "en",
        "second_lang": "ar",
        "reply_translate": True,
        "prompt_new_users": False,
        "is_ticket": True,
        "ticket_initial_second_lang": "ar",
        "locked_second_lang": True,
    }

    mock_channel_config = MagicMock()
    mock_channel_config.all = AsyncMock(return_value=dict(channel_conf))
    cog.config.channel = MagicMock(return_value=mock_channel_config)

    ctx = MagicMock()
    ctx.channel = ticket_ch
    ctx.send = AsyncMock()

    # Attempt to change to Portuguese via prefix command
    await cog.prefix_livetranslate.callback(cog, ctx, action="start", first_or_second_lang="Portuguese")
    ctx.send.assert_called_once()
    assert "already active in this ticket" in ctx.send.call_args[0][0]

