from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import hue_bridge
from .models import ColorMode, Profile
from .player_manager import PlayerManager
from .storage import Storage
from .util import generate_locally_administered_mac

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
CONFIG_PATH = os.environ.get("HUESYNC_CONFIG", "/etc/huesync/config.json")

app = FastAPI(title="HueSync")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

storage = Storage(CONFIG_PATH)
player_manager = PlayerManager(storage)


@app.on_event("startup")
async def on_startup() -> None:
    # A previously "active" profile from before a restart has no real
    # squeezelite/cava process behind it anymore - clear the stale state
    # rather than pretending it's still running.
    storage.set_active_profile_id(None)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await player_manager.deactivate()


# -- Pages ------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    bridges = storage.list_bridges()
    profiles = storage.list_profiles()
    active_id = player_manager.active_profile_id
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "bridges": bridges,
            "profiles": profiles,
            "active_id": active_id,
            "color_modes": list(ColorMode),
        },
    )


# -- Bridges ------------------------------------------------------------------


@app.post("/bridges/pair")
async def pair_bridge(host: str = Form(...), name: str = Form("Hue Bridge")):
    bridge = await hue_bridge.pair(host, bridge_name=name)
    storage.save_bridge(bridge)
    return RedirectResponse("/", status_code=303)


@app.post("/bridges/{bridge_id}/delete")
async def delete_bridge(bridge_id: str):
    storage.delete_bridge(bridge_id)
    return RedirectResponse("/", status_code=303)


@app.get("/bridges/{bridge_id}/areas")
async def bridge_areas(bridge_id: str):
    bridge = storage.get_bridge(bridge_id)
    if bridge is None:
        return []
    areas = await hue_bridge.list_entertainment_areas(bridge)
    return [{"id": a.id, "name": a.name, "light_count": a.light_count} for a in areas]


# -- Profiles -----------------------------------------------------------------


@app.post("/profiles")
async def save_profile(
    profile_id: str = Form(""),
    name: str = Form(...),
    lms_host: str = Form(...),
    lms_port: int = Form(3483),
    player_name: str = Form(...),
    bridge_id: str = Form(...),
    entertainment_area_id: str = Form(...),
    entertainment_area_name: str = Form(""),
    color_mode: str = Form(ColorMode.SPECTRUM_RGB.value),
    sensitivity: float = Form(1.0),
    brightness_floor: float = Form(0.15),
    bars: int = Form(30),
):
    existing = storage.get_profile(profile_id) if profile_id else None
    profile = existing or Profile()
    if profile_id:
        profile.id = profile_id
    profile.name = name
    profile.lms_host = lms_host
    profile.lms_port = lms_port
    profile.player_name = player_name
    profile.bridge_id = bridge_id
    profile.entertainment_area_id = entertainment_area_id
    profile.entertainment_area_name = entertainment_area_name
    profile.color_mode = ColorMode(color_mode)
    profile.sensitivity = sensitivity
    profile.brightness_floor = brightness_floor
    profile.bars = bars
    if not profile.player_mac:
        profile.player_mac = generate_locally_administered_mac()

    storage.save_profile(profile)
    return RedirectResponse("/", status_code=303)


@app.post("/profiles/{profile_id}/delete")
async def delete_profile(profile_id: str):
    if player_manager.active_profile_id == profile_id:
        await player_manager.deactivate()
    storage.delete_profile(profile_id)
    return RedirectResponse("/", status_code=303)


@app.post("/profiles/{profile_id}/activate")
async def activate_profile(profile_id: str):
    profile = storage.get_profile(profile_id)
    if profile is None:
        return RedirectResponse("/", status_code=303)
    await player_manager.activate(profile)
    return RedirectResponse("/", status_code=303)


@app.post("/profiles/deactivate")
async def deactivate_profile():
    await player_manager.deactivate()
    return RedirectResponse("/", status_code=303)


# -- Live preview ---------------------------------------------------------------


@app.websocket("/ws/preview")
async def ws_preview(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            commands = player_manager.last_commands
            if commands:
                c = commands[0]
                await websocket.send_json({"r": c.red, "g": c.green, "b": c.blue})
            else:
                await websocket.send_json({"r": 0, "g": 0, "b": 0})
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    with contextlib.suppress(Exception):
        await websocket.close()


def main() -> None:
    uvicorn.run("huesync.app:app", host="0.0.0.0", port=8420, reload=False)


if __name__ == "__main__":
    main()
