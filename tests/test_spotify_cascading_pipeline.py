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
