# HueSync

Spectrum-reactive Philips Hue Entertainment lighting for [Lyrion Music
Server](https://lyrion.org/) (formerly Logitech Media Server / Squeezebox).

HueSync registers a **virtual player** with your LMS server. Sync it to
whatever real player you're actually listening on (just like any other
multi-room LMS player group), and the room's Hue lights react live to the
music's spectrum and dynamics - no pre-computed BPM tags, no extra
microphone hardware.

## How it works

```
LMS (audio orchestration)
   |  slimproto
squeezelite -n "HueSync" -o null -v     <- registers as an LMS player, no physical output
   |  shared memory (live PCM)
cava (shmem input, raw output -> FIFO)   <- does the FFT / spectrum analysis
   |  spectrum bars
huesync (this project)                   <- maps bars -> colour, sends via DTLS
   |  Hue Entertainment API, up to 50Hz
Hue Bridge -> Entertainment Area (your chosen room/group)
```

Three points worth understanding before you rely on this:

- **squeezelite's `-v` flag** is an official, built-in extension point
  ("visualiser support") that exposes a live audio buffer in shared memory,
  originally meant for on-device VU meters/spectrum displays (jivelite).
  HueSync repurposes that same mechanism.
- **[cava](https://github.com/karlstav/cava)** already has a `shmem` input
  module built specifically for squeezelite's shared memory format, and a
  `raw` output mode that writes fixed-size frames of spectrum data to a
  FIFO. HueSync doesn't do its own FFT - cava does the DSP.
- **[hue-entertainment](https://github.com/music-assistant/hue-entertainment)**
  (the same library used by Music Assistant's own Hue Entertainment plugin)
  handles the DTLS-PSK handshake and HueStream protocol - HueSync only maps
  spectrum data to colour and calls `session.send(...)`.

## Hardware/software requirements

- A Hue Bridge (V2 "square" or Pro) with at least one
  [Entertainment Area](https://www.philips-hue.com/en-us/explore-hue/propositions/entertainment)
  configured in the official Hue app (HueSync cannot create these areas
  itself - only the Hue app can).
- `squeezelite` and `cava` installed and on `PATH`.
- Python 3.11+.
- A Lyrion Music Server instance on the same network.
- **The `snd-dummy` kernel module loaded** - see below.

### Why snd-dummy is required

The virtual player needs an audio output device, but there's no real
sound card involved - we only want squeezelite's `-v` visualiser data,
not actual audio. The obvious choice, ALSA's built-in `null` device,
turns out to be a trap: it discards samples the instant they arrive and
has **no clock to pace against**, so squeezelite decodes as fast as the
CPU allows rather than at playback speed. Measured on a Debian LXC that
meant ~100% CPU on one core, and - worse - LMS itself stuttering for
every other player, because a single-threaded Perl server was being
hammered with stream requests from a client consuming minutes of audio
per second.

`snd-dummy` is a real, timer-driven ALSA card. squeezelite paces against
it exactly as it would against a physical DAC. Same setup, same track:
**~0.2% CPU** instead of ~100%, and no LMS stuttering.

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

Verify inside the container with `squeezelite -l` - the Dummy card should
be listed alongside `null`.

### Bridge limitation

**A single Hue Bridge can stream to only one Entertainment Area at a
time.** HueSync's "profiles" concept exists because of this: you can
configure several profiles (different rooms, different colour styles),
but only one is ever *active* - activating a profile automatically
deactivates whichever one was running before. If you own multiple bridges,
each gets its own set of profiles and can run independently (multi-bridge
support is on the roadmap, see below).

## Installation

```bash
apt install -y squeezelite cava   # Debian/Ubuntu
git clone https://github.com/<your-username>/huesync.git
cd huesync
pip install .
```

Run it:

```bash
huesync
```

The GUI listens on `http://<host>:8420`. Config (paired bridges, profiles)
is stored as JSON at `/etc/huesync/config.json` by default (override with
the `HUESYNC_CONFIG` environment variable) - it's a flat file, not a
database, so it's easy to inspect, back up, or hand-edit if needed.

### Running as a service

See [`systemd/huesync.service`](systemd/huesync.service). Copy it to
`/etc/systemd/system/`, adjust the `User=`/`WorkingDirectory=` if needed,
then:

```bash
systemctl daemon-reload
systemctl enable --now huesync
```

## Usage

1. **Pair a bridge**: press the physical link button, then submit the
   "Pair a new bridge" form within ~30 seconds.
2. **Create a profile**: point it at your LMS server, give the virtual
   player a name, and pick a bridge + Entertainment Area (the area must
   already exist - create it in the Hue app first).
3. **Activate** the profile. In the LMS web UI (or any LMS controller
   app), find the new virtual player and **sync** it to your real
   listening player, exactly like setting up multi-room playback.
4. Play music. The lights react to the live spectrum on your synced room.

### Colour modes

| Mode | Behaviour |
|---|---|
| `spectrum_rgb` | Bass -> red, mid -> green, treble -> blue. Punchy, colourful, good for upbeat music. |
| `bass_brightness` | Fixed warm hue, brightness driven by bass energy. Subtler, good as background mood lighting. |
| `mono_pulse` | Single colour, brightness follows overall loudness. Calmest option. |

`sensitivity` scales the input before mapping (raise it for quiet
recordings), `brightness_floor` keeps lights from going fully dark during
quiet passages, and `bars` controls how many frequency bins cava analyses
(more bars = finer detail, mostly relevant for `spectrum_rgb`).

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## Known limitations / roadmap

- One active stream per bridge (a Hue Bridge limitation, not fixable
  client-side) - multi-bridge support so different rooms can run
  independently is a natural next step.
- Colour mapping applies the same colour to every light in the area; a
  per-light spatial mapping (e.g. bass on one side of the room, treble on
  the other, using each `LightChannel`'s `position`) is a good follow-up.
- No authentication on the GUI - intended for a trusted home LAN only, run
  it behind your own reverse proxy/VPN if you need remote access.

## License

MIT, see [LICENSE](LICENSE).
