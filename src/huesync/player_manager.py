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
        self.cava_log_path: Path = _RUN_DIR / f"{profile.id}.cava.log"
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
        """Stop whatever is currently active, then start this profile.

        Any failure partway through is cleaned up before re-raising, so a
        failed activation never leaves a leaked squeezelite/cava process
        behind (see _teardown_session).
        """
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
        try:
            self._start_squeezelite(session, profile)

            engine = SyncEngine(str(session.fifo_path), profile, channel_ids)
            session.sync_engine = engine

            # Order matters: the FIFO reader must be attached BEFORE cava
            # starts writing. A FIFO with a writer but no reader delivers
            # SIGPIPE to the writer, and cava dies immediately - silently,
            # since we send its output to DEVNULL. Starting the Hue session
            # first (an 8+ second DTLS handshake) was long enough for cava
            # to die every single time, which then looked like "the lights
            # stop after 10 seconds" (the Entertainment idle timeout with
            # zero frames ever arriving).
            #
            # _start_cava() creates the FIFO, so it has to run first, but
            # the reader thread opens it (blocking until a writer appears)
            # before cava is spawned.
            self._create_fifo(session)
            engine.start()
            self._start_cava(session, profile)

            hue_session = EntertainmentSession(bridge.host, bridge.app_key, bridge.client_key)
            await hue_session.start(profile.entertainment_area_id)
            session.hue_session = hue_session

            session.task = asyncio.create_task(engine.run(hue_session))
        except Exception:
            # Whatever got started before the failure - squeezelite, cava,
            # the FIFO, a partially-opened Hue session - gets torn down
            # here instead of leaking.
            await self._teardown_session(session)
            raise

        self._active = session
        self.storage.set_active_profile_id(profile.id)
        log.info("Activated profile %s (%s)", profile.name, profile.id)

    async def deactivate(self) -> None:
        if not self._active:
            return
        session = self._active
        self._active = None
        await self._teardown_session(session)
        self.storage.set_active_profile_id(None)
        log.info("Deactivated profile %s", session.profile.name)

    async def _teardown_session(self, session: ActiveSession) -> None:
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

    # ALSA output device for the virtual player. This is deliberately NOT
    # "null": ALSA's null plugin discards samples the instant they arrive,
    # with no clock to pace against, so squeezelite decodes as fast as the
    # CPU allows - pinning a core at 100% and hammering LMS with stream
    # requests (a single-threaded Perl server, which then stutters for
    # every other player too).
    #
    # snd-dummy is a real, timer-driven ALSA card, so squeezelite paces at
    # actual playback speed exactly as it would against a physical DAC.
    # Measured difference on the same setup: ~100% CPU with null, ~0.2%
    # with snd-dummy.
    #
    # Requires the snd-dummy kernel module on the host (LXCs share the
    # host kernel) and the resulting /dev/snd nodes passed into the
    # container - see README.
    DEFAULT_ALSA_DEVICE = "hw:CARD=Dummy,DEV=0"

    def _start_squeezelite(self, session: ActiveSession, profile: Profile) -> None:
        binary = shutil.which("squeezelite")
        if not binary:
            raise RuntimeError("squeezelite binary not found on PATH")
        cmd = [
            binary,
            "-n", profile.player_name,
            "-m", profile.player_mac,
            "-o", profile.alsa_device or self.DEFAULT_ALSA_DEVICE,
            "-v",
            "-s", f"{profile.lms_host}:{profile.lms_port}",
        ]
        session.squeezelite = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _create_fifo(self, session: ActiveSession) -> None:
        """Create the FIFO cava will write to and the reader will read from.

        Split out from _start_cava so the reader can attach to the FIFO
        before cava starts writing - see the ordering note in activate().
        """
        if session.fifo_path.exists():
            session.fifo_path.unlink()
        os.mkfifo(session.fifo_path)

    def _start_cava(self, session: ActiveSession, profile: Profile) -> None:
        binary = shutil.which("cava")
        if not binary:
            raise RuntimeError("cava binary not found on PATH")

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
        # Keep cava's stderr instead of discarding it. Debugging why cava
        # kept dying was needlessly hard because its output went to
        # DEVNULL - the process just showed up as <defunct> with no clue
        # why. Its log is small and only written on errors.
        session.cava_log_path.write_text("")
        cava_log = session.cava_log_path.open("ab")
        session.cava = subprocess.Popen(
            [binary, "-p", str(session.cava_conf_path)],
            stdout=subprocess.DEVNULL,
            stderr=cava_log,
        )