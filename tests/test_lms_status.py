"""Tests for lms_status._parse_status.

query_lms_status() opens a real TCP socket, so it is not tested here.
_parse_status() operates on a plain string and is fully testable in isolation.
"""

from pytest import approx

from huesync.lms_status import LmsPlayerStatus, _parse_status


def _build_response(*fields: str) -> str:
    """Build a minimal URL-decoded LMS status response string."""
    return "aa:bb:cc:dd:ee:ff status - 1 tags: " + " ".join(fields)


def test_parse_time():
    text = _build_response("time:102.647383")
    result = _parse_status(text)
    assert result.time == approx(102.647383)


def test_parse_sync_master():
    text = _build_response("time:30.0", "sync_master:11:22:33:44:55:66")
    result = _parse_status(text)
    assert result.sync_master == "11:22:33:44:55:66"


def test_parse_sync_master_empty_means_none():
    """LMS returns sync_master: with an empty value when the player is standalone."""
    text = _build_response("time:10.0", "sync_master:")
    result = _parse_status(text)
    assert result.sync_master is None


def test_parse_sync_slaves_single():
    text = _build_response("sync_slaves:aa:bb:cc:00:11:22")
    result = _parse_status(text)
    assert result.sync_slaves == ["aa:bb:cc:00:11:22"]


def test_parse_sync_slaves_multiple():
    text = _build_response("sync_slaves:aa:bb:cc:00:11:22,dd:ee:ff:00:11:22")
    result = _parse_status(text)
    assert result.sync_slaves == ["aa:bb:cc:00:11:22", "dd:ee:ff:00:11:22"]


def test_parse_sync_slaves_empty():
    text = _build_response("sync_slaves:")
    result = _parse_status(text)
    assert result.sync_slaves == []


def test_parse_standalone_player():
    """A standalone player has no sync_master or sync_slaves tokens at all."""
    text = _build_response("time:55.1", "player_name:HueSync")
    result = _parse_status(text)
    assert result.time == approx(55.1)
    assert result.player_name == "HueSync"
    assert result.sync_master is None
    assert result.sync_slaves == []


def test_parse_player_name_url_encoded_space():
    """Player names with spaces arrive URL-encoded; they must be decoded correctly."""
    text = _build_response("player_name:Study%20Room")
    result = _parse_status(text)
    assert result.player_name == "Study Room"


def test_parse_player_name_sonos_format():
    """SONOS::Room names arrive with colons encoded so they stay as one token."""
    text = _build_response("player_name:SONOS%3A%3AStudy")
    result = _parse_status(text)
    assert result.player_name == "SONOS::Study"


def test_parse_mac_in_player_id_not_confused_with_fields():
    """The player MAC at the start of the response must not be parsed as a field.

    partition(":") on "aa:bb:cc:dd:ee:ff" yields key="aa", which is not a
    recognised field name and is skipped.
    """
    text = "aa:bb:cc:dd:ee:ff status - 1 tags: time:1.0"
    result = _parse_status(text)
    assert result.time == approx(1.0)
    assert result.sync_master is None


def test_parse_defaults_when_empty():
    result = _parse_status("")
    assert result == LmsPlayerStatus()
