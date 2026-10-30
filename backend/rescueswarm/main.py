"""RescueSwarm GCS backend entry point.

Run locally only:
    python -m rescueswarm.main
or:
    uvicorn rescueswarm.main:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import APP_VERSION, PROJECT_ROOT
from .api import router, ws_router
from .state import AppState

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = AppState()
    app.state.app_state = state
    await state.startup()
    try:
        yield
    finally:
        await state.shutdown()


app = FastAPI(
    title="RescueSwarm GCS",
    version=APP_VERSION,
    description="Offline multi-drone rescue mission control. Local-only.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-Operator"],
)


@app.middleware("http")
async def local_only_guard(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0].strip("[]")
    if host and host not in ALLOWED_HOSTS:
        return JSONResponse(status_code=403, content={"detail": "Host not permitted"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "connect-src 'self' ws://127.0.0.1:*; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "script-src 'self'; "
        "worker-src 'self' blob:; "
        "child-src 'self' blob:; "
        "object-src 'none'; base-uri 'self'; form-action 'self'",
    )
    if request.url.path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


app.include_router(router)
app.include_router(ws_router)


def _frontend_dir() -> Path | None:
    override = os.environ.get("RESCUESWARM_FRONTEND")
    candidates = [Path(override)] if override else []
    candidates.append(PROJECT_ROOT / "desktop" / "dist")
    for candidate in candidates:
        if candidate and (candidate / "index.html").exists():
            return candidate
    return None


_frontend = _frontend_dir()
if _frontend:
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
else:
    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "application": "RescueSwarm GCS",
            "version": APP_VERSION,
            "status": "backend ready; frontend build not found",
            "docs": "/docs",
            "health": "/api/health",
        }


def main() -> None:
    import uvicorn

    port = int(os.environ.get("RESCUESWARM_PORT", "8765"))
    uvicorn.run(
        "rescueswarm.main:app",
        host="127.0.0.1",
        port=port,
        log_level=os.environ.get("RESCUESWARM_LOG_LEVEL", "info"),
        access_log=False,
    )


if __name__ == "__main__":
    main()
