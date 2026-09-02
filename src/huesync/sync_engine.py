"""The actual audio -> light pipeline.

cava writes a continuous stream of `bars` single bytes (0-255) to a FIFO, one
frame at a time (see player_manager.py for the exact cava config: 8-bit
binary output). This module reads that FIFO in a background thread (blocking
file reads don't mix well with asyncio) and converts each frame into Hue
LightColorCommands, which get pushed onto an asyncio queue for the
EntertainmentSession to send.

Colour mapping is intentionally simple and tunable (see ColorMode in
models.py) rather than "clever" - a few honest, readable transforms are
easier to reason about and adjust by ear than a black-box algorithm.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable

from hue_entertainment import LightColorCommand

from .models import ColorMode, Profile

log = logging.getLogger(__name__)

MAX_16BIT = 65535


class FifoReader:
    """Reads fixed-size frames from cava's raw-output FIFO in a background thread."""

    def __init__(self, fifo_path: str, frame_size: int, on_frame: Callable[[bytes], None]):
        self.fifo_path = fifo_path
        self.frame_size = frame_size
        self.on_frame = on_frame
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        # Open with O_RDONLY | O_NONBLOCK first so we don't block forever if
        # cava hasn't started writing yet, then switch to a normal blocking
        # read loop once the writer side is attached.
        fd = os.open(self.fifo_path, os.O_RDONLY)
        try:
            buf = b""
            while not self._stop.is_set():
                chunk = os.read(fd, 4096)
                if not chunk:
                    # Writer (cava) closed the pipe - back off briefly and retry.
                    threading.Event().wait(0.2)
                    continue
                buf += chunk
                while len(buf) >= self.frame_size:
                    frame, buf = buf[: self.frame_size], buf[self.frame_size :]
                    self.on_frame(frame)
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
    """Owns one FifoReader + the async send loop into an EntertainmentSession."""

    def __init__(self, fifo_path: str, profile: Profile, channel_ids: list[int]):
        self.profile = profile
        self.channel_ids = channel_ids
        self._loop = asyncio.get_event_loop()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4)
        self._reader = FifoReader(fifo_path, frame_size=profile.bars, on_frame=self._on_frame)
        self.last_commands: list[LightColorCommand] = []

    def _on_frame(self, frame: bytes) -> None:
        # Called from the reader thread - hand off to the event loop.
        # Drop the frame if the queue is full rather than blocking the
        # reader thread; a dropped visual frame is unnoticeable, a stalled
        # reader thread causes audible/visible lag build-up.
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, frame)
        except asyncio.QueueFull:
            pass

    def start(self) -> None:
        self._reader.start()

    def stop(self) -> None:
        self._reader.stop()

    async def run(self, session) -> None:  # session: hue_entertainment.EntertainmentSession
        """Consume frames and push colour commands until cancelled."""
        while True:
            frame = await self._queue.get()
            commands = frame_to_commands(frame, self.profile, self.channel_ids)
            self.last_commands = commands
            session.send(commands)
