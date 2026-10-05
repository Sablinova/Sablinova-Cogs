import pytest
from unittest.mock import AsyncMock, patch
from sabdownloader.sabdownloader import _download_spotify, SabDownloader
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_cascading_pipeline_tier1_success():
    with patch("sabdownloader.sabdownloader._download_spotify_spotiflac", new_callable=AsyncMock) as m_t1, \
         patch("sabdownloader.sabdownloader._download_spotify_deezer", new_callable=AsyncMock) as m_t2:
        m_t1.return_value = (["/tmp/test.flac"], {"spotiflac_format": "FLAC", "spotiflac_service": "tidal"})

        files, meta = await _download_spotify("https://open.spotify.com/track/123", "/tmp/test")

        assert files == ["/tmp/test.flac"]
        assert meta["spotiflac_format"] == "FLAC"
        assert meta["spotiflac_service"] == "tidal"
        assert m_t1.called
        assert not m_t2.called


@pytest.mark.asyncio
async def test_cascading_pipeline_tier1_fail_tier2_success():
    with patch("sabdownloader.sabdownloader._download_spotify_spotiflac", new_callable=AsyncMock) as m_t1, \
         patch("sabdownloader.sabdownloader._download_spotify_deezer", new_callable=AsyncMock) as m_t2, \
         patch("sabdownloader.sabdownloader._download_spotify_spotdl", new_callable=AsyncMock) as m_t4:
        m_t1.side_effect = RuntimeError("Tidal proxy 401")
        m_t2.return_value = (["/tmp/deezer.flac"], {"spotiflac_format": "FLAC", "spotiflac_service": "deezer"})

        files, meta = await _download_spotify(
            "https://open.spotify.com/track/123",
            "/tmp/test",
            deezer_arl="dummy_arl",
        )

        assert files == ["/tmp/deezer.flac"]
        assert meta["spotiflac_format"] == "FLAC"
        assert meta["spotiflac_service"] == "deezer"
        assert m_t1.called
        assert m_t2.called
        assert not m_t4.called


@pytest.mark.asyncio
async def test_cascading_pipeline_lossless_fail_spotdl_fallback():
    with patch("sabdownloader.sabdownloader._download_spotify_spotiflac", new_callable=AsyncMock) as m_t1, \
         patch("sabdownloader.sabdownloader._download_spotify_deezer", new_callable=AsyncMock) as m_t2, \
         patch("sabdownloader.sabdownloader._download_spotify_qobuz", new_callable=AsyncMock) as m_t3, \
         patch("sabdownloader.sabdownloader._download_spotify_spotdl", new_callable=AsyncMock) as m_t4:
        m_t1.side_effect = RuntimeError("SpotiFLAC failed")
        m_t2.side_effect = RuntimeError("Deezer ARL not configured")
        m_t3.side_effect = RuntimeError("Qobuz token not configured")
        m_t4.return_value = (["/tmp/song.mp3"], {"spotiflac_format": "MP3", "spotiflac_service": "spotdl"})

        files, meta = await _download_spotify("https://open.spotify.com/track/123", "/tmp/test")

        assert files == ["/tmp/song.mp3"]
        assert meta["spotiflac_format"] == "MP3"
        assert meta["spotiflac_service"] == "spotdl"
        assert m_t1.called
        assert m_t2.called
        assert m_t3.called
        assert m_t4.called


@pytest.mark.asyncio
async def test_cascading_pipeline_spotdl_fail_ytdlp_fallback():
    with patch("sabdownloader.sabdownloader._download_spotify_spotiflac", new_callable=AsyncMock) as m_t1, \
         patch("sabdownloader.sabdownloader._download_spotify_deezer", new_callable=AsyncMock) as m_t2, \
         patch("sabdownloader.sabdownloader._download_spotify_qobuz", new_callable=AsyncMock) as m_t3, \
         patch("sabdownloader.sabdownloader._download_spotify_spotdl", new_callable=AsyncMock) as m_t4, \
         patch("sabdownloader.sabdownloader._download_spotify_ytdlp", new_callable=AsyncMock) as m_t5:
        m_t1.side_effect = RuntimeError("SpotiFLAC failed")
        m_t2.side_effect = RuntimeError("Deezer failed")
        m_t3.side_effect = RuntimeError("Qobuz failed")
        m_t4.side_effect = RuntimeError("spotdl bot detection error")
        m_t5.return_value = (["/tmp/ytdlp.mp3"], {"spotiflac_format": "MP3", "spotiflac_service": "yt-dlp"})

        files, meta = await _download_spotify("https://open.spotify.com/track/123", "/tmp/test")

        assert files == ["/tmp/ytdlp.mp3"]
        assert meta["spotiflac_format"] == "MP3"
        assert meta["spotiflac_service"] == "yt-dlp"
        assert m_t5.called


def test_music_commands_registered():
    bot = MagicMock()
    cog = SabDownloader(bot)
    assert hasattr(cog, "sd_musicstatus")
    assert hasattr(cog, "sd_deezerarl")
    assert hasattr(cog, "sd_qobuztoken")
    assert hasattr(cog, "sd_spotify")
    assert hasattr(cog, "sd_spotiflacverify")
    assert hasattr(cog, "sd_spotiflacgrant")
    assert hasattr(cog, "sd_spotiflacsession")
    assert hasattr(cog, "sd_spotiflacstatus")


def test_find_spotiflac_binary():
    from sabdownloader.sabdownloader import _find_spotiflac_binary
    with patch("os.path.isfile", return_value=True), patch("os.access", return_value=True):
        found = _find_spotiflac_binary()
        assert found is not None


@pytest.mark.asyncio
async def test_start_spotiflac_verification():
    bot = MagicMock()
    cog = SabDownloader(bot)
    ctx = MagicMock()
    ctx.typing = MagicMock()
    ctx.clean_prefix = "!"
    ctx.send = AsyncMock()

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(
        b'{"challenge_url": "https://verify.spotbye.qzz.io/challenge?id=test", "install_id": "test_id"}',
        b""
    ))

    with patch("sabdownloader.sabdownloader._find_spotiflac_binary", return_value="/tmp/spotiflac_cli"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
        await cog._start_spotiflac_verification(ctx)
        assert ctx.send.called
        call_kwargs = ctx.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "SpotiFLAC True FLAC Verification" in embed.title
        assert "https://verify.spotbye.qzz.io/challenge" in embed.description


@pytest.mark.asyncio
async def test_complete_spotiflac_grant():
    bot = MagicMock()
    cog = SabDownloader(bot)
    ctx = MagicMock()
    ctx.typing = MagicMock()
    ctx.send = AsyncMock()

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(
        b'{"session_id": "sess_1234567890abcdef", "expires_at": "2026-11-01T00:00:00Z"}',
        b""
    ))

    with patch("sabdownloader.sabdownloader._find_spotiflac_binary", return_value="/tmp/spotiflac_cli"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
        await cog._complete_spotiflac_grant(ctx, "grant_token_123")
        assert ctx.send.called
        call_kwargs = ctx.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "Activated" in embed.title
        assert "sess_1234567890" in embed.description

