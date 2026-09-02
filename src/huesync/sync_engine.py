"""The actual audio -> light pipeline.

cava writes a continuous stream of `bars` single bytes (0-255) to a FIFO, one
frame at a time (see player_manager.py for the exact cava config: 8-bit
binary output). This module reads that FIFO in a background thread (blocking
file reads don't mix well with asyncio) and converts each frame into Hue
LightColorCommands, sent to the EntertainmentSession at a fixed rate.

Colour mapping is intentionally simple and tunable (see ColorMode in
models.py) rather than "clever" - a few honest, readable transforms are
easier to reason about and adjust by ear than a black-box algorithm.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading

from hue_entertainment import LightColorCommand

from .models import ColorMode, Profile

log = logging.getLogger(__name__)

MAX_16BIT = 65535

# Hue Entertainment accepts up to ~50 updates/sec; cava can emit frames much
# faster than that (its rate isn't tied to real playback speed, especially
# with a "null" ALSA sink that has no hardware clock to pace against). Rather
# than queueing every frame - which overwhelms the event loop with scheduled
# callbacks and starves the sender coroutine - the reader thread just keeps
# the *latest* frame in a lock-protected slot, and the sender polls it at a
# fixed interval. Old frames are simply superseded, never queued.
SEND_INTERVAL_S = 1 / 30


class FifoReader:
    """Reads fixed-size frames from cava's raw-output FIFO in a background
    thread and keeps only the most recent one available for the sender."""

    def __init__(self, fifo_path: str, frame_size: int):
        self.fifo_path = fifo_path
        self.frame_size = frame_size
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest_frame: bytes | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def latest_frame(self) -> bytes | None:
        with self._lock:
            return self._latest_frame

    def _run(self) -> None:
        fd = os.open(self.fifo_path, os.O_RDONLY)
        try:
            buf = b""
            while not self._stop.is_set():
                chunk = os.read(fd, 4096)
                if not chunk:
                    # Writer (cava) closed the pipe - back off briefly and retry.
                    self._stop.wait(0.2)
                    continue
                buf += chunk
                while len(buf) >= self.frame_size:
                    frame, buf = buf[: self.frame_size], buf[self.frame_size :]
                    with self._lock:
                        self._latest_frame = frame
        finally:
            os.close(fd)


def _band_average(frame: bytes, start: float, end: float) -> float:
    """Average of the bars between two fractional positions (0.0-1.0) in the frame."""
    n = len(frame)
    lo, hi = int(start * n), max(int(end * n), int(start * n) + 1)
    hi = min(hi, n)
    band = frame[lo:hi]
    return (sum(band) / len(band)) / 255.0 if band else 0.0


def frame_to_commands(
    frame: bytes, profile: Profile, channel_ids: list[int]
) -> list[LightColorCommand]:
    """Turn one cava frame into per-channel colour commands.

    channel_ids are the Hue Entertainment Area's LightChannel.channel_id
    values - every light in the area gets the same colour for now (a
    per-light spatial mapping, e.g. bass on one side of the room and treble
    on the other, is a natural follow-up but out of scope for v0.1).
    """
    sens = profile.sensitivity
    floor = profile.brightness_floor

    bass = min(_band_average(frame, 0.0, 0.15) * sens, 1.0)
    mid = min(_band_average(frame, 0.15, 0.5) * sens, 1.0)
    treble = min(_band_average(frame, 0.5, 1.0) * sens, 1.0)
    overall = min(_band_average(frame, 0.0, 1.0) * sens, 1.0)

    if profile.color_mode == ColorMode.SPECTRUM_RGB:
        r, g, b = bass, mid, treble
    elif profile.color_mode == ColorMode.BASS_BRIGHTNESS:
        # Fixed warm hue, brightness driven by bass energy.
        brightness = max(bass, floor)
        r, g, b = brightness, brightness * 0.6, brightness * 0.2
    else:  # MONO_PULSE
        brightness = max(overall, floor)
        r = g = b = brightness

    r = max(r, floor if profile.color_mode != ColorMode.SPECTRUM_RGB else 0.0)
    g = max(g, floor if profile.color_mode != ColorMode.SPECTRUM_RGB else 0.0)
    b = max(b, floor if profile.color_mode != ColorMode.SPECTRUM_RGB else 0.0)

    red16, green16, blue16 = (
        int(r * MAX_16BIT),
        int(g * MAX_16BIT),
        int(b * MAX_16BIT),
    )
    return [
        LightColorCommand(channel_id=cid, red=red16, green=green16, blue=blue16)
        for cid in channel_ids
    ]


class SyncEngine:
    """Owns one FifoReader + a fixed-rate async send loop into an
    EntertainmentSession."""

    def __init__(self, fifo_path: str, profile: Profile, channel_ids: list[int]):
        self.profile = profile
        self.channel_ids = channel_ids
        self._reader = FifoReader(fifo_path, frame_size=profile.bars)
        self.last_commands: list[LightColorCommand] = []

    def start(self) -> None:
        self._reader.start()

    def stop(self) -> None:
        self._reader.stop()

    async def run(self, session) -> None:  # session: hue_entertainment.EntertainmentSession
        """Send the latest available frame at a fixed rate until cancelled.

        Deliberately does NOT try to send every frame cava produces - see
        the SEND_INTERVAL_S comment above for why that overwhelmed the
        event loop and starved this very coroutine, silently killing the
        Entertainment stream via its own idle timeout.
        """
        while True:
            frame = self._reader.latest_frame()
            if frame is not None:
                commands = frame_to_commands(frame, self.profile, self.channel_ids)
                self.last_commands = commands
                session.send(commands)
            await asyncio.sleep(SEND_INTERVAL_S)