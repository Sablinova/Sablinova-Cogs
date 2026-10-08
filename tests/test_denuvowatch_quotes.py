import pytest
from denuvowatch.denuvowatch import normalize_game_name


def test_normalize_game_name_strips_quotes():
    assert normalize_game_name('"The Bus"') == "the bus"
    assert normalize_game_name("'The Bus'") == "the bus"
    assert normalize_game_name('“The Bus”') == "the bus"
    assert normalize_game_name("The Bus") == "the bus"
    assert normalize_game_name('"The Bus"') == normalize_game_name("The Bus")


def test_normalize_game_name_punctuation_and_trademarks():
    assert normalize_game_name("ELDEN RING™ III") == "elden ring 3"
    assert normalize_game_name('"ELDEN RING™ III"') == "elden ring 3"
