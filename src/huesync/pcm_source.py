"""Direct PCM tap on squeezelite's visualiser shared memory.

Reads the same segment that cava uses, but independently.  cava continues
driving colour; this is a parallel reader for STFT-based onset detection.

The lock at the start of vis_t is deliberately NOT taken.  Taking a read lock
can cause squeezelite to skip exporting blocks entirely when its trywrlock
fails — so politely locking would introduce the gaps we are trying to avoid.
A torn read means a handful of samples from the wrong position in a 2048-sample
FFT window: inaudible, and rare.
"""

from __future__ import annotations

import logging
import mmap
import struct
from pathlib import Path

import numpy as np

_log = logging.getLogger(__name__)

VIS_BUF_SIZE = 16384  # s16 samples in the circular buffer (8192 stereo frames)
WINDOW_SIZE = 2048  # FFT window length in samples

# vis_t layout, glibc x86-64 (pthread_rwlock_t = 56 bytes):
#   offset  0  pthread_rwlock_t  56 B  (skipped — not taken)
#   offset 56  buf_size           4 B
#   offset 60  buf_index          4 B
#   offset 64  running            1 B  (+3 B padding)
#   offset 68  rate               4 B
#   offset 72  updated            8 B  (time_t)
#   offset 80  buffer[16384]  32768 B  (interleaved stereo s16)
#                             ------
#                             32848 B total
_HDR_OFFSET = 56
_HDR_FMT = "<IIBxxxIq"  # buf_size, buf_index, running, rate, updated
_HDR_SIZE = struct.calcsize(_HDR_FMT)  # 24
_BUF_OFFSET = _HDR_OFFSET + _HDR_SIZE  # 80
_MMAP_SIZE = _BUF_OFFSET + VIS_BUF_SIZE * 2  # 32848


class SqueezeliteShmSource:
    """Reads raw PCM from squeezelite's visualiser shared memory.

    Usage::

        src = SqueezeliteShmSource()
        src.open(mac)                 # call once after squeezelite starts
        samples = src.read_new()      # call at ~100 Hz; returns mono float32
        src.close()
    """

    def __init__(self) -> None:
        self._mm: mmap.mmap | None = None
        self._prev_index: int = 0

    def open(self, mac: str, *, _path: Path | None = None) -> None:
        """Map /dev/shm/squeezelite-{mac} into memory.

        ``_path`` is an internal override used by tests; production code
        always passes a MAC address and lets this method derive the path.
        """
        path = _path if _path is not None else Path(f"/dev/shm/squeezelite-{mac}")
        fd = path.open("rb")
        try:
            self._mm = mmap.mmap(fd.fileno(), _MMAP_SIZE, access=mmap.ACCESS_READ)
        finally:
            fd.close()
        # Snapshot the current write position so the first read_new() returns
        # only samples written *after* open(), not the entire history buffer.
        self._prev_index = self._read_header()[1]

    def close(self) -> None:
        """Unmap the shared memory segment."""
        if self._mm is not None:
            self._mm.close()
            self._mm = None

    def _read_header(self) -> tuple[int, int, bool, int, int]:
        """Return (buf_size, buf_index, running, rate, updated)."""
        if self._mm is None:
            raise RuntimeError("call open() before reading")
        self._mm.seek(_HDR_OFFSET)
        raw = self._mm.read(_HDR_SIZE)
        buf_size, buf_index, running_byte, rate, updated = struct.unpack(_HDR_FMT, raw)
        return buf_size, buf_index, bool(running_byte), rate, updated

    @property
    def sample_rate(self) -> int:
        """Sample rate in Hz, as reported by squeezelite."""
        return self._read_header()[3]

    @property
    def running(self) -> bool:
        """True when squeezelite is actively writing audio data."""
        return self._read_header()[2]

    def read_new(self) -> np.ndarray:
        """Return mono float32 samples written since the last call.

        Handles circular-buffer wraparound.  If more than half the buffer
        was written since the last read (we fell behind the writer), logs a
        warning and returns the newest ``VIS_BUF_SIZE // 2`` samples instead
        of attempting to reconstruct the full overwritten history.

        Returns an empty array when no new samples are available.
        """
        _, buf_index, _, _, _ = self._read_header()

        n_new = (buf_index - self._prev_index) % VIS_BUF_SIZE
        self._prev_index = buf_index  # always advance, even if we return early

        if n_new == 0:
            return np.empty(0, dtype=np.float32)

        if n_new > VIS_BUF_SIZE // 2:
            _log.warning(
                "PCM tap fell behind: %d new samples since last read (buffer %d). "
                "Returning newest window.",
                n_new,
                VIS_BUF_SIZE,
            )
            n_new = VIS_BUF_SIZE // 2

        n_new -= n_new % 2  # round down to complete stereo frames
        if n_new == 0:
            return np.empty(0, dtype=np.float32)

        start = (buf_index - n_new) % VIS_BUF_SIZE

        assert self._mm is not None
        if start + n_new <= VIS_BUF_SIZE:
            self._mm.seek(_BUF_OFFSET + start * 2)
            raw = self._mm.read(n_new * 2)
        else:
            # Wraparound: two reads to reconstruct the contiguous window.
            tail = VIS_BUF_SIZE - start
            self._mm.seek(_BUF_OFFSET + start * 2)
            raw_tail = self._mm.read(tail * 2)
            self._mm.seek(_BUF_OFFSET)
            raw_head = self._mm.read((n_new - tail) * 2)
            raw = raw_tail + raw_head

        # Seqlock-style consistency check: if the writer advanced buf_index
        # while we were copying, the window may contain partially overwritten
        # samples.  Discard and let the next poll start fresh from buf_index.
        # This never takes the pthread_rwlock, so it cannot cause squeezelite
        # to skip exporting blocks.
        _, buf_index_after, _, _, _ = self._read_header()
        if buf_index_after != buf_index:
            _log.debug(
                "PCM tap: torn read detected (buf_index %d → %d), discarding block.",
                buf_index,
                buf_index_after,
            )
            return np.empty(0, dtype=np.float32)

        samples = np.frombuffer(raw, dtype=np.int16)
        # Interleaved stereo s16 → mono float32 in [−1.0, 1.0].
        # samples[0::2] = L channel, samples[1::2] = R channel.
        return (samples[0::2].astype(np.float32) + samples[1::2].astype(np.float32)) / (
            2.0 * 32768.0
        )


class PcmStft:
    """Rolling STFT over a stream of mono float32 samples.

    Accumulates samples in a ring buffer and emits one magnitude frame per hop.
    Call ``push()`` with each batch from ``SqueezeliteShmSource.read_new()``.

    Usage::

        stft = PcmStft(sample_rate=44100)
        frames = stft.push(mono_samples)   # list[np.ndarray], each shape (1025,)
    """

    def __init__(self, sample_rate: int) -> None:
        self._hop = round(sample_rate * 0.010)
        self._window = np.hamming(WINDOW_SIZE).astype(np.float32)
        self._buf = np.zeros(0, dtype=np.float32)

    @property
    def hop(self) -> int:
        """Number of samples between successive frames."""
        return self._hop

    @property
    def n_bins(self) -> int:
        """Number of real-valued frequency bins per frame (WINDOW_SIZE // 2 + 1)."""
        return WINDOW_SIZE // 2 + 1

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        """Append samples and return all complete magnitude frames.

        Each returned frame has shape (n_bins,) and dtype float32.
        Returns an empty list when fewer than WINDOW_SIZE samples are buffered.
        """
        self._buf = np.concatenate([self._buf, samples])
        frames: list[np.ndarray] = []
        while len(self._buf) >= WINDOW_SIZE:
            windowed = self._buf[:WINDOW_SIZE] * self._window
            mag = np.abs(np.fft.rfft(windowed)).astype(np.float32)
            frames.append(mag)
            self._buf = self._buf[self._hop:]
        return frames
