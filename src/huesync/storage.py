"""Persistence layer.

Deliberately a single JSON file rather than a database: HueSync manages at
most a handful of profiles and bridges, so a flat file is easier to inspect,
back up, and diff than a SQLite schema - and it's trivial to hand-edit if
something ever needs fixing outside the GUI.

A simple file lock avoids corruption if the API and a background task write
concurrently (unlikely at this scale, but cheap to guard against).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .models import BridgeConfig, PlayerLatency, Profile

_lock = threading.Lock()


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"bridges": [], "profiles": [], "player_latencies": [], "active_profile_id": None})

    def _read(self) -> dict:
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Back-fill any top-level keys added after the initial file was written.
        data.setdefault("player_latencies", [])
        return data

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp.replace(self.path)

    # -- Bridges ----------------------------------------------------------

    def list_bridges(self) -> list[BridgeConfig]:
        with _lock:
            return [BridgeConfig.from_dict(b) for b in self._read()["bridges"]]

    def get_bridge(self, bridge_id: str) -> BridgeConfig | None:
        return next((b for b in self.list_bridges() if b.id == bridge_id), None)

    def save_bridge(self, bridge: BridgeConfig) -> None:
        with _lock:
            data = self._read()
            data["bridges"] = [b for b in data["bridges"] if b["id"] != bridge.id]
            data["bridges"].append(bridge.to_dict())
            self._write(data)

    def delete_bridge(self, bridge_id: str) -> None:
        with _lock:
            data = self._read()
            data["bridges"] = [b for b in data["bridges"] if b["id"] != bridge_id]
            self._write(data)

    # -- Profiles -----------------------------------------------------------

    def list_profiles(self) -> list[Profile]:
        with _lock:
            return [Profile.from_dict(p) for p in self._read()["profiles"]]

    def get_profile(self, profile_id: str) -> Profile | None:
        return next((p for p in self.list_profiles() if p.id == profile_id), None)

    def save_profile(self, profile: Profile) -> None:
        with _lock:
            data = self._read()
            data["profiles"] = [p for p in data["profiles"] if p["id"] != profile.id]
            data["profiles"].append(profile.to_dict())
            self._write(data)

    def delete_profile(self, profile_id: str) -> None:
        with _lock:
            data = self._read()
            data["profiles"] = [p for p in data["profiles"] if p["id"] != profile_id]
            if data.get("active_profile_id") == profile_id:
                data["active_profile_id"] = None
            self._write(data)

    # -- Player latencies ---------------------------------------------------

    def list_player_latencies(self) -> list[PlayerLatency]:
        with _lock:
            return [PlayerLatency.from_dict(p) for p in self._read()["player_latencies"]]

    def get_player_latency(self, player_mac: str) -> PlayerLatency | None:
        return next((p for p in self.list_player_latencies() if p.player_mac == player_mac), None)

    def save_player_latency(self, pl: PlayerLatency) -> None:
        with _lock:
            data = self._read()
            data["player_latencies"] = [
                p for p in data["player_latencies"] if p["player_mac"] != pl.player_mac
            ]
            data["player_latencies"].append(pl.to_dict())
            self._write(data)

    def delete_player_latency(self, player_mac: str) -> None:
        with _lock:
            data = self._read()
            data["player_latencies"] = [
                p for p in data["player_latencies"] if p["player_mac"] != player_mac
            ]
            self._write(data)

    # -- Active profile -----------------------------------------------------

    def get_active_profile_id(self) -> str | None:
        with _lock:
            return self._read().get("active_profile_id")

    def set_active_profile_id(self, profile_id: str | None) -> None:
        with _lock:
            data = self._read()
            data["active_profile_id"] = profile_id
            self._write(data)
