"""Small standalone helpers with no other module dependencies."""

from __future__ import annotations

import random


def format_player_name(name: str) -> str:
    """Format an LMS internal player name for display.

    LMS programs like sonos-squeezebox prefix names with their type and double
    colons, e.g. "SONOS::Study".  This converts that to "Study (Sonos)": the
    room/instance comes first with normal capitalisation, the type follows in
    parentheses.  Names without "::" are returned unchanged.
    """
    idx = name.find("::")
    if idx < 0:
        return name
    type_part = name[:idx].strip()
    room = name[idx + 2:].strip()
    type_fmt = type_part[0].upper() + type_part[1:].lower() if type_part else type_part
    return f"{room} ({type_fmt})"


def generate_locally_administered_mac() -> str:
    """A random MAC in the locally-administered range (02:xx:xx:xx:xx:xx).

    Locally-administered addresses are guaranteed to never collide with a
    real vendor-assigned MAC, which matters here since squeezelite's shared
    memory segment and LMS's player list are both keyed by this MAC.
    """
    octets = [0x02] + [random.randint(0x00, 0xFF) for _ in range(5)]
    return ":".join(f"{o:02x}" for o in octets)
