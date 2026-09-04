"""The audio analysis and colour engine.

Signal path (one layer at a time):

    FifoReader      — reads cava's raw FIFO output in a background thread
    BandNormaliser  — AGC: normalises each bar against its own rolling average
    OnsetDetector   — spectral flux onset detection with EMA-based threshold
    CavaAnalyser    — wraps the three above; produces AudioFeatures each frame
    ColourModeEffect— implements Effect; maps AudioFeatures to a Scene using
                      one of the two active ColorMode strategies
    SyncEngine      — orchestrates Analyser + Effect + Output at 30 Hz,
                      with an optional ring-buffer delay on the output

Nothing in this module imports from hue_entertainment; all Hue-specific code
lives in hue_output.py.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
import time
from collections import deque

from .latency import NoLatencyProbe
from .models import ColorMode, Profile
from .types import (
    Analyser,
    AudioFeatures,
    Colour,
    Effect,
    LatencyProbe,
    Output,
    Scene,
    UniformScene,
)

log = logging.getLogger(__name__)

# Hue Entertainment accepts up to ~50 updates/sec; cava can emit frames much
# faster than that (its rate isn't tied to real playback speed, especially
# with a timer-driven ALSA device like snd-dummy).  Rather than queueing every
# frame — which overwhelms the event loop with scheduled callbacks and starves
# the sender coroutine — the reader thread keeps the *latest* frame in a
# lock-protected slot, and the sender polls it at a fixed interval.  Old
# frames are simply superseded, never queued.
SEND_INTERVAL_S = 1 / 30


# ---------------------------------------------------------------------------
# FifoReader — background thread that tails cava's FIFO
# ---------------------------------------------------------------------------


class FifoReader:
    """Reads fixed-size frames from cava's raw-output FIFO in a background
    thread and keeps only the most recent one available for the sender."""

    def __init__(self, fifo_path: str, frame_size: int) -> None:
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
                    # Writer (cava) closed the pipe — back off briefly and retry.
                    self._stop.wait(0.2)
                    continue
                buf += chunk
                while len(buf) >= self.frame_size:
                    frame, buf = buf[: self.frame_size], buf[self.frame_size :]
                    with self._lock:
                        self._latest_frame = frame
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# BandNormaliser — per-band EMA AGC
# ---------------------------------------------------------------------------


class BandNormaliser:
    """Converts raw cava bar values into per-band exertion scores.

    Instead of comparing bands against each other in absolute terms (which
    lets bass dominate almost every frame because it's always energetic),
    each bar is compared against *its own recent average*:

        exertion(i) = raw[i] / rolling_average(i)

    A band that is always loud stops being interesting; it only lights up
    when it is louder than it usually is.  Mids and treble that were
    previously drowned out now get equal standing whenever they spike above
    their own baselines.

    The result is re-encoded as bytes (0-255) so it can be fed straight
    into the existing frame_to_commands() pipeline unchanged:

        exertion 0.0  → byte   0  (band well below its average)
        exertion 1.0  → byte  85  (band exactly at its average, clip=3.0)
        exertion 3.0  → byte 255  (band at three times its average, clipped)

    Three-layer loudness pipeline — how the settings interact:

    1. HERE (exertion_clip): sets what "maximally loud relative to normal"
       means in relative terms.  Default 3.0 gives headroom so constant-
       level music (exertion ≈ 1×) sits around byte 85 (≈ 33 % output)
       rather than saturating.  Lower values compress dynamic range;
       higher values give more headroom before saturation.

    2. profile.sensitivity (in ColourModeEffect.render()): a multiplier
       applied *after* normalisation.  With normalised input at ≈ 0.33,
       sens=1.0 is roughly one-third brightness steady-state; sens=2.0
       doubles that to two-thirds.  Fine-tune here.

    3. Clip at 1.0 (in ColourModeEffect.render()): safety ceiling just
       before RGB conversion.  Prevents individual channels from exceeding
       full brightness regardless of sensitivity.  Not a musical choice.
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

    #: Default exertion clip ratio.  See class docstring.
    DEFAULT_EXERTION_CLIP: float = 3.0

    def __init__(
        self,
        alpha: float = DEFAULT_ALPHA,
        gate: float = DEFAULT_GATE,
        exertion_clip: float = DEFAULT_EXERTION_CLIP,
    ) -> None:
        self.alpha = alpha
        self.gate = gate
        self.exertion_clip = exertion_clip
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
        self._ema = [ema * (1.0 - a) + v * a for ema, v in zip(self._ema, frame, strict=True)]

        # Silence gate: if the mean raw bar is negligible, keep the lights
        # dark rather than amplifying noise into meaningless colour flashes.
        if sum(frame) / n < self.gate:
            return bytes(n)

        result = bytearray(n)
        for i, (v, ema) in enumerate(zip(frame, self._ema, strict=True)):
            # Guard against a zero EMA (e.g. a bar that has been silent for
            # the entire session so far).
            exertion = v / max(ema, 1.0)
            result[i] = int(min(exertion, self.exertion_clip) * (255.0 / self.exertion_clip))
        return bytes(result)


# ---------------------------------------------------------------------------
# OnsetDetector — Dixon (2006) three-condition peak-picking
# ---------------------------------------------------------------------------


class OnsetDetector:
    """Detects musical onsets using the peak-picking algorithm from Dixon (2006).

    Spectral flux is normalised to mean 0, standard deviation 1 via EMA
    statistics, then a candidate frame is declared an onset only when all
    three conditions hold simultaneously:

        1. Local maximum: f(n) >= f(k) for all k in [n-w, n+w]  (w=3)
        2. Above asymmetric mean: f(n) >= mean(f(k), k in [n-m*w, n+w]) + delta
           (m=3, so the window looks 3× further back than forward)
        3. Above decaying threshold: f(n) >= g_alpha(n-1)
           where g_alpha(n) = max(f(n), alpha*g_alpha(n-1) + (1-alpha)*f(n))

    Condition 1 requires looking w frames ahead, so the detector is inherently
    w frames (~100 ms at 30 Hz) behind real time.  This is irrelevant for
    lighting.

    Condition 3 replaces the old fixed cooldown: it suppresses re-triggering
    adaptively — a loud onset raises the bar for longer than a quiet one.

    Source: Simon Dixon, "Onset Detection Revisited", DAFx-06.
    """

    _W: int = 3    # local-max half-window (frames)
    _M: int = 3    # asymmetry multiplier for condition 2
    #: EMA factor for running flux statistics (normalisation).
    _ALPHA_NORM: float = 0.1
    #: Frames to wait before reporting onsets (lets EMA statistics settle).
    _WARMUP_FRAMES: int = 30
    #: Ring-buffer size: m*w past frames + candidate + w future frames.
    _BUF_MAXLEN: int = _M * _W + 1 + _W   # = 13

    def __init__(self, delta: float = 0.1, alpha: float = 0.9) -> None:
        self._delta = delta   # condition 2 margin (in normalised-flux units)
        self._alpha = alpha   # condition 3 decay factor per frame

        self._prev_bars: list[float] | None = None
        self._flux_ema: float = 0.0
        self._flux_var: float = 0.0

        # Ring buffer of normalised flux values.  Candidate to evaluate is
        # always at index _M*_W (= 9) — i.e. _W frames behind the newest.
        self._buf: deque[float] = deque(maxlen=self._BUF_MAXLEN)

        # g_alpha history: maxlen = W+2 so that g_hist[0] at step C+W equals
        # g_alpha(C-1), which is what condition 3 requires.
        self._g_hist: deque[float] = deque(
            [0.0] * (self._W + 2), maxlen=self._W + 2
        )
        self._g: float = 0.0
        self._warmup: int = self._WARMUP_FRAMES

    def process(self, bars: list[float]) -> tuple[bool, float]:
        """Return *(onset, flux_strength)* for the current bar frame.

        *onset* is True on frames where a musical onset is detected.
        *flux_strength* is the raw (unnormalised) spectral flux for this frame.
        """
        if self._prev_bars is None:
            self._prev_bars = list(bars)
            return False, 0.0

        # Spectral flux: sum of positive differences only (rising energy).
        flux = sum(max(0.0, b - p) for b, p in zip(bars, self._prev_bars, strict=True))
        self._prev_bars = list(bars)

        # Running mean and variance for normalisation.  Use pre-update mean so
        # the residual is unbiased.
        old_ema = self._flux_ema
        self._flux_ema = old_ema + self._ALPHA_NORM * (flux - old_ema)
        self._flux_var = self._flux_var + self._ALPHA_NORM * (
            (flux - old_ema) ** 2 - self._flux_var
        )
        flux_std = math.sqrt(max(self._flux_var, 0.0))

        # Normalise to mean 0, std 1.
        f_norm = (flux - old_ema) / max(flux_std, 1e-6)

        # Read g_alpha(C-1) before updating, then advance the history.
        g_prev = self._g_hist[0]
        self._g = max(f_norm, self._alpha * self._g + (1.0 - self._alpha) * f_norm)
        self._g_hist.append(self._g)

        self._buf.append(f_norm)

        if self._warmup > 0:
            self._warmup -= 1
            return False, flux

        if len(self._buf) < self._BUF_MAXLEN:
            return False, flux

        buf = list(self._buf)
        ci = self._M * self._W   # candidate index = 9
        f_c = buf[ci]

        # Condition 1: local maximum within ±w.
        w = self._W
        if any(f_c < buf[ci + k] for k in range(-w, w + 1) if k != 0):
            return False, flux

        # Condition 2: above asymmetric local mean + delta (window = entire buf).
        if f_c < sum(buf) / len(buf) + self._delta:
            return False, flux

        # Condition 3: above decaying threshold from previous onset.
        if f_c < g_prev:
            return False, flux

        return True, flux


# ---------------------------------------------------------------------------
# Helpers shared by CavaAnalyser and ColourModeEffect
# ---------------------------------------------------------------------------


def _band_average(frame: bytes, start: float, end: float) -> float:
    """Average of bars in a fractional slice of *frame* (bytes, 0-255 → 0.0-1.0).

    Kept for internal use and for the unit tests that exercise it directly.
    New code should prefer _slice_avg() which works on the float bar lists
    produced by CavaAnalyser.
    """
    n = len(frame)
    lo, hi = int(start * n), max(int(end * n), int(start * n) + 1)
    hi = min(hi, n)
    band = frame[lo:hi]
    return (sum(band) / len(band)) / 255.0 if band else 0.0


def _slice_avg(bars: list[float], start: float, end: float) -> float:
    """Average of a fractional slice of a float bar list (values already 0.0-1.0)."""
    n = len(bars)
    lo, hi = int(start * n), max(int(end * n), int(start * n) + 1)
    hi = min(hi, n)
    segment = bars[lo:hi]
    return sum(segment) / len(segment) if segment else 0.0


# ---------------------------------------------------------------------------
# CavaAnalyser — implements the Analyser protocol
# ---------------------------------------------------------------------------


class CavaAnalyser:
    """Reads cava bar frames from a FIFO, normalises them, and produces
    AudioFeatures including onset detection.

    Wraps FifoReader (raw bytes from FIFO), BandNormaliser (AGC), and
    OnsetDetector (spectral flux).  The Analyser protocol is satisfied by
    start(), stop(), and latest().

    AudioFeatures produced here:
    - bars:           normalised bar values (0.0-1.0)
    - bass/mid/full:  cumulative mel-like band slices (see AudioFeatures docs)
    - centroid:       spectral centroid normalised 0.0-1.0
    - onset:          True on frames where a musical onset is detected
    - onset_strength: raw spectral flux value for that frame
    - beat/tempo:     not yet computed; always None
    """

    def __init__(
        self,
        fifo_path: str,
        bars: int,
        onset_delta: float = 0.1,
        onset_alpha: float = 0.9,
        exertion_clip: float = BandNormaliser.DEFAULT_EXERTION_CLIP,
    ) -> None:
        self._reader = FifoReader(fifo_path, frame_size=bars)
        self._normaliser = BandNormaliser(exertion_clip=exertion_clip)
        self._onset = OnsetDetector(delta=onset_delta, alpha=onset_alpha)

    def start(self) -> None:
        self._reader.start()

    def stop(self) -> None:
        self._reader.stop()

    def latest(self) -> AudioFeatures | None:
        frame = self._reader.latest_frame()
        if frame is None:
            return None
        normed = self._normaliser.normalise(frame)
        n = len(normed)
        bars = [v / 255.0 for v in normed]
        total = sum(bars)
        centroid = (
            sum(i * v for i, v in enumerate(bars)) / total / n if total > 1e-9 else 0.0
        )
        onset, onset_strength = self._onset.process(bars)
        return AudioFeatures(
            bars=bars,
            # Cumulative slices: each covers its range plus everything below it.
            # Proportions are mel-like given cava's log-spaced bars at 50-10000 Hz.
            bass=_slice_avg(bars, 0.0, 0.20),
            mid=_slice_avg(bars, 0.0, 0.55),
            full=_slice_avg(bars, 0.0, 1.0),
            centroid=centroid,
            onset=onset,
            onset_strength=onset_strength,
        )


# ---------------------------------------------------------------------------
# ColourModeEffect — implements the Effect protocol
# ---------------------------------------------------------------------------


class ColourModeEffect:
    """The two remaining ColorMode strategies wrapped as a stateful Effect.

    bass_brightness has been removed.  The two surviving modes are:
    - SPECTRUM_RGB:  bass/mid/treble bands mapped to R/G/B channels.
    - MONO_PULSE:    single colour; brightness follows overall loudness.

    This is stateless per-frame (rendered colour depends only on the current
    AudioFeatures), but implemented as a class so future stateful Effects
    (onset cooldowns, decay envelopes, palette drift) follow the same pattern.

    Clipping: each band value is multiplied by profile.sensitivity and clipped
    to 1.0.  This is the *second* ceiling in the pipeline (the first is
    BandNormaliser's exertion clip).  With normalised input, sens ≈ 1.0 keeps
    steady-state music at roughly half-brightness with brief peaks at full;
    raising sensitivity above ~2.0 pushes the steady state into saturation.
    See BandNormaliser for the full picture.

    Band proportions: the legacy exclusive splits (0-15 %, 15-50 %, 50-100 %)
    are preserved here for backwards compatibility with existing profiles.
    New effects should use the cumulative fields on AudioFeatures instead.
    """

    def __init__(self, profile: Profile) -> None:
        self.profile = profile

    def render(self, features: AudioFeatures, t: float) -> Scene:  # noqa: ARG002
        sens = self.profile.sensitivity
        floor = self.profile.brightness_floor
        bars = features.bars

        # Legacy exclusive band proportions — kept to match the original
        # frame_to_commands() behaviour so existing profiles sound the same.
        bass = min(_slice_avg(bars, 0.0, 0.15) * sens, 1.0)
        mid = min(_slice_avg(bars, 0.15, 0.5) * sens, 1.0)
        treble = min(_slice_avg(bars, 0.5, 1.0) * sens, 1.0)
        overall = min(_slice_avg(bars, 0.0, 1.0) * sens, 1.0)

        mode = self.profile.color_mode
        if mode == ColorMode.SPECTRUM_RGB:
            r, g, b = bass, mid, treble
        else:  # MONO_PULSE
            brightness = max(overall, floor)
            r = g = b = brightness

        if mode != ColorMode.SPECTRUM_RGB:
            r = max(r, floor)
            g = max(g, floor)
            b = max(b, floor)

        return UniformScene(Colour(r=r, g=g, b=b))


# ---------------------------------------------------------------------------
# SyncEngine — orchestrates Analyser + Effect + Output at 30 Hz
# ---------------------------------------------------------------------------


class SyncEngine:
    """Owns a CavaAnalyser and a ColourModeEffect; drives them at a fixed rate
    into whatever Output is passed to run().

    Delay buffer
    ------------
    A LatencyProbe is queried each tick for the current delay in milliseconds.
    Every tick appends one slot (a rendered Scene or None for silent/absent
    frames) and pops the oldest slot(s) to keep the buffer at the probe's
    target depth.  This keeps the delay time-consistent: silent gaps advance
    the buffer rather than compressing it.

    The probe defaults to NoLatencyProbe (0 ms, zero overhead).
    PlayerManager constructs the appropriate probe from the PlayerLatency config
    and can install a new one live via update_probe() — for example when the
    LMS sync master changes between polling cycles.

    last_onset
    ----------
    Reflects the onset flag on the most recently analysed AudioFeatures,
    *without* any output delay applied.  This is intentional: the GUI
    preview uses it to let the user judge detection timing directly against
    what they hear, not against the delayed light output.
    """

    def __init__(
        self,
        fifo_path: str,
        profile: Profile,
        probe: LatencyProbe | None = None,
    ) -> None:
        self.profile = profile
        self._analyser: Analyser = CavaAnalyser(
            fifo_path,
            bars=profile.bars,
            onset_delta=profile.onset_delta,
            onset_alpha=profile.onset_alpha,
            exertion_clip=profile.exertion_clip,
        )
        self._effect: Effect = ColourModeEffect(profile)
        self._probe: LatencyProbe = probe if probe is not None else NoLatencyProbe()
        self._delay_buffer: deque[Scene | None] = deque()
        self._last_onset: bool = False
        self._last_bars: list[float] = []

    def update_probe(self, probe: LatencyProbe) -> None:
        """Swap the latency probe live. Safe to call from the asyncio event loop."""
        self._probe = probe

    @property
    def last_onset(self) -> bool:
        return self._last_onset

    @property
    def last_bars(self) -> list[float]:
        return self._last_bars

    def start(self) -> None:
        self._analyser.start()

    def stop(self) -> None:
        self._analyser.stop()

    async def run(self, output: Output) -> None:
        """Send the latest available frame at a fixed rate until cancelled.

        Deliberately does NOT try to send every frame cava produces — see the
        SEND_INTERVAL_S comment above for why that overwhelmed the event loop
        and starved this very coroutine, silently killing the Entertainment
        stream via its own idle timeout.

        Each tick, one slot is appended to the delay buffer (a rendered Scene
        or None for absent/silent frames) and the oldest slot(s) are popped
        to keep the buffer at the probe's current target depth.  The output
        timestamp is taken at send time so spatial effects that use t for
        animation stay consistent with the real display moment.
        """
        while True:
            features = self._analyser.latest()
            t = time.monotonic()

            if features is not None:
                self._last_onset = features.onset
                self._last_bars = features.bars
                scene: Scene = self._effect.render(features, t)
                self._delay_buffer.append(scene)
            else:
                # None slot: advances the buffer in time without sending,
                # so the delay stays consistent even during silent passages.
                self._delay_buffer.append(None)

            delay_frames = max(
                0, round(self._probe.current_delay_ms() / 1000.0 / SEND_INTERVAL_S)
            )
            while len(self._delay_buffer) > delay_frames:
                entry = self._delay_buffer.popleft()
                if entry is not None:
                    output.send(entry, time.monotonic())

            await asyncio.sleep(SEND_INTERVAL_S)
