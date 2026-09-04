"""Tests for the JSON REST API (src/huesync/api.py).

Uses FastAPI's TestClient so no real network connections are made.
PlayerManager is mocked to avoid needing actual processes or bridges.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from fastapi.testclient import TestClient

from huesync.app import app
from huesync.models import BridgeConfig, Profile
from huesync.storage import Storage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "config.json")


def _make_mock_manager() -> MagicMock:
    manager = MagicMock()
    # Active profile
    manager.active_profile_id = None
    manager.detected_sync_master = None
    manager.latency_warning = None
    # New properties
    type(manager).applied_delay_ms = PropertyMock(return_value=0)
    type(manager).bridge_connected = PropertyMock(return_value=False)
    type(manager).process_status = PropertyMock(return_value={"squeezelite": False, "cava": False})
    type(manager).last_bars = PropertyMock(return_value=[])
    # Async methods
    manager.activate = AsyncMock()
    manager.deactivate = AsyncMock()
    manager.refresh_probe = AsyncMock()
    return manager


@pytest.fixture()
def client(tmp_path: Path):
    storage = _make_storage(tmp_path)
    manager = _make_mock_manager()
    app.state.storage = storage
    app.state.player_manager = manager
    with TestClient(app) as c:
        c._manager = manager  # expose for test inspection
        c._storage = storage
        yield c


# ---------------------------------------------------------------------------
# Bridges
# ---------------------------------------------------------------------------


def test_list_bridges_empty(client: TestClient):
    resp = client.get("/api/bridges")
    assert resp.status_code == 200
    assert resp.json() == []


def test_delete_bridge_returns_204(client: TestClient):
    # Store a bridge first so there is something to delete.
    bridge = BridgeConfig(name="Test", host="192.168.1.1")
    client._storage.save_bridge(bridge)

    resp = client.delete(f"/api/bridges/{bridge.id}")
    assert resp.status_code == 204

    assert client._storage.get_bridge(bridge.id) is None


def test_get_bridge_areas_404_for_unknown(client: TestClient):
    resp = client.get("/api/bridges/does-not-exist/areas")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def test_list_profiles_returns_list(client: TestClient):
    resp = client.get("/api/profiles")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_create_profile_returns_201(client: TestClient):
    payload = {
        "name": "Living room",
        "lms_host": "192.168.1.10",
        "bridge_id": "br-1",
        "entertainment_area_id": "ea-1",
    }
    resp = client.post("/api/profiles", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Living room"
    assert body["lms_host"] == "192.168.1.10"
    # player_mac should have been auto-generated
    assert body["player_mac"] != ""


def test_get_profile_404_for_unknown(client: TestClient):
    resp = client.get("/api/profiles/does-not-exist")
    assert resp.status_code == 404


def test_patch_profile_applies_partial_update(client: TestClient):
    # Create a profile to patch.
    profile = Profile(name="Original", lms_host="10.0.0.1")
    client._storage.save_profile(profile)

    resp = client.patch(f"/api/profiles/{profile.id}", json={"name": "Updated"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Updated"
    # lms_host must be unchanged.
    assert body["lms_host"] == "10.0.0.1"


def test_patch_profile_does_not_touch_other_fields(client: TestClient):
    profile = Profile(name="A", sensitivity=0.5, brightness_floor=0.3)
    client._storage.save_profile(profile)

    resp = client.patch(f"/api/profiles/{profile.id}", json={"sensitivity": 0.8})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sensitivity"] == 0.8
    assert body["brightness_floor"] == pytest.approx(0.3)


def test_delete_profile_returns_204(client: TestClient):
    profile = Profile(name="To delete")
    client._storage.save_profile(profile)

    resp = client.delete(f"/api/profiles/{profile.id}")
    assert resp.status_code == 204
    assert client._storage.get_profile(profile.id) is None


def test_deactivate_profile_returns_active_id_none(client: TestClient):
    resp = client.post("/api/profiles/deactivate")
    assert resp.status_code == 200
    assert resp.json() == {"active_id": None}


def test_deactivate_does_not_conflict_with_profile_id_route(client: TestClient):
    # "deactivate" must not be routed as a {profile_id} parameter.
    resp = client.post("/api/profiles/deactivate")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Player latencies
# ---------------------------------------------------------------------------


def test_list_player_latencies_empty(client: TestClient):
    resp = client.get("/api/player-latencies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_player_latency_returns_201(client: TestClient):
    payload = {"player_mac": "AA:BB:CC:DD:EE:FF", "strategy": "fixed", "fixed_delay_ms": 1500}
    resp = client.post("/api/player-latencies", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    # MAC is stored lower-cased and stripped.
    assert body["player_mac"] == "aa:bb:cc:dd:ee:ff"
    assert body["fixed_delay_ms"] == 1500
    client._manager.refresh_probe.assert_called()


def test_patch_player_latency_404_for_unknown(client: TestClient):
    resp = client.patch("/api/player-latencies/00:11:22:33:44:55", json={"fixed_delay_ms": 999})
    assert resp.status_code == 404


def test_delete_player_latency_returns_204(client: TestClient):
    # Create an entry first.
    payload = {"player_mac": "11:22:33:44:55:66", "strategy": "fixed", "fixed_delay_ms": 2000}
    client.post("/api/player-latencies", json=payload)

    resp = client.delete("/api/player-latencies/11:22:33:44:55:66")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_get_status_returns_correct_shape(client: TestClient):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "active_profile_id" in body
    assert "sync_master" in body
    assert "applied_delay_ms" in body
    assert "latency_warning" in body
    assert "processes" in body
    assert "squeezelite" in body["processes"]
    assert "cava" in body["processes"]
    assert "bridge_connected" in body
