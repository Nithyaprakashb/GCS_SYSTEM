"""FastAPI surface for the RescueSwarm GCS.

The server binds to loopback only. It exposes state, operator actions and the
local MJPEG feeds to the Electron renderer. No endpoint accepts arbitrary shell
commands or raw MAVLink frames, and the copilot cannot reach the command path.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .config import APP_VERSION, SAMPLES_DIR
from .mission_engine import MissionError
from .mavlink import TransportError

router = APIRouter(prefix="/api")
ws_router = APIRouter()


def get_state(request: Request) -> Any:
    return request.app.state.app_state


def operator(request: Request) -> str:
    return (request.headers.get("X-Operator") or "operator").strip()[:64] or "operator"


def http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc).strip("'"))
    if isinstance(exc, (MissionError, TransportError, ValueError, RuntimeError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# --------------------------------------------------------------------- models
class ModeBody(BaseModel):
    mode: str = Field(pattern="^(SIMULATION|REAL|simulation|real)$")


class KmlBody(BaseModel):
    filename: str = "mission.kml"
    kml_text: str | None = None
    sample: bool = False


class SignBody(BaseModel):
    check_id: str
    signed: bool = True


class StartBody(BaseModel):
    confirmation: str = ""


class ReasonBody(BaseModel):
    reason: str = "Operator action"


class VideoStartBody(BaseModel):
    mode: str | None = None


class VideoFaultBody(BaseModel):
    fault: str | None = None


class FaultBody(BaseModel):
    fault: str
    callsign: str | None = None
    value: Any = None


class ConfigBody(BaseModel):
    patch: dict[str, Any]


class AskBody(BaseModel):
    question: str


# ---------------------------------------------------------------------- health
@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    state = get_state(request)
    return {
        "ok": True,
        "version": APP_VERSION,
        "mode": state.mission.mode,
        "phase": state.mission.phase,
        "mission_id": state.mission.mission_id,
        "offline": True,
    }


@router.get("/state")
async def full_state(request: Request) -> dict[str, Any]:
    return get_state(request).ui_snapshot()


# ---------------------------------------------------------------------- system
@router.get("/system/info")
async def system_info(request: Request) -> dict[str, Any]:
    state = get_state(request)
    return await asyncio.to_thread(state.system.environment)


@router.get("/system/diagnostics")
async def system_diagnostics(request: Request) -> dict[str, Any]:
    state = get_state(request)
    return await asyncio.to_thread(state.system.diagnostics)


@router.get("/system/devices")
async def system_devices(request: Request) -> dict[str, Any]:
    state = get_state(request)
    return await asyncio.to_thread(state.system.devices)


@router.get("/system/config")
async def system_config(request: Request) -> dict[str, Any]:
    return get_state(request).system.config_view()


@router.patch("/system/config")
async def update_config(request: Request, body: ConfigBody) -> dict[str, Any]:
    state = get_state(request)
    try:
        updated = state.config.update(body.patch)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state.bus.publish("operator.action",
                      {"action": "CONFIG_UPDATE", "sections": sorted(body.patch.keys()),
                       "operator": operator(request)},
                      source="OPERATOR", data_quality="SYSTEM")
    return {"updated": updated, "note": "Restart telemetry/video services to apply transport changes."}


@router.post("/system/mode")
async def set_mode(request: Request, body: ModeBody) -> dict[str, Any]:
    state = get_state(request)
    try:
        return await state.set_mode(body.mode, operator(request))
    except Exception as exc:
        raise http_error(exc) from exc


# ----------------------------------------------------------------------- fleet
@router.get("/fleet")
async def fleet(request: Request) -> dict[str, Any]:
    state = get_state(request)
    return {
        "fleet": state.telemetry.snapshot(),
        "safety": state.safety.evaluate_fleet(state.telemetry.vehicles, state.mission.aoi),
        "generated_at": state.bus.recent(1)[-1]["receive_time"] if state.bus.recent(1) else None,
    }


@router.post("/fleet/connect")
async def fleet_connect(request: Request, body: ModeBody) -> dict[str, Any]:
    state = get_state(request)
    try:
        return await state.connect_fleet(body.mode, operator(request))
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/fleet/disconnect")
async def fleet_disconnect(request: Request) -> dict[str, Any]:
    state = get_state(request)
    try:
        return await state.disconnect_fleet()
    except Exception as exc:
        raise http_error(exc) from exc


# ----------------------------------------------------------------------- video
@router.get("/video/health")
async def video_health(request: Request) -> dict[str, Any]:
    return get_state(request).video.health()


@router.post("/video/start")
async def video_start(request: Request, body: VideoStartBody | None = None) -> dict[str, Any]:
    state = get_state(request)
    mode = (body.mode if body and body.mode else state.mission.mode).upper()
    if mode not in {"SIMULATION", "REAL"}:
        raise HTTPException(status_code=400, detail="Unsupported video mode")
    result = await asyncio.to_thread(state.video.start, mode)
    state.bus.publish("operator.action",
                      {"action": "VIDEO_START", "mode": mode, "operator": operator(request)},
                      source="OPERATOR", data_quality="SYSTEM")
    return result


@router.post("/video/stop")
async def video_stop(request: Request) -> dict[str, Any]:
    state = get_state(request)
    state.video.stop()
    return state.video.health()


@router.post("/video/{callsign}/inject")
async def video_inject(request: Request, callsign: str, body: VideoFaultBody) -> dict[str, Any]:
    state = get_state(request)
    if state.mission.mode != "SIMULATION":
        raise HTTPException(status_code=403, detail="Video faults can only be injected in SIMULATION")
    try:
        return state.video.inject(callsign, body.fault)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/video/{callsign}/frame.jpg")
async def video_frame(request: Request, callsign: str) -> Response:
    state = get_state(request)
    if callsign not in state.video.feeds:
        raise HTTPException(status_code=404, detail=f"Unknown feed {callsign}")
    frame = state.video.latest_frame(callsign)
    if not frame:
        return JSONResponse(status_code=503, content={"detail": "No frame available"})
    return Response(content=frame, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.get("/video/{callsign}/stream")
async def video_stream(request: Request, callsign: str) -> StreamingResponse:
    state = get_state(request)
    if callsign not in state.video.feeds:
        raise HTTPException(status_code=404, detail=f"Unknown feed {callsign}")
    return StreamingResponse(
        state.video.mjpeg_stream(callsign),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Feed": callsign},
    )


# --------------------------------------------------------------------- mission
@router.get("/mission/status")
async def mission_status(request: Request) -> dict[str, Any]:
    return get_state(request).mission.status()


@router.get("/mission/manual-items")
async def manual_items(request: Request) -> dict[str, Any]:
    return {"items": get_state(request).preflight.manual_items()}


@router.post("/mission/import-kml")
async def import_kml(request: Request, body: KmlBody) -> dict[str, Any]:
    state = get_state(request)
    if body.sample:
        sample = SAMPLES_DIR / "sample_search_zone_10ha.kml"
        if not sample.exists():
            raise HTTPException(status_code=404, detail="Sample KML not found")
        content = sample.read_bytes()
        filename = sample.name
    elif body.kml_text:
        content = body.kml_text.encode("utf-8")
        filename = body.filename or "mission.kml"
    else:
        raise HTTPException(status_code=400, detail="Provide kml_text or sample=true")
    try:
        return await asyncio.to_thread(state.mission.import_kml, content, filename, operator(request))
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/mission/load-sample")
async def load_sample(request: Request) -> dict[str, Any]:
    return await import_kml(request, KmlBody(sample=True))


@router.post("/mission/plan")
async def create_plan(request: Request) -> dict[str, Any]:
    state = get_state(request)
    try:
        return await asyncio.to_thread(state.mission.create_plan, operator(request))
    except Exception as exc:
        raise http_error(exc) from exc


@router.get("/mission/gate")
async def mission_gate(request: Request) -> dict[str, Any]:
    state = get_state(request)
    try:
        return await asyncio.to_thread(state.mission.gate)
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/mission/preflight")
async def run_preflight(request: Request) -> dict[str, Any]:
    state = get_state(request)
    try:
        return await asyncio.to_thread(state.mission.run_preflight, operator(request))
    except Exception as exc:
        raise http_error(exc) from exc


@router.get("/mission/preflight")
async def last_preflight(request: Request) -> dict[str, Any]:
    state = get_state(request)
    return state.preflight.last_report or {"ready": False, "checks": [], "detail": "Not run"}


@router.post("/mission/sign")
async def sign(request: Request, body: SignBody) -> dict[str, Any]:
    state = get_state(request)
    try:
        return state.mission.sign_manual(body.check_id, body.signed, operator(request))
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/mission/sign-all-sim")
async def sign_all_sim(request: Request) -> dict[str, Any]:
    state = get_state(request)
    try:
        return state.mission.sign_all_simulation(operator(request))
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/mission/upload")
async def upload_mission(request: Request) -> dict[str, Any]:
    state = get_state(request)
    try:
        return await state.mission.upload_mission(operator(request))
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/mission/start")
async def start_mission(request: Request, body: StartBody) -> dict[str, Any]:
    state = get_state(request)
    try:
        return state.mission.start_mission(operator(request), body.confirmation)
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/mission/abort")
async def abort_mission(request: Request, body: ReasonBody) -> dict[str, Any]:
    state = get_state(request)
    try:
        return await asyncio.to_thread(state.mission.abort, operator(request), body.reason)
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/mission/recall")
async def recall(request: Request, body: ReasonBody) -> dict[str, Any]:
    state = get_state(request)
    try:
        return state.mission.emergency_recall(operator(request), body.reason)
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/mission/recall/clear")
async def recall_clear(request: Request) -> dict[str, Any]:
    state = get_state(request)
    return state.mission.clear_recall(operator(request))


@router.post("/mission/reset")
async def mission_reset(request: Request) -> dict[str, Any]:
    """Clear a completed or aborted session so a new sortie can be prepared.

    The previous mission, its events and its evidence remain on disk; only the
    in-memory working session is reset. Executing missions cannot be reset.
    """
    state = get_state(request)
    if state.mission.phase == "EXECUTING":
        raise HTTPException(status_code=409, detail="Abort or finish the executing mission before starting a new one")
    previous = state.mission.mission_id
    state.mission.reset_session()
    if state.simulation is not None and state.mission.mode == "SIMULATION":
        # Keep the simulated fleet alive at the pad so the operator sees the
        # same link state during the next setup without fabricating a launch.
        state.simulation.start_bench()
    state.bus.publish(
        "operator.action",
        {"action": "NEW_MISSION", "operator": operator(request), "previous_mission_id": previous},
        source="GCS", data_quality="SYSTEM",
    )
    return state.mission.status()


# --------------------------------------------------------------- fleet products
@router.get("/survivors")
async def survivors(request: Request) -> dict[str, Any]:
    return get_state(request).survivors.snapshot()


@router.get("/survivors/{survivor_id}")
async def survivor(request: Request, survivor_id: str) -> dict[str, Any]:
    found = get_state(request).survivors.get(survivor_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"Unknown survivor {survivor_id}")
    return found


@router.get("/kits")
async def kits(request: Request) -> dict[str, Any]:
    return get_state(request).ledger.snapshot()


@router.get("/allocations")
async def allocations(request: Request) -> dict[str, Any]:
    return get_state(request).allocator.snapshot()


@router.get("/deliveries")
async def deliveries(request: Request) -> dict[str, Any]:
    return get_state(request).delivery.snapshot()


@router.get("/events")
async def recent_events(request: Request, limit: int = 100) -> dict[str, Any]:
    return {"events": get_state(request).bus.recent(max(1, min(500, limit)))}


# ---------------------------------------------------------- map, replay, reports
@router.get("/map/style")
async def map_style(request: Request) -> dict[str, Any]:
    return get_state(request).tiles.style()


@router.get("/map/tiles/{z}/{x}/{y}.png")
async def map_tile(request: Request, z: int, x: int, y: int, theme: str = "dark") -> Response:
    state = get_state(request)
    try:
        data = await asyncio.to_thread(state.tiles.tile, z, x, y, theme == "light")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/missions")
async def missions(request: Request, limit: int = 30) -> dict[str, Any]:
    state = get_state(request)
    return {"missions": state.db.list_missions(max(1, min(100, limit)))}


@router.get("/missions/{mission_id}")
async def mission_record(request: Request, mission_id: str) -> dict[str, Any]:
    state = get_state(request)
    record = state.db.get_mission(mission_id)
    if not record:
        raise HTTPException(status_code=404, detail="Unknown mission")
    return {
        "mission": record,
        "plan": state.db.get_latest_plan(mission_id),
        "survivors": state.db.get_survivors(mission_id),
        "assignments": state.db.get_assignments(mission_id),
        "kits": state.db.get_kits(mission_id),
        "events": state.db.get_events(mission_id, limit=300),
    }


@router.get("/missions/{mission_id}/replay")
async def mission_replay(request: Request, mission_id: str) -> dict[str, Any]:
    state = get_state(request)
    try:
        return await asyncio.to_thread(state.replay.replay, mission_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@router.get("/missions/{mission_id}/analytics")
async def mission_analytics(request: Request, mission_id: str) -> dict[str, Any]:
    state = get_state(request)
    if not state.db.get_mission(mission_id):
        raise HTTPException(status_code=404, detail="Unknown mission")
    return await asyncio.to_thread(state.reporter.analytics, mission_id)


@router.post("/missions/{mission_id}/report")
async def regenerate_report(request: Request, mission_id: str) -> dict[str, Any]:
    state = get_state(request)
    record = state.db.get_mission(mission_id)
    if not record:
        raise HTTPException(status_code=404, detail="Unknown mission")
    summary = state.mission.completion_summary if state.mission.mission_id == mission_id else None
    if not summary:
        summary = state.reporter._summary_from_db(mission_id)
        summary["delivery"] = {"policy": "Report regenerated from recorded data."}
    plan = state.db.get_latest_plan(mission_id)
    aoi = (plan or {}).get("aoi")
    report = await asyncio.to_thread(state.reporter.generate, mission_id, summary, plan, aoi)
    return {"generated": True, "report": report}


@router.get("/missions/{mission_id}/report")
async def mission_report(request: Request, mission_id: str) -> dict[str, Any]:
    state = get_state(request)
    path = state.evidence.path(mission_id, "reports/report.json")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report has not been generated")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/missions/{mission_id}/report.pdf")
async def mission_report_pdf(request: Request, mission_id: str) -> FileResponse:
    state = get_state(request)
    path = state.evidence.path(mission_id, "reports/report.pdf")
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF report has not been generated")
    return FileResponse(path, media_type="application/pdf", filename=f"{mission_id}-report.pdf")


@router.get("/missions/{mission_id}/events")
async def mission_events(request: Request, mission_id: str, limit: int = 500) -> dict[str, Any]:
    state = get_state(request)
    return {"events": state.db.get_events(mission_id, limit=max(1, min(5000, limit)))}


# --------------------------------------------------------------------- copilot
@router.post("/copilot/ask")
async def copilot_ask(request: Request, body: AskBody) -> dict[str, Any]:
    state = get_state(request)
    snapshot = state.ui_snapshot()
    context = {
        "mission": snapshot["mission"],
        "fleet": snapshot["fleet"],
        "safety": snapshot["safety"],
        "survivors": snapshot["survivors"],
        "kits": snapshot["kits"],
        "allocations": snapshot["allocations"],
        "deliveries": snapshot["deliveries"],
        "video": snapshot["video"],
        "preflight": snapshot["preflight"],
        "events": snapshot["events"][-60:],
        "generated_at": snapshot["generated_at"],
    }
    try:
        return await asyncio.to_thread(state.copilot.ask, body.question, context)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ------------------------------------------------------------------ simulation
@router.post("/simulation/inject")
async def simulation_inject(request: Request, body: FaultBody) -> dict[str, Any]:
    state = get_state(request)
    if state.mission.mode != "SIMULATION":
        raise HTTPException(status_code=403, detail="Fault injection is only available in SIMULATION")
    try:
        return state.simulation.inject(body.fault, body.callsign, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ------------------------------------------------------------------- websocket
@ws_router.websocket("/ws")
async def websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    state = websocket.app.state.app_state
    queue = state.bus.subscribe()
    try:
        await websocket.send_json({"kind": "snapshot", "data": state.ui_snapshot()})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20.0)
                await websocket.send_json({"kind": "event", "event": event})
            except asyncio.TimeoutError:
                await websocket.send_json({"kind": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        state.bus.unsubscribe(queue)
