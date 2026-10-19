"""
Comprehensive unit test suite for the Restrict cog.
Validates content detection regexes, attachment/embed parsing, duration parsing,
and restriction evaluation logic.
"""

import time
from unittest.mock import MagicMock
import pytest

from restrict.restrict import (
    Restrict,
    TENOR_REGEX,
    GIPHY_REGEX,
    GIF_URL_REGEX,
    IMAGE_URL_REGEX,
    VIDEO_URL_REGEX,
    LINK_REGEX,
    INVITE_REGEX,
    parse_duration,
)


class MockAttachment:
    def __init__(self, filename: str, content_type: str = ""):
        self.filename = filename
        self.content_type = content_type


class MockEmbed:
    def __init__(self, emb_type: str = "rich", url: str = None, thumbnail_url: str = None, image_url: str = None):
        self.type = emb_type
        self.url = url
        self.thumbnail = MagicMock(url=thumbnail_url)
        self.image = MagicMock(url=image_url)
        self.video = MagicMock(url=None)


class MockMessage:
    def __init__(self, content: str = "", attachments=None, embeds=None, stickers=None):
        self.content = content
        self.clean_content = content
        self.attachments = attachments or []
        self.embeds = embeds or []
        self.stickers = stickers or []


# =============================================================================
# Regex & Content Detection Tests
# =============================================================================

def test_tenor_regex():
    valid = [
        "https://tenor.com/view/happy-cat-gif-12345678",
        "http://tenor.com/view/dance-123",
        "Check this out: https://media.tenor.com/abc/dance.gif lol",
        "https://c.tenor.com/1234/tenor.gif",
    ]
    for url in valid:
        assert TENOR_REGEX.search(url) is not None, f"Failed matching Tenor URL: {url}"

    invalid = [
        "https://google.com/search?q=tenor",
        "just talking about tenor",
    ]
    for url in invalid:
        assert TENOR_REGEX.search(url) is None, f"Unexpected match for non-Tenor: {url}"


def test_giphy_regex():
    valid = [
        "https://giphy.com/gifs/cat-cute-12345",
        "https://media.giphy.com/media/abc123/giphy.gif",
        "https://i.giphy.com/media/xyz/200.gif",
    ]
    for url in valid:
        assert GIPHY_REGEX.search(url) is not None, f"Failed matching Giphy URL: {url}"


def test_gif_url_regex():
    valid = [
        "https://cdn.example.com/memes/cat.gif",
        "https://i.imgur.com/abc123.gifv",
        "https://images.example.com/test.gif?size=large",
    ]
    for url in valid:
        assert GIF_URL_REGEX.search(url) is not None, f"Failed matching GIF link: {url}"

    invalid = [
        "https://cdn.example.com/memes/cat.png",
        "https://cdn.example.com/memes/video.mp4",
        "gift for you",
    ]
    for url in invalid:
        assert GIF_URL_REGEX.search(url) is None, f"Unexpected match for non-GIF link: {url}"


def test_contains_gif():
    # 1. Tenor Link
    m1 = MockMessage(content="Look at this https://tenor.com/view/cat-gif-99")
    assert Restrict.contains_gif(m1) is True

    # 2. Giphy Link
    m2 = MockMessage(content="https://giphy.com/gifs/dance-123")
    assert Restrict.contains_gif(m2) is True

    # 3. Direct .gif Link
    m3 = MockMessage(content="Check https://images.site.com/anim.gif")
    assert Restrict.contains_gif(m3) is True

    # 4. Attachment .gif
    m4 = MockMessage(attachments=[MockAttachment("funny.gif", "image/gif")])
    assert Restrict.contains_gif(m4) is True

    # 5. Attachment .gifv
    m5 = MockMessage(attachments=[MockAttachment("clip.gifv", "video/mp4")])
    assert Restrict.contains_gif(m5) is True

    # 6. Embed gifv
    m6 = MockMessage(embeds=[MockEmbed(emb_type="gifv", url="https://tenor.com/view/123")])
    assert Restrict.contains_gif(m6) is True

    # 7. Non-GIF message
    m7 = MockMessage(content="Hello world, how are you?")
    assert Restrict.contains_gif(m7) is False


def test_contains_image():
    m1 = MockMessage(content="Here is a photo: https://cdn.site.com/pic.png")
    assert Restrict.contains_image(m1) is True

    m2 = MockMessage(attachments=[MockAttachment("photo.jpg", "image/jpeg")])
    assert Restrict.contains_image(m2) is True

    m3 = MockMessage(content="Just text here")
    assert Restrict.contains_image(m3) is False


def test_contains_video():
    m1 = MockMessage(content="Watch this: https://youtube.com/watch?v=12345")
    assert Restrict.contains_video(m1) is True

    m2 = MockMessage(attachments=[MockAttachment("clip.mp4", "video/mp4")])
    assert Restrict.contains_video(m2) is True

    m3 = MockMessage(content="https://tiktok.com/@user/video/12345")
    assert Restrict.contains_video(m3) is True


def test_contains_link_and_invite():
    m_link = MockMessage(content="Visit https://example.com today")
    assert Restrict.contains_link(m_link) is True
    assert Restrict.contains_invite(m_link) is False

    m_inv = MockMessage(content="Join our server: https://discord.gg/freegames")
    assert Restrict.contains_link(m_inv) is True
    assert Restrict.contains_invite(m_inv) is True


def test_contains_sticker():
    m1 = MockMessage(stickers=[MagicMock(id=12345)])
    assert Restrict.contains_sticker(m1) is True

    m2 = MockMessage(stickers=[])
    assert Restrict.contains_sticker(m2) is False


# =============================================================================
# Duration Parser Tests
# =============================================================================

def test_parse_duration():
    now = time.time()

    # None or empty
    exp, reason = parse_duration(None)
    assert exp is None and reason is None

    # Permanent keywords
    exp, reason = parse_duration("perm")
    assert exp is None and reason is None
    exp, reason = parse_duration("forever")
    assert exp is None and reason is None

    # 30m with reason
    exp, reason = parse_duration("30m Excessive spamming")
    assert exp is not None
    assert abs((exp - now) - 1800) < 5
    assert reason == "Excessive spamming"

    # 2h without reason
    exp, reason = parse_duration("2h")
    assert exp is not None
    assert abs((exp - now) - 7200) < 5
    assert reason is None

    # 1d with reason
    exp, reason = parse_duration("1d Rule 3 violation")
    assert exp is not None
    assert abs((exp - now) - 86400) < 5
    assert reason == "Rule 3 violation"

    # Pure reason without duration
    exp, reason = parse_duration("Trolling in general")
    assert exp is None
    assert reason == "Trolling in general"


# =============================================================================
# Content Violation Evaluator
# =============================================================================

def test_evaluate_content_violation():
    bot_mock = MagicMock()
    cog = Restrict(bot_mock)

    gif_msg = MockMessage(content="https://tenor.com/view/cat-123")
    img_msg = MockMessage(content="https://site.com/pic.png")
    text_msg = MockMessage(content="Normal hello")

    # GIF restriction
    assert cog.evaluate_content_violation("gif", gif_msg) is True
    assert cog.evaluate_content_violation("gif", img_msg) is False
    assert cog.evaluate_content_violation("gif", text_msg) is False

    # Image restriction
    assert cog.evaluate_content_violation("image", img_msg) is True
    assert cog.evaluate_content_violation("image", text_msg) is False

    # All restriction
    assert cog.evaluate_content_violation("all", gif_msg) is True
    assert cog.evaluate_content_violation("all", img_msg) is True
    assert cog.evaluate_content_violation("all", text_msg) is False


def test_cache_and_expiration():
    bot_mock = MagicMock()
    cog = Restrict(bot_mock)

    gid = 12345
    uid = 67890
    now = time.time()

    # Active permanent restriction
    cog._cache[gid] = {
        "users": {
            uid: {"gif": {"expires": None, "reason": "Test perm"}}
        },
        "channels": {},
        "roles": {},
    }

    assert uid in cog._cache[gid]["users"]
    assert "gif" in cog._cache[gid]["users"][uid]
    assert cog._cache[gid]["users"][uid]["gif"]["expires"] is None

    # Active temporary restriction
    cog._cache[gid]["users"][uid]["image"] = {"expires": now + 3600, "reason": "1h test"}
    assert cog._cache[gid]["users"][uid]["image"]["expires"] > now

    # Expired restriction
    cog._cache[gid]["users"][uid]["video"] = {"expires": now - 10, "reason": "expired"}
    assert cog._cache[gid]["users"][uid]["video"]["expires"] < now


def test_escalation_tracking():
    bot_mock = MagicMock()
    cog = Restrict(bot_mock)

    gid = 11111
    uid = 22222
    now = time.time()

    cog._violations[gid] = {
        uid: [now - 200, now - 100, now - 50]
    }

    # Verify sliding window filters old timestamps
    window = 150  # 2.5 minutes
    valid_violations = [t for t in cog._violations[gid][uid] if (now - t) <= window]
    assert len(valid_violations) == 2  # now-100 and now-50 are inside 150s window
