"""Data models for HueSync.

Kept intentionally simple (plain dataclasses, JSON-serialisable) so the
whole config can live in one human-readable, git-diffable file.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, fields
from enum import StrEnum

log = logging.getLogger(__name__)


class ColorMode(StrEnum):
    """How cava's spectrum bars are translated into a Hue colour.

    bass_brightness was removed: it was a poorly behaved legacy mode that
    mapped bass energy to a fixed warm hue.  Profiles that stored it are
    migrated to spectrum_rgb on load (see Profile.from_dict).
    """

    # Whole spectrum split in three bands (bass/mid/treble) mapped to R/G/B.
    SPECTRUM_RGB = "spectrum_rgb"
    # Single colour; brightness follows overall loudness.
    MONO_PULSE = "mono_pulse"


@dataclass
class BridgeConfig:
    """Credentials for one paired Hue Bridge."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Hue Bridge"
    host: str = ""
    app_key: str = ""
    client_key: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "app_key": self.app_key,
            "client_key": self.client_key,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BridgeConfig:
        return cls(**d)


#: Profile field names as a set — used to strip unknown keys when loading old
#: or future config files so cls(**d) never receives unexpected kwargs.
_PROFILE_FIELDS: frozenset[str] = frozenset()  # filled after class definition


@dataclass
class Profile:
    """One configured 'virtual player -> Hue Entertainment Area' pairing.

    Only one Profile can be *active* at a time per bridge - the Hue Bridge
    itself only supports a single Entertainment streaming session. HueSync
    enforces this in the player manager rather than letting the bridge
    reject a second stream with a confusing error.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "New profile"

    # LMS / virtual player
    lms_host: str = "127.0.0.1"
    lms_port: int = 3483
    player_name: str = "HueSync"
    player_mac: str = ""  # auto-generated on first save if left empty
    # ALSA output device for the virtual player. Empty means "use the
    # default" (snd-dummy, see player_manager.DEFAULT_ALSA_DEVICE). Only
    # set this if you have a reason to point squeezelite somewhere else.
    alsa_device: str = ""

    # Hue
    bridge_id: str = ""
    entertainment_area_id: str = ""
    entertainment_area_name: str = ""
    light_count: int = 0

    # Colour mapping
    color_mode: ColorMode = ColorMode.SPECTRUM_RGB
    sensitivity: float = 1.0  # multiplier applied to bar values before mapping
    brightness_floor: float = 0.15  # minimum brightness so lights never go fully dark
    bars: int = 30  # number of cava bars (more = finer frequency detail)
    # Analysed frequency range written into cava's [general] section.
    # Music has almost no energy above ~12 kHz; cava's default of 22000 Hz
    # (Nyquist for 44.1 kHz) leaves the top half of the bar frame near zero.
    # Option names verified against cava 0.10.7 (general:lower_cutoff_freq /
    # general:higher_cutoff_freq).
    lower_cutoff_freq: int = 50
    higher_cutoff_freq: int = 12000

    # Onset detection tuning (Dixon 2006 three-condition peak-picking).
    # onset_delta: margin above the asymmetric local mean required for condition 2.
    # Higher values = fewer, more confident onsets.
    onset_delta: float = 0.1
    # onset_alpha: per-frame decay of the adaptive suppression threshold (condition 3).
    # Higher values = longer suppression after a loud onset.  Range 0–1.
    onset_alpha: float = 0.9

    # Output delay.  Positive values delay the light output relative to the
    # analysed audio.  Use this when the audio source (e.g. Sonos) buffers
    # significantly and the lights arrive before the sound does.
    # Implemented as a ring buffer in SyncEngine; range 0-3000 ms.
    light_delay_ms: int = 0

    enabled: bool = True

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["color_mode"] = self.color_mode.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Profile:
        d = dict(d)

        # Migrate removed color modes to a safe default.
        if "color_mode" in d:
            try:
                d["color_mode"] = ColorMode(d["color_mode"])
            except ValueError:
                log.warning(
                    "Unsupported color_mode %r in saved profile; falling back to spectrum_rgb",
                    d["color_mode"],
                )
                d["color_mode"] = ColorMode.SPECTRUM_RGB

        # Strip keys that are not current Profile fields so that loading a
        # config written by a newer version of HueSync never causes a
        # TypeError, and loading a config with removed fields is harmless.
        d = {k: v for k, v in d.items() if k in _PROFILE_FIELDS}

        return cls(**d)


_PROFILE_FIELDS = frozenset(f.name for f in fields(Profile))
