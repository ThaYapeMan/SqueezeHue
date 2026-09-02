"""Process lifecycle: squeezelite (virtual LMS player) + cava (spectrum
analysis) + the Hue EntertainmentSession, all tied to whichever Profile is
currently active.

Only one profile can be active at a time (a Hue Bridge only supports a
single Entertainment stream), which this class enforces directly rather
than letting a second `start()` collide with the bridge's own rejection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from hue_entertainment import EntertainmentSession

from .hue_bridge import get_area_channel_ids, list_entertainment_areas
from .models import Profile
from .storage import Storage
from .sync_engine import SyncEngine

log = logging.getLogger(__name__)

_RUN_DIR = Path(tempfile.gettempdir()) / "huesync"


class ActiveSession:
    def __init__(self, profile: Profile):
        self.profile = profile
        self.squeezelite: subprocess.Popen | None = None
        self.cava: subprocess.Popen | None = None
        self.fifo_path: Path = _RUN_DIR / f"{profile.id}.fifo"
        self.cava_conf_path: Path = _RUN_DIR / f"{profile.id}.conf"
        self.sync_engine: SyncEngine | None = None
        self.hue_session: EntertainmentSession | None = None
        self.task: asyncio.Task | None = None


class PlayerManager:
    def __init__(self, storage: Storage):
        self.storage = storage
        self._active: ActiveSession | None = None
        _RUN_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def active_profile_id(self) -> str | None:
        return self._active.profile.id if self._active else None

    @property
    def last_commands(self):
        if self._active and self._active.sync_engine:
            return self._active.sync_engine.last_commands
        return []

    async def activate(self, profile: Profile) -> None:
        """Stop whatever is currently active, then start this profile."""
        await self.deactivate()

        bridge = self.storage.get_bridge(profile.bridge_id)
        if bridge is None:
            raise ValueError("Profile has no valid bridge configured")

        areas = await list_entertainment_areas(bridge)
        area = next((a for a in areas if a.id == profile.entertainment_area_id), None)
        if area is None:
            raise ValueError("Configured Entertainment Area no longer exists on the bridge")

        # list_entertainment_areas() only returns a summary (light_count);
        # the real per-light channel_id values must be fetched separately.
        channel_ids = await get_area_channel_ids(bridge, profile.entertainment_area_id)

        session = ActiveSession(profile)
        self._start_squeezelite(session, profile)
        self._start_cava(session, profile)

        engine = SyncEngine(str(session.fifo_path), profile, channel_ids)
        session.sync_engine = engine

        hue_session = EntertainmentSession(bridge.host, bridge.app_key, bridge.client_key)
        await hue_session.start(profile.entertainment_area_id)
        session.hue_session = hue_session

        engine.start()
        session.task = asyncio.create_task(engine.run(hue_session))

        self._active = session
        self.storage.set_active_profile_id(profile.id)
        log.info("Activated profile %s (%s)", profile.name, profile.id)

    async def deactivate(self) -> None:
        if not self._active:
            return
        session = self._active
        self._active = None

        if session.task:
            session.task.cancel()
        if session.sync_engine:
            session.sync_engine.stop()
        if session.hue_session:
            try:
                await session.hue_session.stop()
                await session.hue_session.aclose()
            except Exception:  # noqa: BLE001 - best-effort teardown
                log.exception("Error stopping Hue Entertainment session")

        for proc in (session.cava, session.squeezelite):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()

        for p in (session.fifo_path, session.cava_conf_path):
            p.unlink(missing_ok=True)

        self.storage.set_active_profile_id(None)
        log.info("Deactivated profile %s", session.profile.name)

    def _start_squeezelite(self, session: ActiveSession, profile: Profile) -> None:
        binary = shutil.which("squeezelite")
        if not binary:
            raise RuntimeError("squeezelite binary not found on PATH")
        cmd = [
            binary,
            "-n", profile.player_name,
            "-m", profile.player_mac,
            "-o", "null",
            "-v",
            "-s", f"{profile.lms_host}:{profile.lms_port}",
        ]
        session.squeezelite = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _start_cava(self, session: ActiveSession, profile: Profile) -> None:
        binary = shutil.which("cava")
        if not binary:
            raise RuntimeError("cava binary not found on PATH")

        if session.fifo_path.exists():
            session.fifo_path.unlink()
        os.mkfifo(session.fifo_path)

        mac = profile.player_mac
        conf = f"""[general]
bars = {profile.bars}

[input]
method = shmem
source = /squeezelite-{mac}

[output]
method = raw
raw_target = {session.fifo_path}
data_format = binary
bit_format = 8bit
channels = mono
"""
        session.cava_conf_path.write_text(conf)
        session.cava = subprocess.Popen(
            [binary, "-p", str(session.cava_conf_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
