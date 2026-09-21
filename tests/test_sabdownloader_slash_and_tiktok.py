import pytest
from unittest.mock import MagicMock
from sabdownloader.sabdownloader import (
    SabDownloader,
    _tiktok_extract_post_id,
    _TIKTOK_DOMAINS,
    _TIKTOK_SHORT_DOMAINS,
)


def test_command_structure_is_single_hybrid():
    bot = MagicMock()
    cog = SabDownloader(bot)

    # Check dl command
    assert hasattr(cog, "dl")
    dl_cmd = getattr(cog, "dl")
    assert dl_cmd.name == "dl"
    # Ensure it is not a group with subcommands
    assert not hasattr(dl_cmd, "commands")

    # Check download command
    assert hasattr(cog, "download")
    download_cmd = getattr(cog, "download")
    assert download_cmd.name == "download"
    assert not hasattr(download_cmd, "commands")

    # Check dlaudio and dlhd prefix commands exist
    assert hasattr(cog, "dlaudio")
    assert hasattr(cog, "dlhd")


def test_tiktok_domains_and_extract_post_id():
    assert "vm.tiktok.com" in _TIKTOK_SHORT_DOMAINS
    assert "vt.tiktok.com" in _TIKTOK_SHORT_DOMAINS

    standard_url = "https://www.tiktok.com/@creator/video/7687904796925365525"
    assert _tiktok_extract_post_id(standard_url) == "7687904796925365525"

    query_url = "https://www.tiktok.com/@creator/video/7687904796925365525?is_from_webapp=1"
    assert _tiktok_extract_post_id(query_url) == "7687904796925365525"
