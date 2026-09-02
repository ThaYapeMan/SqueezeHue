# HueSync — project context for Claude Code

## Language convention

All code comments, docstrings, commit messages, and documentation must be
written in **English**. Conversational responses to the user may be in Dutch.

## What this project does

HueSync makes Philips Hue Entertainment lights react to music playing in
Lyrion Music Server (LMS). The signal chain is:

```
LMS  →  squeezelite (virtual player)  →  ALSA shared memory
     →  cava (spectrum analyser)      →  FIFO (named pipe)
     →  SyncEngine (Python)           →  Hue Entertainment API (UDP/DTLS)
```

- **squeezelite** registers as a virtual player in LMS and decodes the audio
  stream into PCM. It writes the PCM into a POSIX shared-memory segment at
  `/dev/shm/squeezelite-<mac>`.
- **cava** reads that shared-memory segment (`method = shmem`), computes a
  bar spectrum (configurable number of bars, 8-bit, mono), and writes raw
  frames to a named FIFO.
- **SyncEngine** (`sync_engine.py`) reads the FIFO in a background thread
  (`FifoReader`), normalises the spectrum via `BandNormaliser`, maps it to
  RGB via the active `ColorMode`, and sends `LightColorCommand`s to the Hue
  bridge over a DTLS-secured UDP stream at a fixed 30 Hz rate.

## Non-obvious design decisions — do not "clean up"

### snd-dummy instead of ALSA null (`player_manager.py`)

squeezelite's ALSA output is pointed at `hw:CARD=Dummy,DEV=0` (the
`snd-dummy` kernel module), **not** the `null` ALSA plugin. The null plugin
has no hardware clock: squeezelite decodes as fast as the CPU allows, pinning
a core at ~100 % and hammering LMS with stream requests (LMS is
single-threaded Perl). snd-dummy is a real timer-driven card; squeezelite
paces at actual playback speed (~0.2 % CPU). Requires `snd-dummy` on the
host kernel and the resulting `/dev/snd` nodes passed into the container.

### FIFO reader starts before cava (`player_manager.py` — `activate()`)

A FIFO with a writer but no reader delivers SIGPIPE to the writer. If cava
starts before `SyncEngine.start()` opens the read end, cava dies immediately
and silently. The activation order is therefore:

1. `_create_fifo()` — creates the FIFO on disk
2. `engine.start()` — opens the read end (blocks until a writer appears)
3. `_start_cava()` — spawns cava as the writer

### Wait for squeezelite's SHM segment (`player_manager.py`)

squeezelite creates `/dev/shm/squeezelite-<mac>` a moment after it starts.
`_wait_for_shm()` polls (0.1 s interval, 10 s timeout) before writing cava's
config and spawning cava. Without this wait, cava exits immediately on a new
profile with "Could not open source".

### 30 Hz sender cap (`sync_engine.py`)

The Hue Entertainment API accepts up to ~50 updates/s, but cava can produce
frames much faster than real-time (especially against a virtual ALSA device
with no hardware clock). Queueing every frame overwhelms the asyncio event
loop and starves the sender coroutine, which then triggers the bridge's
10-second idle timeout (the bridge closes the stream if it receives no frames
for 10 s). `FifoReader` keeps only the *latest* frame in a lock-protected
slot; the sender polls it at a fixed `SEND_INTERVAL_S = 1/30`.

### cava stderr kept, not discarded (`player_manager.py`)

cava's stderr is redirected to a log file (`<profile-id>.cava.log`) rather
than `/dev/null`. Discarding it made silent failures (cava exiting as
`<defunct>` with no error message) very hard to debug.

### BandNormaliser — two clipping ceilings (`sync_engine.py`)

The audio→colour pipeline has two independent upper limits that interact:

1. **`BandNormaliser._EXERTION_CLIP` (default 2.0×)** — a *musical scale
   choice*. Exertion is clipped at 2× the band's rolling average, then
   encoded as bytes 0–255 (128 = at average, 255 = 2× average). Raising
   this makes the output more sensitive to brief spikes.
2. **`frame_to_commands()` clip at 1.0** — a *safety ceiling* before the
   16-bit RGB conversion, after multiplying by `profile.sensitivity`.

With normalised input, `sensitivity = 1.0` keeps steady-state music at
roughly half-brightness with brief peaks at full. Raising sensitivity above
~2.0 pushes the steady state into saturation. The default of 1.0 is correct
starting point post-normalisation.

## Deployment

The runtime environment is a **dedicated LXC container** on a Proxmox host.
There is no local Python runtime on the development machine. Deployment is:

```
# on the LXC
cd /opt/huesync && git pull && systemctl restart huesync
```

The service runs as user `huesync` under systemd (`systemd/huesync.service`).
Config lives at `/etc/huesync/config.json` (`HUESYNC_CONFIG` env var).

**Do not assume `python`, `pytest`, `uv`, or `pip` are available locally.**
Tests can be verified by stubbing `hue_entertainment` and running with
`python3 -c "..."` if no venv is present (see test runs in session history).

## Key files

| File | Role |
|------|------|
| `src/huesync/sync_engine.py` | Audio→colour pipeline: `FifoReader`, `BandNormaliser`, `frame_to_commands`, `SyncEngine` |
| `src/huesync/player_manager.py` | Process lifecycle: squeezelite + cava + Hue session |
| `src/huesync/models.py` | `Profile`, `BridgeConfig`, `ColorMode` |
| `src/huesync/app.py` | FastAPI web UI |
| `src/huesync/hue_bridge.py` | Hue bridge pairing and area discovery |
| `src/huesync/storage.py` | JSON config persistence |
| `tests/test_sync_engine.py` | Unit tests for the audio pipeline |
| `tests/test_storage.py` | Unit tests for config persistence |
| `docs/` | Design briefs (not deployed) |
