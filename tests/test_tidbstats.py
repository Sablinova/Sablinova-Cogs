import pytest
from tidbstats.tidbstats import format_ms, make_progress_bar


def test_format_ms_none_and_negative():
    assert format_ms(None) == "00:00"
    assert format_ms(-500) == "00:00"
    assert format_ms(0) == "00:00"


def test_format_ms_standard_durations():
    # 24,716 ms = 24 seconds
    assert format_ms(24716) == "00:24"
    # 90,000 ms = 1 minute 30 seconds
    assert format_ms(90000) == "01:30"
    # 3,665,000 ms = 1 hour 1 minute 5 seconds
    assert format_ms(3665000) == "01:01:05"


def test_make_progress_bar():
    # 0%
    bar_0 = make_progress_bar(0.0, length=10)
    assert "[`░░░░░░░░░░`] 0.0%" in bar_0

    # 50%
    bar_50 = make_progress_bar(0.5, length=10)
    assert "[`█████░░░░░`] 50.0%" in bar_50

    # 100%
    bar_100 = make_progress_bar(1.0, length=10)
    assert "[`██████████`] 100.0%" in bar_100

    # Clamping
    bar_over = make_progress_bar(1.5, length=10)
    assert "[`██████████`] 100.0%" in bar_over

    bar_under = make_progress_bar(-0.5, length=10)
    assert "[`░░░░░░░░░░`] 0.0%" in bar_under


def test_v4_stats_payload_parsing():
    sample_v4 = {
        "total_media": 9067,
        "total_shows": 8749,
        "total_submissions": 973068,
        "accepted_timestamps": 499331,
        "contributors": 664,
        "top_media": [
            {"tvdb_id": 71663, "type": "tv", "submissions": 2551},
            {"tvdb_id": 81797, "type": "tv", "submissions": 2530},
        ],
        "total_time_saved_ms": 65079044871,
        "total_movies": 323,
        "total_episodes": 218741,
    }

    ratio = sample_v4["accepted_timestamps"] / sample_v4["total_submissions"]
    assert 0.51 < ratio < 0.52
    hours_saved = int(sample_v4["total_time_saved_ms"] / (1000 * 60 * 60))
    assert hours_saved == 18077
