import tempfile
from pathlib import Path

from huesync.models import BridgeConfig, Profile
from huesync.storage import Storage


def make_storage() -> Storage:
    d = tempfile.mkdtemp()
    return Storage(Path(d) / "config.json")


def test_save_and_get_profile_roundtrip():
    storage = make_storage()
    profile = Profile(name="Living room")
    storage.save_profile(profile)

    fetched = storage.get_profile(profile.id)
    assert fetched is not None
    assert fetched.name == "Living room"
    assert fetched.id == profile.id


def test_delete_profile_clears_active_id():
    storage = make_storage()
    profile = Profile(name="Test")
    storage.save_profile(profile)
    storage.set_active_profile_id(profile.id)

    storage.delete_profile(profile.id)

    assert storage.get_profile(profile.id) is None
    assert storage.get_active_profile_id() is None


def test_save_bridge_roundtrip():
    storage = make_storage()
    bridge = BridgeConfig(name="Test bridge", host="192.168.1.50")
    storage.save_bridge(bridge)

    fetched = storage.get_bridge(bridge.id)
    assert fetched is not None
    assert fetched.host == "192.168.1.50"
