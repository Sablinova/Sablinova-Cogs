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
    def should_use_embed(files: list[str]) -> bool:
        return bool(files)

    # Downloads with files include an embed
    assert should_use_embed(["photo1.jpg", "photo2.png"]) is True
    assert should_use_embed(["single.webp"]) is True
    assert should_use_embed(["video.mp4"]) is True
    assert should_use_embed(["song.flac"]) is True
    assert should_use_embed(["audio.mp3"]) is True

    # Empty files list does not send embed
    assert should_use_embed([]) is False


@pytest.mark.asyncio
async def test_handle_file_upload_embed_behavior(cog, tmp_path):
    from sabdownloader.sabdownloader import ProgressTracker

    img1 = tmp_path / "img1.jpg"
    img1.write_bytes(b"fake image 1" * 100)
    img2 = tmp_path / "img2.png"
    img2.write_bytes(b"fake image 2" * 100)
    vid = tmp_path / "video.mp4"
    vid.write_bytes(b"fake video" * 100)
    flac = tmp_path / "track.flac"
    flac.write_bytes(b"fake flac audio" * 200)

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

    # 1. Instagram photos: sends with embed containing requester, file type, and size
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
    embed = kwargs["embed"]
    assert embed.author.name == "TestUser"
    fields = {f.name: f.value for f in embed.fields}
    assert fields.get("Requested by") == "@TestUser"
    assert "JPG" in fields.get("File Type", "") and "PNG" in fields.get("File Type", "")
    assert "Size" in fields

    # 2. Video download: sends with embed
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
    assert "embed" in kwargs and kwargs["embed"] is not None
    embed = kwargs["embed"]
    fields = {f.name: f.value for f in embed.fields}
    assert fields.get("Requested by") == "@TestUser"
    assert fields.get("File Type") == "MP4"
    assert "Size" in fields

    # 3. Audio FLAC download: sends with embed
    ctx.send.reset_mock()
    tracker = ProgressTracker()
    await cog._handle_file_upload(
        ctx=ctx,
        files=[str(flac)],
        status_msg=status_msg,
        tracker=tracker,
        url="https://open.spotify.com/track/123",
        platform="Spotify",
        guild_config=guild_config,
    )
    assert ctx.send.called
    kwargs = ctx.send.call_args.kwargs
    assert "embed" in kwargs and kwargs["embed"] is not None
    embed = kwargs["embed"]
    fields = {f.name: f.value for f in embed.fields}
    assert fields.get("Requested by") == "@TestUser"
    assert fields.get("File Type") == "FLAC"
    assert "Size" in fields

    # 4. Large audio: attempted directly on Discord as lossless FLAC without transcoding
    large_flac = tmp_path / "large_track.flac"
    large_flac.write_bytes(b"large audio data" * 100)
    ctx.send.reset_mock()
    tracker = ProgressTracker()
    with patch("os.path.getsize") as mock_getsize:
        def fake_getsize(path):
            if str(path).endswith(".flac"):
                return 30 * 1024 * 1024
            return 1024
        mock_getsize.side_effect = fake_getsize

        await cog._handle_file_upload(
            ctx=ctx,
            files=[str(large_flac)],
            status_msg=status_msg,
            tracker=tracker,
            url="https://open.spotify.com/track/456",
            platform="Spotify",
            guild_config=guild_config,
        )
        assert ctx.send.called
        kwargs = ctx.send.call_args.kwargs
        assert "embed" in kwargs and kwargs["embed"] is not None
        embed = kwargs["embed"]
        fields = {f.name: f.value for f in embed.fields}
        assert fields.get("File Type") == "FLAC"

