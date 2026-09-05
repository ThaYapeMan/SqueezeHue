"""Tests for PlayerManager shm lifecycle.

These tests create real files under /dev/shm to verify that teardown and
startup cleanup actually remove them — the same path the production code
uses, so there is no seam between test and production behaviour.
"""

import asyncio
from pathlib import Path

import pytest

from huesync.models import Profile
from huesync.player_manager import ActiveSession, PlayerManager
from huesync.storage import Storage


def _make_manager(tmp_path: Path) -> PlayerManager:
    return PlayerManager(Storage(tmp_path / "config.json"))


# ---------------------------------------------------------------------------
# Teardown removes the shm segment
# ---------------------------------------------------------------------------


def test_teardown_removes_shm_segment(tmp_path: Path) -> None:
    """_teardown_session() must unlink /dev/shm/squeezelite-<mac>."""
    mac = "02:ff:00:de:ad:01"
    shm_path = Path(f"/dev/shm/squeezelite-{mac}")
    shm_path.write_bytes(b"")  # create a fake segment

    manager = _make_manager(tmp_path)
    profile = Profile(player_mac=mac)
    session = ActiveSession(profile)

    asyncio.run(manager._teardown_session(session))

    assert not shm_path.exists(), (
        f"_teardown_session() should have removed {shm_path}"
    )


def test_teardown_tolerates_missing_shm(tmp_path: Path) -> None:
    """_teardown_session() must not raise if the shm file is already gone."""
    mac = "02:ff:00:de:ad:02"
    shm_path = Path(f"/dev/shm/squeezelite-{mac}")
    assert not shm_path.exists(), "precondition: file must not exist"

    manager = _make_manager(tmp_path)
    profile = Profile(player_mac=mac)
    session = ActiveSession(profile)

    # Should complete without raising FileNotFoundError.
    asyncio.run(manager._teardown_session(session))


def test_teardown_skips_shm_when_mac_is_empty(tmp_path: Path) -> None:
    """If player_mac is empty (pre-fix profile), teardown must not crash."""
    manager = _make_manager(tmp_path)
    profile = Profile(player_mac="")
    session = ActiveSession(profile)

    asyncio.run(manager._teardown_session(session))


# ---------------------------------------------------------------------------
# Startup orphan cleanup
# ---------------------------------------------------------------------------


def test_cleanup_orphaned_shm_removes_known_mac(tmp_path: Path) -> None:
    """cleanup_orphaned_shm() removes segments whose MAC matches a profile."""
    mac = "02:ff:00:de:ad:03"
    shm_path = Path(f"/dev/shm/squeezelite-{mac}")
    shm_path.write_bytes(b"")

    storage = Storage(tmp_path / "config.json")
    storage.save_profile(Profile(player_mac=mac))
    manager = PlayerManager(storage)

    manager.cleanup_orphaned_shm()

    assert not shm_path.exists(), (
        "cleanup_orphaned_shm() should have removed the segment"
    )


def test_cleanup_orphaned_shm_ignores_unknown_mac(tmp_path: Path) -> None:
    """cleanup_orphaned_shm() must not touch segments it does not own."""
    unknown_mac = "02:ff:00:de:ad:04"
    shm_path = Path(f"/dev/shm/squeezelite-{unknown_mac}")
    shm_path.write_bytes(b"")

    # Storage has no profile with this MAC.
    manager = _make_manager(tmp_path)
    manager.cleanup_orphaned_shm()

    # File must still be there; clean it up ourselves.
    try:
        assert shm_path.exists(), (
            "cleanup_orphaned_shm() must not remove segments with unknown MACs"
        )
    finally:
        shm_path.unlink(missing_ok=True)
