# HueSync

Spectrum-reactive Philips Hue Entertainment lighting for [Lyrion Music
Server](https://lyrion.org/) (formerly Logitech Media Server / Squeezebox).

HueSync registers a **virtual player** with your LMS server. Sync it to
whatever real player you're actually listening on (just like any other
multi-room LMS player group), and the room's Hue lights react live to the
music's spectrum and dynamics — no pre-computed BPM tags, no extra
microphone hardware.

## How it works

```
LMS (audio orchestration)
   |  slimproto
squeezelite -n "HueSync" -o hw:CARD=Dummy,DEV=0 -v   <- virtual player; snd-dummy for pacing
   |  shared memory (live PCM, /dev/shm/squeezelite-<mac>)
cava (shmem input, raw output -> FIFO)                <- FFT + log-spaced spectrum bars
   |  spectrum bars (30 Hz)
huesync / SyncEngine                                  <- analysis, normalisation, effect render
   |  Hue Entertainment API, up to 50 Hz (DTLS/UDP)
Hue Bridge -> Entertainment Area (your chosen room)
```

Three points worth understanding before you rely on this:

- **squeezelite's `-v` flag** is an official, built-in extension point
  ("visualiser support") that exposes a live audio buffer in shared memory,
  originally meant for on-device spectrum displays (jivelite).
  HueSync repurposes that same mechanism.
- **[cava](https://github.com/karlstav/cava)** has a `shmem` input module
  built specifically for squeezelite's shared memory format, and a `raw`
  output mode that writes fixed-size spectrum frames to a FIFO. HueSync
  doesn't do its own FFT — cava handles the DSP.
- **[hue-entertainment](https://github.com/music-assistant/hue-entertainment)**
  (the same library used by Music Assistant's Hue Entertainment plugin)
  handles the DTLS-PSK handshake and HueStream protocol. HueSync maps
  spectrum data to colour and calls `session.send(...)`.

## Hardware/software requirements

- A Hue Bridge (V2 "square" or Pro) with at least one
  [Entertainment Area](https://www.philips-hue.com/en-us/explore-hue/propositions/entertainment)
  configured in the official Hue app (HueSync cannot create areas itself —
  only the Hue app can).
- `squeezelite` and `cava` installed and on `PATH`.
- Python 3.11+.
- A Lyrion Music Server instance on the same network.
- **The `snd-dummy` kernel module loaded** — see below.

### Why snd-dummy is required

The virtual player needs an ALSA output device, but there is no real sound
card involved — we only want squeezelite's `-v` visualiser data. The obvious
choice, ALSA's built-in `null` plugin, is a trap: it discards samples the
instant they arrive with **no hardware clock**, so squeezelite decodes as fast
as the CPU allows rather than at playback speed. Measured on a Debian LXC that
meant ~100 % CPU on one core and LMS stuttering for every other player (a
single-threaded Perl server being hammered with stream requests from a client
consuming minutes of audio per second).

`snd-dummy` is a real, timer-driven ALSA card. squeezelite paces against it
exactly as it would against a physical DAC. Same setup, same track: **~0.2 %
CPU** instead of ~100 %, and no LMS stuttering.

On a bare-metal host or VM:

```bash
modprobe snd-dummy
echo "snd-dummy" > /etc/modules-load.d/snd-dummy.conf   # persist across reboots
```

In an **LXC container** the module must be loaded on the *host* (containers
share the host kernel), then the device nodes passed in. On Proxmox:

```bash
# On the host:
modprobe snd-dummy
echo "snd-dummy" > /etc/modules-load.d/snd-dummy.conf
cat /proc/asound/cards          # note the Dummy card's number, e.g. 1
ls -la /dev/snd/                # find controlCN and pcmCND0p for that number

pct set <CTID> -dev0 /dev/snd/controlC1,gid=29
pct set <CTID> -dev1 /dev/snd/pcmC1D0p,gid=29
pct reboot <CTID>
```

Verify inside the container with `squeezelite -l` — the Dummy card should be
listed alongside `null`.

### Bridge limitation

**A single Hue Bridge can stream to only one Entertainment Area at a time.**
HueSync's "profiles" concept exists because of this: you can configure
several profiles (different rooms, different colour styles), but only one is
ever *active* — activating a profile automatically deactivates whichever one
was running before. If you own multiple bridges, each gets its own set of
profiles and can run independently.

## Installation

```bash
apt install -y squeezelite cava python3-venv   # Debian/Ubuntu
git clone https://github.com/ThaYapeMan/SqueezeHue.git
cd SqueezeHue
python3 -m venv .venv && source .venv/bin/activate
pip install .
```

Run it:

```bash
huesync
```

The GUI listens on `http://<host>:8420`. Config (paired bridges, profiles) is
stored as JSON at `/etc/huesync/config.json` by default (override with the
`HUESYNC_CONFIG` environment variable) — a flat file, easy to inspect, back
up, or hand-edit.

### Running as a service

See [`systemd/huesync.service`](systemd/huesync.service). Copy it to
`/etc/systemd/system/`, adjust `User=`/`WorkingDirectory=` if needed, then:

```bash
systemctl daemon-reload
systemctl enable --now huesync
```

## Usage

1. **Pair a bridge**: press the physical link button, then submit the
   "Pair a new bridge" form within ~30 seconds.
2. **Create a profile**: point it at your LMS server (use the **Discover**
   button to find it on the local network automatically), give the virtual
   player a name, and pick a bridge + Entertainment Area (must already exist —
   create it in the Hue app first).
3. **Activate** the profile. In the LMS web UI (or any LMS controller app),
   find the new virtual player and **sync** it to your real listening player,
   exactly like setting up multi-room playback.
4. Play music. The lights react to the live spectrum.

To change a profile's settings later, click **Edit** in the profile list. The
form fills with the current values. If the profile was active when you save,
it is deactivated first — reactivate it manually to apply the new settings.

### Colour modes

| Mode | Behaviour |
|---|---|
| `spectrum_rgb` | Bass → red, mid → green, treble → blue. Punchy, colourful. |
| `mono_pulse` | Single colour, brightness follows overall loudness. Calmest option. |

### Profile fields

| Field | Default | Notes |
|---|---|---|
| `sensitivity` | 1.0 | Scales bar values before colour mapping. Raise for quiet recordings. |
| `brightness_floor` | 0.15 | Minimum brightness; keeps lights from going fully dark during quiet passages. |
| `bars` | 30 | Number of frequency bins cava analyses. More bins = finer detail. |
| `lower_cutoff_freq` | 50 Hz | Low end of the analysed frequency range. |
| `higher_cutoff_freq` | 12000 Hz | High end of the analysed frequency range. cava's default is 22000 Hz (Nyquist), but music has almost no energy above ~12 kHz — without an explicit cap the top third of the bar frame sits near zero and the treble channel never lights up. |
| `onset_sensitivity` | 1.5 | Controls onset detection threshold (*k* in `flux > mean + k × std`). Higher = fewer triggers. |
| `onset_cooldown_ms` | 120 ms | Minimum gap between consecutive onsets; prevents a single transient from triggering repeatedly. |
| `light_delay_ms` | 0 ms | Delays light output relative to the analysed audio (0–3000 ms). Useful when the audio source (e.g. Sonos) buffers 1–2 s and the lights arrive before the sound. |

## Architecture — effect pipeline

The pipeline is split into four protocol layers so that the output transport
and the colour logic never know about each other:

```
Analyser  →  AudioFeatures  →  Effect  →  Scene  →  Output
```

- **Analyser** (`CavaAnalyser`): reads cava bars from the FIFO, applies
  per-band AGC normalisation (`BandNormaliser`), and runs onset detection
  (`OnsetDetector`). Produces an `AudioFeatures` object each frame.
- **Effect** (`ColourModeEffect`, and future layered effects): receives
  `AudioFeatures`, returns a `Scene`.
- **Scene**: a callable `color_at(position, t) → Colour`. Effects never
  construct `LightColorCommand` objects or know about channel IDs — they
  describe a colour field in the room.
- **Output** (`HueDriver`): samples the `Scene` at each light's registered
  `(x, y, z)` position and sends the resulting `LightColorCommand`s to the
  bridge over DTLS/UDP.

This separation means:
- Effects are testable without a Hue bridge (pass a `NullOutput`).
- A second output driver (DMX, WLED, …) requires only a new `Output`
  implementation, not touching any effect or analysis code.
- Spatial effects (waves, fireworks) work by reading `position` — there is no
  "broadcast to all lights" API call; every light is sampled individually.

The full effect roadmap (layered Mellow/Active engine, palettes, onset-driven
effects) is documented in [`docs/EFFECT_ENGINE.md`](docs/EFFECT_ENGINE.md).

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

## Known limitations / roadmap

- One active stream per bridge (a Hue Bridge hardware limitation).
- Colour mapping currently uses the same colour for every light in the area.
  Per-light spatial mapping (e.g. bass on one side of the room, treble on the
  other) is concretely planned — see [`docs/EFFECT_ENGINE.md`](docs/EFFECT_ENGINE.md).
- No authentication on the GUI — intended for a trusted home LAN only. Run it
  behind your own reverse proxy/VPN if you need remote access.

## License

MIT, see [LICENSE](LICENSE).
