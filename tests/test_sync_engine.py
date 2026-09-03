from huesync.models import ColorMode, Profile
from huesync.sync_engine import BandNormaliser, ColourModeEffect, _band_average
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
# ColourModeEffect
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
    well above 128 (the midpoint that represents exertion = 1.0)."""
    norm = BandNormaliser()
    # Warm the EMA to a moderate level well above the silence gate.
    warm = bytes([100] * 30)
    for _ in range(200):
        norm.normalise(warm)
    # A spike to near-full-scale should yield exertion > 1.0 → bytes > 128.
    spike = bytes([220] * 30)
    result = norm.normalise(spike)
    assert all(b > 128 for b in result)


def test_normaliser_steady_state_converges_to_midpoint():
    """After EMA convergence, a frame equal to the running average should
    produce exertion ≈ 1.0, encoded as byte ≈ 128 (±8 for float rounding)."""
    norm = BandNormaliser()
    steady = bytes([180] * 30)
    for _ in range(300):
        norm.normalise(steady)
    result = norm.normalise(steady)
    assert all(120 <= b <= 136 for b in result), (
        f"Expected bytes near 128 after convergence, got: {list(result[:5])}…"
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
    assert all(a < b for a, b in zip(ema_after_silence, ema_after_loud))
