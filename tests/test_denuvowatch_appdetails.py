import pytest
import sys
from unittest.mock import patch, MagicMock

if "bs4" not in sys.modules:
    sys.modules["bs4"] = MagicMock()

from denuvowatch.denuvowatch import fetch_app_details, get_game_snapshot


def test_fetch_app_details_exact_key_match():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "730": {
            "success": True,
            "data": {
                "name": "Counter-Strike 2",
                "steam_appid": 730,
                "drm_notice": "",
            }
        }
    }
    with patch("requests.get", return_value=fake_response):
        details = fetch_app_details(730)
        assert details.get("name") == "Counter-Strike 2"
        assert details.get("steam_appid") == 730


def test_fetch_app_details_divergent_key_match():
    # Valve recently started returning different keys for appdetails requests
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "2678630": {
            "success": True,
            "data": {
                "name": "Counter-Strike 2",
                "steam_appid": 730,
                "drm_notice": "",
            }
        }
    }
    with patch("requests.get", return_value=fake_response):
        details = fetch_app_details(730)
        assert details.get("name") == "Counter-Strike 2"
        assert details.get("steam_appid") == 730


def test_get_game_snapshot_with_divergent_key():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "2441600": {
            "success": True,
            "data": {
                "name": "Cyberpunk 2077",
                "steam_appid": 1091500,
                "drm_notice": "",
                "header_image": "https://example.com/header.jpg",
                "release_date": {"coming_soon": False, "date": "10 Dec, 2020"},
            }
        }
    }
    with patch("requests.get", return_value=fake_response), patch(
        "denuvowatch.denuvowatch.fetch_build_id_only", return_value=("20383525", 1760436232)
    ), patch(
        "denuvowatch.denuvowatch.has_denuvo", return_value=False
    ):
        snapshot = get_game_snapshot(1091500)
        assert snapshot is not None
        assert snapshot["name"] == "Cyberpunk 2077"
        assert snapshot["build_id"] == "20383525"
        assert snapshot["denuvo"] is False
