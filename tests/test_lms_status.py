"""Tests for lms_status._parse_status.

query_lms_status() opens a real TCP socket, so it is not tested here.
_parse_status() operates on a plain string and is fully testable in isolation.

LMS CLI responses are fully URL-encoded, including the colon that separates
key from value (%3A).  The _build_response() helper below uses the decoded
form (literal ":") to keep older tests readable; _build_encoded_response()
produces the actual wire format.
"""

from pytest import approx

from huesync.lms_status import LmsPlayerStatus, _parse_status


def _build_response(*fields: str) -> str:
    """Minimal LMS status response with literal (decoded) key:value colons."""
    return "aa:bb:cc:dd:ee:ff status - 1 tags: " + " ".join(fields)


def _build_encoded_response(*fields: str) -> str:
    """Minimal LMS status response in the actual wire format (key%3Avalue)."""
    from urllib.parse import quote
    encoded = [quote(f, safe=" ").replace(" ", "%20") for f in fields]
    return "aa%3Abb%3Acc%3Add%3Aee%3Aff status 0 1 " + " ".join(encoded)


# ---------------------------------------------------------------------------
# Decoded-separator format (still handled correctly)
# ---------------------------------------------------------------------------

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


def test_parse_mac_in_player_id_not_confused_with_fields():
    """The player MAC at the start of the response must not be parsed as a field."""
    text = "aa:bb:cc:dd:ee:ff status - 1 tags: time:1.0"
    result = _parse_status(text)
    assert result.time == approx(1.0)
    assert result.sync_master is None


def test_parse_defaults_when_empty():
    result = _parse_status("")
    assert result == LmsPlayerStatus()


# ---------------------------------------------------------------------------
# Fully URL-encoded format (actual LMS wire format: key%3Avalue)
# ---------------------------------------------------------------------------

def test_parse_fully_encoded_response():
    """Regression: LMS encodes %3A as the key:value separator.

    This is the format confirmed in production — the parser must decode each
    token in full before splitting on ':', not search for a literal ':'.
    """
    text = (
        "02%3Aae%3A8a%3Ab0%3A24%3A9b status 0 1 "
        "player_name%3AHueSync "
        "sync_master%3A94%3A9f%3A3e%3Afa%3Aba%3A66 "
        "sync_slaves%3A02%3Aae%3A8a%3Ab0%3A24%3A9b"
    )
    result = _parse_status(text)
    assert result.player_name == "HueSync"
    assert result.sync_master == "94:9f:3e:fa:ba:66"
    assert result.sync_slaves == ["02:ae:8a:b0:24:9b"]


def test_parse_encoded_time():
    text = _build_encoded_response("time:102.647383")
    result = _parse_status(text)
    assert result.time == approx(102.647383)


def test_parse_encoded_player_name_with_space():
    """Player names with spaces arrive doubly-encoded: space = %20."""
    text = _build_encoded_response("player_name:Study Room")
    result = _parse_status(text)
    assert result.player_name == "Study Room"


def test_parse_encoded_sync_master_mac():
    """Sync master MAC colons are encoded as %3A in the value."""
    text = _build_encoded_response("sync_master:94:9f:3e:fa:ba:66")
    result = _parse_status(text)
    assert result.sync_master == "94:9f:3e:fa:ba:66"


def test_parse_encoded_sync_slaves_multiple():
    text = _build_encoded_response("sync_slaves:aa:bb:cc:00:11:22,dd:ee:ff:00:11:22")
    result = _parse_status(text)
    assert result.sync_slaves == ["aa:bb:cc:00:11:22", "dd:ee:ff:00:11:22"]
