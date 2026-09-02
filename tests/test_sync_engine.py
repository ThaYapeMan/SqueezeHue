from huesync.models import ColorMode, Profile
from huesync.sync_engine import BandNormaliser, _band_average, frame_to_commands


def test_band_average_silence():
    frame = bytes([0] * 30)
    assert _band_average(frame, 0.0, 1.0) == 0.0


def test_band_average_full_scale():
    frame = bytes([255] * 30)
    assert _band_average(frame, 0.0, 1.0) == 1.0


def test_frame_to_commands_spectrum_rgb_length_matches_channels():
    profile = Profile(color_mode=ColorMode.SPECTRUM_RGB, bars=30)
    frame = bytes([128] * 30)
    commands = frame_to_commands(frame, profile, channel_ids=[0, 5, 9])
    assert len(commands) == 3
    assert {c.channel_id for c in commands} == {0, 5, 9}


def test_frame_to_commands_mono_pulse_equal_rgb():
    profile = Profile(color_mode=ColorMode.MONO_PULSE, bars=30)
    frame = bytes([200] * 30)
    commands = frame_to_commands(frame, profile, channel_ids=[0])
    c = commands[0]
    assert c.red == c.green == c.blue


def test_frame_to_commands_respects_brightness_floor_on_silence():
    profile = Profile(color_mode=ColorMode.MONO_PULSE, brightness_floor=0.2, bars=30)
    frame = bytes([0] * 30)
    commands = frame_to_commands(frame, profile, channel_ids=[0])
    c = commands[0]
    # Floor is a fraction of 16-bit max; silence should still be at least that bright.
    assert c.red >= int(0.2 * 65535) - 1


# ---------------------------------------------------------------------------
# BandNormaliser tests
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
