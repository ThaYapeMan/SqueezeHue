from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import hue_bridge
from .api import router as api_router
from .lms_discovery import discover_lms
from .models import ColorMode, PlayerLatency, Profile
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

app.state.storage = storage
app.state.player_manager = player_manager
app.include_router(api_router)


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


def _page_ctx(request: Request, **extras) -> dict:
    """Build the base template context, merged with any extra keys."""
    return {
        "bridges": storage.list_bridges(),
        "profiles": storage.list_profiles(),
        "player_latencies": storage.list_player_latencies(),
        "active_id": player_manager.active_profile_id,
        "color_modes": list(ColorMode),
        **extras,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", _page_ctx(request))


# -- Bridges ------------------------------------------------------------------


@app.post("/bridges/pair")
async def pair_bridge(request: Request, host: str = Form(...), name: str = Form("Hue Bridge")):
    try:
        bridge = await hue_bridge.pair(host, bridge_name=name)
    except TimeoutError:
        return templates.TemplateResponse(
            request,
            "index.html",
            _page_ctx(
                request,
                pair_error=(
                    "Pairing timed out. Press the physical link button on the bridge, "
                    "then submit this form again within ~30 seconds."
                ),
            ),
            status_code=400,
        )
    storage.save_bridge(bridge)
    return RedirectResponse("/", status_code=303)


@app.post("/bridges/{bridge_id}/delete")
async def delete_bridge(bridge_id: str):
    storage.delete_bridge(bridge_id)
    return RedirectResponse("/", status_code=303)


@app.get("/lms/discover")
async def lms_discover():
    """Discover Lyrion Music Servers on the local network via UDP broadcast.

    Only finds servers on the same subnet.  Returns a list of objects with
    host, name, json_port, uuid, and version fields.
    """
    servers = await discover_lms(timeout=3.0)
    return [
        {
            "host": s.host,
            "name": s.name,
            "json_port": s.json_port,
            "uuid": s.uuid,
            "version": s.version,
        }
        for s in servers
    ]


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
    lower_cutoff_freq: int = Form(50),
    higher_cutoff_freq: int = Form(12000),
    onset_delta: float = Form(0.1),
    onset_alpha: float = Form(0.9),
    exertion_clip: float = Form(3.0),
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
    profile.lower_cutoff_freq = lower_cutoff_freq
    profile.higher_cutoff_freq = higher_cutoff_freq
    profile.onset_delta = onset_delta
    profile.onset_alpha = onset_alpha
    profile.exertion_clip = exertion_clip
    profile.bars = bars
    if not profile.player_mac:
        profile.player_mac = generate_locally_administered_mac()

    # If this profile was active, deactivate it before saving so the running
    # processes are not left with stale config.  The user must reactivate it
    # manually; we never silently restart so they can choose the right moment.
    was_active = bool(profile_id and player_manager.active_profile_id == profile_id)
    if was_active:
        await player_manager.deactivate()

    storage.save_profile(profile)

    if was_active:
        return RedirectResponse(
            "/?info=Profile+saved.+It+was+deactivated%3B+reactivate+it+to+apply+the+changes.",
            status_code=303,
        )
    return RedirectResponse("/", status_code=303)


@app.post("/profiles/{profile_id}/delete")
async def delete_profile(profile_id: str):
    if player_manager.active_profile_id == profile_id:
        await player_manager.deactivate()
    storage.delete_profile(profile_id)
    return RedirectResponse("/", status_code=303)


@app.post("/profiles/{profile_id}/activate")
async def activate_profile(request: Request, profile_id: str):
    profile = storage.get_profile(profile_id)
    if profile is None:
        return RedirectResponse("/", status_code=303)
    try:
        await player_manager.activate(profile)
    except Exception as exc:  # noqa: BLE001 - surface any activation failure to the GUI
        log.exception("Failed to activate profile %s", profile.name)
        return templates.TemplateResponse(
            request,
            "index.html",
            _page_ctx(
                request,
                pair_error=f"Could not activate profile '{profile.name}': {exc}",
            ),
            status_code=400,
        )
    return RedirectResponse("/", status_code=303)


@app.post("/profiles/deactivate")
async def deactivate_profile():
    await player_manager.deactivate()
    return RedirectResponse("/", status_code=303)


# -- Player latencies -----------------------------------------------------------


@app.post("/player-latencies")
async def save_player_latency(
    player_mac: str = Form(...),
    strategy: str = Form("fixed"),
    fixed_delay_ms: int = Form(2000),
    speaker_ip: str = Form(""),
):
    pl = PlayerLatency(
        player_mac=player_mac.strip().lower(),
        strategy=strategy,
        fixed_delay_ms=fixed_delay_ms,
        speaker_ip=speaker_ip.strip() or None,
    )
    storage.save_player_latency(pl)
    await player_manager.refresh_probe()
    return RedirectResponse("/", status_code=303)


@app.post("/player-latencies/delete")
async def delete_player_latency(player_mac: str = Form(...)):
    storage.delete_player_latency(player_mac)
    await player_manager.refresh_probe()
    return RedirectResponse("/", status_code=303)


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
                "active_profile_id": player_manager.active_profile_id,
                "sync_master": player_manager.detected_sync_master,
                "applied_delay_ms": player_manager.applied_delay_ms,
                "latency_warning": player_manager.latency_warning,
                "processes": player_manager.process_status,
                "bridge_connected": player_manager.bridge_connected,
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
