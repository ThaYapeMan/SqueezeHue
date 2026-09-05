"""Tests for SqueezeliteShmSource (PCM tap, step 1).

All tests use a synthetic vis_t file: a regular tempfile written to match
the shared-memory layout.  The source's mmap and any writable mmap used by
the test both refer to the same file, so writes from the test side are
visible through the source's read-only view.
"""

import logging
import mmap
import struct
from pathlib import Path

import numpy as np
import pytest

from huesync.pcm_source import (
    VIS_BUF_SIZE,
    _BUF_OFFSET,
    _HDR_FMT,
    _HDR_OFFSET,
    _HDR_SIZE,
    _MMAP_SIZE,
    SqueezeliteShmSource,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HDR_END = _HDR_OFFSET + _HDR_SIZE  # == _BUF_OFFSET == 80


def _make_vis_t(
    buf_index: int = 0,
    running: bool = True,
    rate: int = 44100,
    buffer: bytes | None = None,
) -> bytes:
    """Build a complete vis_t image ready to write to a tempfile."""
    lock_bytes = b"\x00" * _HDR_OFFSET
    header = struct.pack(_HDR_FMT, VIS_BUF_SIZE, buf_index, int(running), rate, 0)
    buf_bytes = buffer if buffer is not None else b"\x00" * (VIS_BUF_SIZE * 2)
    return lock_bytes + header + buf_bytes


def _write_vis_t(tmp_path: Path, **kwargs: object) -> Path:
    p = tmp_path / "squeezelite-test"
    p.write_bytes(_make_vis_t(**kwargs))  # type: ignore[arg-type]
    return p


def _open_writable_mm(path: Path) -> mmap.mmap:
    """Return a writable mmap on *path* for test-side mutations."""
    fd = path.open("r+b")
    mm = mmap.mmap(fd.fileno(), _MMAP_SIZE)
    fd.close()
    return mm


def _set_buf_index(mm: mmap.mmap, index: int) -> None:
    mm.seek(_HDR_OFFSET + 4)  # buf_index is 4 bytes after buf_size
    mm.write(struct.pack("<I", index))
    mm.flush()


def _write_samples(mm: mmap.mmap, start: int, s16_values: list[int]) -> None:
    """Write s16 values into the circular buffer, wrapping at VIS_BUF_SIZE."""
    for i, v in enumerate(s16_values):
        pos = (start + i) % VIS_BUF_SIZE
        mm.seek(_BUF_OFFSET + pos * 2)
        mm.write(struct.pack("<h", v))
    mm.flush()


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


def test_sample_rate_read_from_header(tmp_path: Path) -> None:
    p = _write_vis_t(tmp_path, rate=44100)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    assert src.sample_rate == 44100
    src.close()


def test_sample_rate_48k(tmp_path: Path) -> None:
    p = _write_vis_t(tmp_path, rate=48000)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    assert src.sample_rate == 48000
    src.close()


def test_running_true(tmp_path: Path) -> None:
    p = _write_vis_t(tmp_path, running=True)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    assert src.running is True
    src.close()


def test_running_false(tmp_path: Path) -> None:
    p = _write_vis_t(tmp_path, running=False)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    assert src.running is False
    src.close()


# ---------------------------------------------------------------------------
# read_new — no new samples
# ---------------------------------------------------------------------------


def test_read_new_empty_on_open(tmp_path: Path) -> None:
    """open() snapshots buf_index; immediate read_new() returns nothing."""
    p = _write_vis_t(tmp_path, buf_index=100)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    result = src.read_new()
    assert result.shape == (0,)
    assert result.dtype == np.float32
    src.close()


# ---------------------------------------------------------------------------
# read_new — simple (no wraparound)
# ---------------------------------------------------------------------------


def test_read_new_basic(tmp_path: Path) -> None:
    """Two stereo frames → two mono samples with correct downmix."""
    # Stereo pairs: frame0 = (L=1000, R=2000), frame1 = (L=3000, R=4000).
    buf = bytearray(VIS_BUF_SIZE * 2)
    struct.pack_into("<hhhh", buf, 0, 1000, 2000, 3000, 4000)

    p = _write_vis_t(tmp_path, buf_index=4, buffer=bytes(buf))
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0  # rewind so read_new() sees samples 0-3

    result = src.read_new()

    assert result.shape == (2,)
    assert result.dtype == np.float32
    np.testing.assert_allclose(result[0], (1000 + 2000) / (2.0 * 32768.0), rtol=1e-6)
    np.testing.assert_allclose(result[1], (3000 + 4000) / (2.0 * 32768.0), rtol=1e-6)
    src.close()


def test_read_new_scaled_to_unit_range(tmp_path: Path) -> None:
    """Maximum int16 value maps to 1.0; minimum maps to ≈−1.0."""
    buf = bytearray(VIS_BUF_SIZE * 2)
    struct.pack_into("<hh", buf, 0, 32767, 32767)   # max L, max R
    struct.pack_into("<hh", buf, 4, -32768, -32768)  # min L, min R

    p = _write_vis_t(tmp_path, buf_index=4, buffer=bytes(buf))
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0

    result = src.read_new()

    assert result.shape == (2,)
    np.testing.assert_allclose(result[0], 32767 / 32768.0, rtol=1e-5)
    np.testing.assert_allclose(result[1], -1.0, rtol=1e-5)
    src.close()


def test_read_new_advances_prev_index(tmp_path: Path) -> None:
    """After read_new(), a second call with no new data returns empty."""
    buf = bytearray(VIS_BUF_SIZE * 2)
    struct.pack_into("<hh", buf, 0, 100, 200)

    p = _write_vis_t(tmp_path, buf_index=2, buffer=bytes(buf))
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0

    first = src.read_new()
    assert first.shape == (1,)

    second = src.read_new()
    assert second.shape == (0,)
    src.close()


# ---------------------------------------------------------------------------
# read_new — wraparound
# ---------------------------------------------------------------------------


def test_read_new_wraparound(tmp_path: Path) -> None:
    """Circular-buffer wraparound returns contiguous samples in write order."""
    # Three stereo frames spanning the wrap boundary:
    #   frame_A at s16 positions 16382-16383
    #   frame_B at s16 positions 0-1
    #   frame_C at s16 positions 2-3
    # buf_index after writing = 4 (wrapped around from 16384).
    FRAME_A = (100, 200)
    FRAME_B = (300, 400)
    FRAME_C = (500, 600)

    buf = bytearray(VIS_BUF_SIZE * 2)
    struct.pack_into("<hh", buf, 16382 * 2, *FRAME_A)
    struct.pack_into("<hh", buf, 0 * 2, *FRAME_B)
    struct.pack_into("<hh", buf, 2 * 2, *FRAME_C)

    p = _write_vis_t(tmp_path, buf_index=4, buffer=bytes(buf))
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 16382  # last position before the three frames were written

    result = src.read_new()

    # n_new = (4 - 16382) % 16384 = 6 → 3 mono samples
    assert result.shape == (3,)
    np.testing.assert_allclose(result[0], (100 + 200) / (2.0 * 32768.0), rtol=1e-6)
    np.testing.assert_allclose(result[1], (300 + 400) / (2.0 * 32768.0), rtol=1e-6)
    np.testing.assert_allclose(result[2], (500 + 600) / (2.0 * 32768.0), rtol=1e-6)
    src.close()


# ---------------------------------------------------------------------------
# read_new — fell behind
# ---------------------------------------------------------------------------


def test_fell_behind_logs_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """When n_new > VIS_BUF_SIZE // 2, a WARNING is logged."""
    p = _write_vis_t(tmp_path, buf_index=9000)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0  # n_new = 9000 > 8192 → fell behind

    with caplog.at_level(logging.WARNING, logger="huesync.pcm_source"):
        src.read_new()

    assert any("fell behind" in r.message.lower() for r in caplog.records)
    src.close()


def test_fell_behind_returns_newest_window(tmp_path: Path) -> None:
    """Fell-behind case returns exactly VIS_BUF_SIZE // 2 mono samples."""
    p = _write_vis_t(tmp_path, buf_index=9000)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0

    result = src.read_new()

    # VIS_BUF_SIZE // 2 = 8192 s16 samples = 4096 stereo frames = 4096 mono samples.
    assert result.shape == (VIS_BUF_SIZE // 4,)
    assert result.dtype == np.float32
    src.close()


def test_fell_behind_subsequent_read_is_empty(tmp_path: Path) -> None:
    """After a fell-behind read, prev_index advances to buf_index; next call empty."""
    p = _write_vis_t(tmp_path, buf_index=9000)
    src = SqueezeliteShmSource()
    src.open("x", _path=p)
    src._prev_index = 0

    src.read_new()           # consumes the fell-behind window
    result = src.read_new()  # no new data since buf_index didn't move

    assert result.shape == (0,)
    src.close()


# ---------------------------------------------------------------------------
# Live update via shared mmap (simulates squeezelite writing new frames)
# ---------------------------------------------------------------------------


def test_incremental_reads(tmp_path: Path) -> None:
    """Two successive polls each return only their own new frames."""
    p = _write_vis_t(tmp_path, buf_index=0)

    src = SqueezeliteShmSource()
    src.open("x", _path=p)

    with _open_writable_mm(p) as w_mm:
        # Write 2 stereo frames (4 s16 values) starting at position 0.
        _write_samples(w_mm, 0, [10, 20, 30, 40])
        _set_buf_index(w_mm, 4)

        first = src.read_new()
        assert first.shape == (2,)
        np.testing.assert_allclose(first[0], (10 + 20) / (2.0 * 32768.0), rtol=1e-6)

        # Write 1 more stereo frame at position 4.
        _write_samples(w_mm, 4, [50, 60])
        _set_buf_index(w_mm, 6)

        second = src.read_new()
        assert second.shape == (1,)
        np.testing.assert_allclose(second[0], (50 + 60) / (2.0 * 32768.0), rtol=1e-6)

    src.close()
