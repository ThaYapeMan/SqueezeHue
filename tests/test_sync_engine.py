from huesync.models import ColorMode, Profile
from huesync.sync_engine import BandNormaliser, ColourModeEffect, OnsetDetector, _band_average
from huesync.types import AudioFeatures, Position

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_features(bars: list[float] | None = None) -> AudioFeatures:
    """Build an AudioFeatures object from a list of bar values (0.0-1.0).

    Computes cumulative band slices and spectral centroid from the bars,
    matching what CavaAnalyser produces, so ColourModeEffect tests have
    realistic inputs without needing a real FIFO or cava process.
    """
    if bars is None:
        bars = [0.5] * 30
    n = len(bars)
    total = sum(bars)
    centroid = sum(i * v for i, v in enumerate(bars)) / total / n if total > 1e-9 else 0.0
    bass_n = max(1, int(n * 0.20))
    mid_n = max(1, int(n * 0.55))
    return AudioFeatures(
        bars=bars,
        bass=sum(bars[:bass_n]) / bass_n,
        mid=sum(bars[:mid_n]) / mid_n,
        full=sum(bars) / n,
        centroid=centroid,
    )


_ORIGIN = Position(0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# _band_average (kept for compatibility; internal helper)
# ---------------------------------------------------------------------------


def test_band_average_silence():
    frame = bytes([0] * 30)
    assert _band_average(frame, 0.0, 1.0) == 0.0


def test_band_average_full_scale():
    frame = bytes([255] * 30)
    assert _band_average(frame, 0.0, 1.0) == 1.0


# ---------------------------------------------------------------------------
# ColourModeEffect — two modes remaining after bass_brightness removal
# ---------------------------------------------------------------------------


def test_colour_mode_effect_returns_valid_scene():
    """render() must return a Scene whose color_at() yields values in [0, 1]."""
    profile = Profile(color_mode=ColorMode.SPECTRUM_RGB, bars=30)
    effect = ColourModeEffect(profile)
    scene = effect.render(_make_features(), 0.0)
    colour = scene.color_at(_ORIGIN, 0.0)
    assert 0.0 <= colour.r <= 1.0
    assert 0.0 <= colour.g <= 1.0
    assert 0.0 <= colour.b <= 1.0


def test_colour_mode_effect_mono_pulse_equal_rgb():
    """MONO_PULSE must produce equal R/G/B (white-light brightness control)."""
    profile = Profile(color_mode=ColorMode.MONO_PULSE, bars=30)
    effect = ColourModeEffect(profile)
    features = _make_features([200 / 255.0] * 30)
    colour = effect.render(features, 0.0).color_at(_ORIGIN, 0.0)
    assert colour.r == colour.g == colour.b


def test_colour_mode_effect_respects_brightness_floor_on_silence():
    """Silence with brightness_floor=0.2 → every component >= 0.2."""
    profile = Profile(color_mode=ColorMode.MONO_PULSE, brightness_floor=0.2, bars=30)
    effect = ColourModeEffect(profile)
    features = _make_features([0.0] * 30)
    colour = effect.render(features, 0.0).color_at(_ORIGIN, 0.0)
    assert colour.r >= 0.2
    assert colour.g >= 0.2
    assert colour.b >= 0.2


def test_colour_mode_effect_spectrum_rgb_full_treble():
    """Pure treble energy (upper half of bars) → blue channel active, not bass/mid."""
    profile = Profile(color_mode=ColorMode.SPECTRUM_RGB, sensitivity=1.0, bars=30)
    effect = ColourModeEffect(profile)
    # All energy in the top 50 % of bars (= treble in the legacy 0-15/15-50/50-100 split).
    bars = [0.0] * 15 + [1.0] * 15
    colour = effect.render(_make_features(bars), 0.0).color_at(_ORIGIN, 0.0)
    # Treble maps to blue; bass/mid bars are zero so R and G should be near 0.
    assert colour.b > 0.0
    assert colour.r == 0.0
    assert colour.g == 0.0


def test_colour_mode_effect_sensitivity_scales_output():
    """Higher sensitivity → brighter output (up to the 1.0 clip)."""
    bars = [0.3] * 30
    profile_low = Profile(color_mode=ColorMode.MONO_PULSE, sensitivity=0.5, bars=30)
    profile_high = Profile(color_mode=ColorMode.MONO_PULSE, sensitivity=2.0, bars=30)
    low = ColourModeEffect(profile_low).render(_make_features(bars), 0.0).color_at(_ORIGIN, 0.0)
    high = ColourModeEffect(profile_high).render(_make_features(bars), 0.0).color_at(_ORIGIN, 0.0)
    assert high.r >= low.r


# ---------------------------------------------------------------------------
# BandNormaliser
# ---------------------------------------------------------------------------


def test_normaliser_silence_gate_returns_dark_frame():
    """A frame whose mean bar is below the gate threshold → all-dark output."""
    norm = BandNormaliser()
    silent = bytes([0] * 30)
    result = norm.normalise(silent)
    assert all(b == 0 for b in result)
    assert len(result) == 30


def test_normaliser_spike_above_warmup_produces_bright_output():
    """After EMA has warmed up to a moderate level, a sudden spike → output
    well above the steady-state byte (≈ 85 with clip=3.0)."""
    norm = BandNormaliser()
    # Warm the EMA to a moderate level well above the silence gate.
    warm = bytes([100] * 30)
    for _ in range(200):
        norm.normalise(warm)
    # A spike to near-full-scale should yield exertion > 1.0 → bytes > 85.
    spike = bytes([220] * 30)
    result = norm.normalise(spike)
    assert all(b > 128 for b in result)


def test_normaliser_steady_state_converges_to_midpoint():
    """After EMA convergence, a frame equal to the running average should
    produce exertion ≈ 1.0.  With default clip=3.0 that encodes as byte ≈ 85
    (= int(1.0 * 255/3.0)), not 128 — the higher clip gives more headroom."""
    norm = BandNormaliser()
    steady = bytes([180] * 30)
    for _ in range(300):
        norm.normalise(steady)
    result = norm.normalise(steady)
    assert all(82 <= b <= 88 for b in result), (
        f"Expected bytes near 85 after convergence (clip=3.0), got: {list(result[:5])}…"
    )


def test_normaliser_ema_still_updates_during_silence():
    """EMA must update even when the silence gate fires, so the baseline
    decays during pauses and the first post-silence frame is not wild."""
    norm = BandNormaliser()
    loud = bytes([200] * 30)
    for _ in range(100):
        norm.normalise(loud)
    ema_after_loud = list(norm._ema)  # type: ignore[union-attr]

    silent = bytes([0] * 30)
    for _ in range(100):
        norm.normalise(silent)
    ema_after_silence = list(norm._ema)  # type: ignore[union-attr]

    # EMA should have decayed toward 0 during silence, not stayed frozen.
    assert all(a < b for a, b in zip(ema_after_silence, ema_after_loud, strict=True))


# ---------------------------------------------------------------------------
# OnsetDetector — Dixon (2006) three-condition peak-picking
# ---------------------------------------------------------------------------


def _warm_up_onset(detector: OnsetDetector, bars: list[float], frames: int = 50) -> None:
    """Feed *frames* frames to get past the warmup period and fill the buffer."""
    for _ in range(frames):
        detector.process(bars)


def _alternating_warm_up(detector: OnsetDetector, n_bars: int = 30, pairs: int = 60) -> None:
    """Alternate between two bar levels so the EMA settles at a non-trivial level.

    Ends on the high bars ([0.6]*n_bars) so the test sequence can start from there.
    """
    lo = [0.3] * n_bars
    hi = [0.6] * n_bars
    for _ in range(pairs):
        detector.process(lo)
        detector.process(hi)


def test_onset_no_trigger_during_warmup():
    """No onset should fire during the warmup period regardless of flux."""
    detector = OnsetDetector(delta=0.0, alpha=0.0)  # maximally sensitive
    bars = [1.0] * 30
    # Warmup is 30 frames; constant bars produce zero flux, so no onset fires.
    for _ in range(31):
        onset, _ = detector.process(bars)
        assert not onset, "Onset fired during warmup"


def test_onset_triggers_on_sudden_spike():
    """After warmup, a large flux spike triggers an onset within w+1 frames."""
    w = OnsetDetector._W
    detector = OnsetDetector(delta=0.1, alpha=0.0)
    n_bars = 30
    _alternating_warm_up(detector, n_bars)
    # Feed a spike from [0.6] to [1.0] followed by w fall-back frames.
    # The spike becomes the candidate w frames later; the falling frames confirm
    # it is a local maximum and give the detector its lookahead.
    spike_bars = [1.0] * n_bars
    hi_bars = [0.6] * n_bars
    frames = [spike_bars] + [hi_bars] * w
    onset_fired = any(detector.process(f)[0] for f in frames)
    assert onset_fired, f"Expected onset within {w + 1} frames of spike"


def test_onset_fires_at_peak_not_rising_edge():
    """Dixon condition 1: onset fires at the flux peak, not on the rising edge.

    A gradual rise followed by a single large jump then a fall must produce
    exactly one onset, timed to the large jump (the true local maximum), not to
    any frame on the rising slope.  alpha=0.0 disables condition 3 so this test
    isolates conditions 1 and 2.
    """
    w = OnsetDetector._W  # 3
    detector = OnsetDetector(delta=0.1, alpha=0.0)
    n_bars = 30
    _alternating_warm_up(detector, n_bars)

    # Bar sequence starting from [0.6] (last warmup level).
    # Flux per step = 30 * max(0, new_level - prev_level).
    bar_sequence = [
        [0.65] * n_bars,  # step 0: flux = 1.5  (rising edge)
        [0.70] * n_bars,  # step 1: flux = 1.5  (rising edge)
        [0.75] * n_bars,  # step 2: flux = 1.5  (rising edge)
        [1.00] * n_bars,  # step 3: flux = 7.5  ← PEAK
        [0.95] * n_bars,  # step 4: flux = 0    (falling)
        [0.80] * n_bars,  # step 5: flux = 0    (falling)
        [0.60] * n_bars,  # step 6: flux = 0    ← onset fires here (peak + w)
        [0.40] * n_bars,  # step 7: flux = 0
        [0.20] * n_bars,  # step 8: flux = 0
    ]

    onsets = [step for step, bars in enumerate(bar_sequence) if detector.process(bars)[0]]

    assert len(onsets) == 1, f"Expected 1 onset, got {len(onsets)} at steps {onsets}"
    assert onsets[0] == 3 + w, (
        f"Expected onset at step {3 + w} (peak index 3 + lookahead w={w}), "
        f"got step {onsets[0]}"
    )


def test_onset_no_trigger_on_slow_rise():
    """A monotonically rising sequence with constant flux has no local maximum.

    Condition 2 (above local mean + delta) also fails because every frame's
    normalised flux is identical — none exceeds the window mean by delta > 0.
    """
    detector = OnsetDetector(delta=0.1, alpha=0.0)
    n_bars = 30
    _alternating_warm_up(detector, n_bars)

    # Linear rise from 0.6 to 1.0 in 20 equal steps: constant flux ≈ 0.6/20 * 30.
    any_onset = False
    for step in range(20):
        level = 0.6 + (step + 1) * 0.4 / 20
        onset, _ = detector.process([level] * n_bars)
        if onset:
            any_onset = True

    assert not any_onset, "Expected no onset on monotonically rising flux"


def test_onset_strength_positive_on_rising_energy():
    """Flux strength must be > 0 when energy rises."""
    detector = OnsetDetector()
    bars_low = [0.1] * 30
    bars_high = [0.9] * 30
    detector.process(bars_low)  # initialise prev
    _, strength = detector.process(bars_high)
    assert strength > 0.0


def test_onset_strength_zero_on_falling_energy():
    """Flux is one-sided (positive differences only), so falling energy → 0."""
    detector = OnsetDetector()
    bars_high = [0.9] * 30
    bars_low = [0.1] * 30
    detector.process(bars_high)
    _, strength = detector.process(bars_low)
    assert strength == 0.0
