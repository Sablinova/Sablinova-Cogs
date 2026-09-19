import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from sabdownloader.sabdownloader import SabDownloader, _detect_platform


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
        mock_conf = MagicMock()
        mock_get_conf.return_value = mock_conf
        cog_instance = SabDownloader(mock_bot)
        return cog_instance


def test_detect_platform():
    test_cases = [
        ("https://www.instagram.com/p/Dc5jfTtEv4T/", "Instagram"),
        ("https://instagram.com/p/Dc5jfTtEv4T/", "Instagram"),
        ("https://instagr.am/p/Dc5jfTtEv4T/", "Instagram"),
        ("https://m.instagram.com/reel/12345/", "Instagram"),
        ("https://www.facebook.com/watch?v=12345", "Facebook"),
        ("https://facebook.com/story.php?story_fbid=123", "Facebook"),
        ("https://fb.watch/xyz123/", "Facebook"),
        ("https://fb.com/user/posts/123", "Facebook"),
        ("https://m.facebook.com/story.php?id=1", "Facebook"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "YouTube"),
        ("https://youtu.be/dQw4w9WgXcQ", "YouTube"),
        ("https://x.com/user/status/123456", "Twitter/X"),
        ("https://twitter.com/user/status/123456", "Twitter/X"),
        ("https://www.tiktok.com/@user/video/123456", "TikTok"),
        ("https://www.reddit.com/r/funny/comments/12345", "Reddit"),
        ("https://v.redd.it/abc123xyz", "Reddit"),
        ("https://unknownsite.com/media/file.mp4", "unknownsite.com"),
    ]
    for url, expected in test_cases:
        assert _detect_platform(url) == expected, f"Failed for {url}"


def test_embed_rules_decision(cog):
    def should_use_embed(platform: str, files: list[str]) -> bool:
        return (
            platform in ("Facebook", "Instagram")
            and bool(files)
            and all(cog._is_image(fp) for fp in files)
        )

    # Instagram photos -> Embed
    assert should_use_embed("Instagram", ["photo1.jpg", "photo2.png"]) is True
    assert should_use_embed("Instagram", ["single.webp"]) is True

    # Facebook photos -> Embed
    assert should_use_embed("Facebook", ["photo1.jpg", "photo2.jpeg"]) is True

    # Instagram videos / reels -> NO Embed
    assert should_use_embed("Instagram", ["video.mp4"]) is False
    assert should_use_embed("Instagram", ["photo1.jpg", "video.mp4"]) is False

    # Facebook videos -> NO Embed
    assert should_use_embed("Facebook", ["reel.mp4"]) is False
    assert should_use_embed("Facebook", ["photo1.jpg", "video.mp4"]) is False

    # YouTube videos -> NO Embed
    assert should_use_embed("YouTube", ["video.mp4"]) is False
    assert should_use_embed("YouTube", ["audio.mp3"]) is False

    # TikTok videos -> NO Embed
    assert should_use_embed("TikTok", ["tiktok.mp4"]) is False

    # Twitter/X photos and videos -> NO Embed
    assert should_use_embed("Twitter/X", ["photo.jpg"]) is False
    assert should_use_embed("Twitter/X", ["video.mp4"]) is False

    # Reddit photos and videos -> NO Embed
    assert should_use_embed("Reddit", ["meme.png"]) is False
    assert should_use_embed("Reddit", ["clip.mp4"]) is False

    # Empty files list -> NO Embed
    assert should_use_embed("Instagram", []) is False
    assert should_use_embed("Facebook", []) is False


@pytest.mark.asyncio
async def test_handle_file_upload_embed_behavior(cog, tmp_path):
    from sabdownloader.sabdownloader import ProgressTracker

    img1 = tmp_path / "img1.jpg"
    img1.write_bytes(b"fake image 1")
    img2 = tmp_path / "img2.png"
    img2.write_bytes(b"fake image 2")
    vid = tmp_path / "video.mp4"
    vid.write_bytes(b"fake video")

    ctx = MagicMock()
    ctx.guild = MagicMock()
    ctx.guild.filesize_limit = 25 * 1024 * 1024
    ctx.guild.name = "Test Guild"
    ctx.channel.name = "general"
    ctx.author.display_name = "TestUser"
    ctx.author.display_avatar.url = "https://example.com/avatar.png"
    ctx.author.mention = "@TestUser"
    ctx.author.id = 12345
    ctx.interaction = None
    ctx.send = AsyncMock()

    status_msg = MagicMock()
    status_msg.delete = AsyncMock()

    guild_config = {
        "anondrop_enabled": False,
        "delete_command": False,
    }

    cog.config.anondrop_userkey = AsyncMock(return_value="")
    cog.config.delete_command = AsyncMock(return_value=False)
    cog.config.log_channel = AsyncMock(return_value=None)

    # 1. Instagram photos: should send with embed
    ctx.send.reset_mock()
    tracker = ProgressTracker()
    await cog._handle_file_upload(
        ctx=ctx,
        files=[str(img1), str(img2)],
        status_msg=status_msg,
        tracker=tracker,
        url="https://instagram.com/p/123",
        platform="Instagram",
        guild_config=guild_config,
    )
    assert ctx.send.called
    kwargs = ctx.send.call_args.kwargs
    assert "embed" in kwargs and kwargs["embed"] is not None

    # 2. Instagram video: should send WITHOUT embed
    ctx.send.reset_mock()
    tracker = ProgressTracker()
    await cog._handle_file_upload(
        ctx=ctx,
        files=[str(vid)],
        status_msg=status_msg,
        tracker=tracker,
        url="https://instagram.com/reel/123",
        platform="Instagram",
        guild_config=guild_config,
    )
    assert ctx.send.called
    kwargs = ctx.send.call_args.kwargs
    assert "embed" not in kwargs

    # 3. YouTube video: should send WITHOUT embed
    ctx.send.reset_mock()
    tracker = ProgressTracker()
    await cog._handle_file_upload(
        ctx=ctx,
        files=[str(vid)],
        status_msg=status_msg,
        tracker=tracker,
        url="https://youtube.com/watch?v=123",
        platform="YouTube",
        guild_config=guild_config,
    )
    assert ctx.send.called
    kwargs = ctx.send.call_args.kwargs
    assert "embed" not in kwargs

    # 4. Twitter photo: should send WITHOUT embed
    ctx.send.reset_mock()
    tracker = ProgressTracker()
    await cog._handle_file_upload(
        ctx=ctx,
        files=[str(img1)],
        status_msg=status_msg,
        tracker=tracker,
        url="https://x.com/user/status/123",
        platform="Twitter/X",
        guild_config=guild_config,
    )
    assert ctx.send.called
    kwargs = ctx.send.call_args.kwargs
    assert "embed" not in kwargs

