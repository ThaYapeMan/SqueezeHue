"""Query the LMS CLI (port 9090) for a player's status.

The LMS CLI is a line-oriented TCP protocol.  The raw player MAC must be sent
as-is — URL-encoding the colons makes LMS silently fail to match the player.
Responses are URL-encoded; this module decodes them before parsing.

Standalone usage: python -m huesync.lms_status <host> <mac>
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass, field
from urllib.parse import unquote

DEFAULT_PORT: int = 9090
_SOCKET_TIMEOUT_S: float = 3.0

log = logging.getLogger(__name__)


@dataclass
class LmsPlayerStatus:
    """Parsed subset of the LMS CLI status response for one player."""

    time: float | None = None
    player_name: str | None = None      # display name of the queried player
    sync_master: str | None = None      # MAC of the sync-group master, None if standalone
    sync_slaves: list[str] = field(default_factory=list)   # MACs of sync slaves


def query_lms_status(host: str, mac: str, port: int = DEFAULT_PORT) -> LmsPlayerStatus:
    """Query a single player's status from the LMS CLI.

    *mac* is sent verbatim — LMS matches players by raw MAC and silently
    fails to find them if the colons are URL-encoded.  The response is
    fully URL-encoded by LMS, including the colon that separates keys from
    values (%3A).  See _parse_status for the parsing strategy.

    Raises OSError / socket.timeout on connection or timeout errors.
    """
    command = f"{mac} status - 1 tags:\n"
    log.info("LMS query: %s:%d player=%s", host, port, mac)
    with socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT_S) as sock:
        sock.sendall(command.encode("utf-8"))
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
    log.info("LMS raw response: %r", raw[:400])
    result = _parse_status(raw)
    log.info("LMS parsed: player_name=%r sync_master=%r sync_slaves=%r",
             result.player_name, result.sync_master, result.sync_slaves)
    return result


def _parse_status(text: str) -> LmsPlayerStatus:
    """Parse the LMS CLI status response into an LmsPlayerStatus.

    LMS URL-encodes the full response, including the colon that separates
    key from value (%3A).  The correct approach is therefore:

      1. Split on literal spaces — these are never encoded, they delimit tokens.
      2. Fully unquote each token — this decodes %3A to ":", %20 to " ", etc.
      3. partition(":") on the decoded token — the first ":" is the key/value
         separator; any further colons (e.g. in a MAC address) stay in the value.

    This handles all observed LMS response formats in one pass.
    """
    result = LmsPlayerStatus()
    for token in text.split():
        key, sep, value = unquote(token).partition(":")
        if not sep:
            continue
        if key == "time":
            try:
                result.time = float(value)
            except ValueError:
                pass
        elif key == "player_name":
            result.player_name = value or None
        elif key == "sync_master":
            result.sync_master = value or None
        elif key == "sync_slaves":
            result.sync_slaves = [s for s in value.split(",") if s]
    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python -m huesync.lms_status <host> <mac>", file=sys.stderr)
        sys.exit(1)
    _host, _mac = sys.argv[1], sys.argv[2]
    _status = query_lms_status(_host, _mac)
    print(f"time:         {_status.time}")
    print(f"player_name:  {_status.player_name}")
    print(f"sync_master:  {_status.sync_master}")
    print(f"sync_slaves:  {_status.sync_slaves}")
