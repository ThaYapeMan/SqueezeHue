from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import __git_hash__, __version__
from .api import router as api_router
from .player_manager import PlayerManager
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_PATH = os.environ.get("HUESYNC_CONFIG", "/etc/huesync/config.json")

app = FastAPI(title="HueSync")

storage = Storage(CONFIG_PATH)
player_manager = PlayerManager(storage)

app.state.storage = storage
app.state.player_manager = player_manager
app.include_router(api_router)

# Serve the React SPA at /. html=True makes StaticFiles serve index.html for
# unknown paths so client-side routing works (only relevant if we add it later).
_WEBUI_DIR = BASE_DIR / "webui"
if _WEBUI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_WEBUI_DIR), html=True), name="webui")


@app.on_event("startup")
async def on_startup() -> None:
    # A previously "active" profile from before a restart has no real
    # squeezelite/cava process behind it anymore - clear the stale state
    # rather than pretending it's still running.
    storage.set_active_profile_id(None)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await player_manager.deactivate()


# -- Live preview ---------------------------------------------------------------


@app.websocket("/ws/preview")
async def ws_preview(websocket: WebSocket):
    await websocket.accept()
    last_status_json: str | None = None
    tick = 0
    try:
        while True:
            colours = player_manager.last_colours
            onset = player_manager.last_onset
            if colours:
                r, g, b = colours[0].to_16bit()
            else:
                r = g = b = 0

            await websocket.send_json({
                "type": "frame",
                "colour": {"r": r, "g": g, "b": b},
                "onset": onset,
            })

            if tick % 3 == 0:
                await websocket.send_json({
                    "type": "spectrum",
                    "bars": player_manager.last_bars,
                })

            status_dict = {
                "type": "status",
                "version": f"{__version__}+{__git_hash__}",
                "active_profile_id": player_manager.active_profile_id,
                "active_profile_name": player_manager.active_profile_name,
                "sync_master": player_manager.detected_sync_master,
                "sync_master_name": player_manager.detected_sync_master_name,
                "applied_delay_ms": player_manager.applied_delay_ms,
                "latency_warning": player_manager.latency_warning,
                "processes": player_manager.process_status,
                "bridge_connected": player_manager.bridge_connected,
                "color_mode": player_manager.active_color_mode,
            }
            status_json = json.dumps(status_dict, sort_keys=True)
            if status_json != last_status_json:
                await websocket.send_json(status_dict)
                last_status_json = status_json

            tick += 1
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    with contextlib.suppress(Exception):
        await websocket.close()


def main() -> None:
    uvicorn.run("huesync.app:app", host="0.0.0.0", port=8420, reload=False)


if __name__ == "__main__":
    main()
