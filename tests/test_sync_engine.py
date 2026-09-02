from huesync.models import ColorMode, Profile
from huesync.sync_engine import _band_average, frame_to_commands


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
