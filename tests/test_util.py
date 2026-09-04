from huesync.util import format_player_name


def test_format_player_name_sonos():
    assert format_player_name("SONOS::Study") == "Study (Sonos)"


def test_format_player_name_no_separator():
    assert format_player_name("HueSync") == "HueSync"


def test_format_player_name_preserves_room_case():
    assert format_player_name("SONOS::Living Room") == "Living Room (Sonos)"


def test_format_player_name_type_capitalised():
    assert format_player_name("AIRPLAY::Kitchen") == "Kitchen (Airplay)"
