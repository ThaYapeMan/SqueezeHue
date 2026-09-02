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


class BandNormaliser:
    """Converts raw cava bar values into per-band exertion scores.

    Instead of comparing bands against each other in absolute terms (which
    lets bass dominate almost every frame because it's always energetic),
    each bar is compared against *its own recent average*:

        exertion(i) = raw[i] / rolling_average(i)

    A bass line that is always present stops being interesting; it only
    lights up when it is louder than it usually is.  Mids and treble that
    were previously drowned out now get equal standing whenever they spike
    above their own baselines.

    The result is re-encoded as bytes (0-255) so it can be fed straight
    into the existing frame_to_commands() pipeline unchanged:

        exertion 0.0  → byte   0  (band well below its average)
        exertion 1.0  → byte 128  (band exactly at its average)
        exertion 2.0  → byte 255  (band at twice its average, clipped here)

    Clipping note — there are two independent ceilings in the pipeline:

    1. HERE at 2× the band's own average.  This is a *musical scale
       choice*: it sets what "maximally loud relative to normal" means.
       Raising it makes the output more sensitive to brief spikes;
       lowering it compresses the dynamic range further.

    2. In frame_to_commands(), after multiplying by profile.sensitivity.
       That clip (at 1.0, just before the 16-bit RGB conversion) is a
       *safety ceiling*, not a musical choice.

    These two ceilings interact.  With DEFAULT_ALPHA the normalised output
    hovers around byte 128 (= 0.5 after ÷ 255) during steady music, so
    sens=1.0 gives roughly half-brightness on average and peaks briefly
    at full-brightness.  Raising sensitivity above ~2.0 pushes steady-state
    output into the upper ceiling and the image becomes uniformly saturated.
    After this change profile.sensitivity is best treated as a fine-tune
    around 1.0 rather than the primary loudness driver it was before
    (the normalisation now does that work).
    """

    #: EMA smoothing factor.  At 30 Hz, α = 0.02 gives a window of ≈ 1.7 s.
    #: Raise (e.g. 0.05) to react faster; lower (e.g. 0.01) for a longer
    #: memory that smooths over brief dynamic shifts.
    DEFAULT_ALPHA: float = 0.02

    #: Mean raw bar value (0-255) below which the entire output frame is
    #: zeroed.  Prevents background noise from being amplified into wild
    #: colours when the track is paused or very quiet.  The EMA still
    #: updates during silence so the baseline decays naturally.
    DEFAULT_GATE: float = 5.0

    #: Maximum exertion ratio before clipping.  See class docstring.
    _EXERTION_CLIP: float = 2.0

    def __init__(
        self,
        alpha: float = DEFAULT_ALPHA,
        gate: float = DEFAULT_GATE,
    ) -> None:
        self.alpha = alpha
        self.gate = gate
        # Lazily initialised on the first frame so frame size need not be
        # known at construction time.
        self._ema: list[float] | None = None

    def normalise(self, frame: bytes) -> bytes:
        """Return an exertion-normalised copy of *frame* as bytes (0-255).

        Always updates the EMA, even below the silence gate, so the
        baseline decays during pauses and recovers cleanly on resumption.
        """
        n = len(frame)

        if self._ema is None:
            # Seed the EMA with the first frame so the normaliser is not
            # blind for the first few seconds of a session.
            self._ema = [float(v) for v in frame]

        a = self.alpha
        self._ema = [ema * (1.0 - a) + v * a for ema, v in zip(self._ema, frame)]

        # Silence gate: if the mean raw bar is negligible, keep the lights
        # dark rather than amplifying noise into meaningless colour flashes.
        if sum(frame) / n < self.gate:
            return bytes(n)

        result = bytearray(n)
        for i, (v, ema) in enumerate(zip(frame, self._ema)):
            # Guard against a zero EMA (e.g. a bar that has been silent for
            # the entire session so far).
            exertion = v / max(ema, 1.0)
            result[i] = int(min(exertion, self._EXERTION_CLIP) * (255.0 / self._EXERTION_CLIP))
        return bytes(result)


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

    *frame* is normally pre-processed by BandNormaliser before reaching
    here, so bar values encode exertion (relative energy vs. rolling
    average) rather than raw loudness.  The function itself is stateless
    and unaware of this distinction; it simply maps bytes to colours.

    channel_ids are the Hue Entertainment Area's LightChannel.channel_id
    values - every light in the area gets the same colour for now (a
    per-light spatial mapping, e.g. bass on one side of the room and treble
    on the other, is a natural follow-up but out of scope for v0.1).

    Clipping: each band value is multiplied by profile.sensitivity and
    clipped to 1.0.  This is the *second* ceiling in the pipeline (the
    first is BandNormaliser's exertion clip).  With normalised input,
    sens ≈ 1.0 keeps steady-state music at roughly half-brightness with
    brief peaks at full; raising sensitivity above ~2.0 pushes the steady
    state into saturation.  See BandNormaliser for the full picture.
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
        self._normaliser = BandNormaliser()
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
                normed = self._normaliser.normalise(frame)
                commands = frame_to_commands(normed, self.profile, self.channel_ids)
                self.last_commands = commands
                session.send(commands)
            await asyncio.sleep(SEND_INTERVAL_S)