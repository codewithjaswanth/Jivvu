"""Decoupled QuizZy Observability Dashboard Server running on 127.0.0.1:8765."""

from __future__ import annotations

import os
import sys

from pathlib import Path

_UVICORN_LOG = Path(__file__).resolve().parent / "runtime_uvicorn.log"
try:
    if sys.stdout is None or not hasattr(sys.stdout, "write"):
        sys.stdout = open(_UVICORN_LOG, "a", encoding="utf-8", buffering=1)
    if sys.stderr is None or not hasattr(sys.stderr, "write"):
        sys.stderr = open(_UVICORN_LOG, "a", encoding="utf-8", buffering=1)
except Exception:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import lifecycle_manager
from backend.lifecycle_manager import (
    get_state,
    is_backend_healthy,
    record_heartbeat,
    start_backend,
    stop_backend,
)
from backend.telemetry_db import (
    clear_logs,
    get_dashboard_summary,
    get_request_detail,
    get_request_logs,
)
from backend.model_manager import (
    get_active_model_selection,
    get_available_models_catalog,
    get_model_display_label,
    set_active_model_selection,
)

logger = logging.getLogger("quizzy.dashboard_server")

STATIC_DIR = Path(__file__).resolve().parent / "static" / "dashboard"
BACKEND_HEALTH_URL = "http://127.0.0.1:8000/health"


LEASE_TIMEOUT_SECONDS = float(os.getenv("QUIZZY_ZERO_TAB_LEASE_SECONDS", "60.0"))


async def run_lifecycle_watchdog() -> None:
    """
    Background watchdog loop running every 2.0 seconds.
    If the backend was auto-started by CCBP and the heartbeat lease expires
    (>60s without heartbeat or 0 active tabs continuously for >=60.0s,
    configured by QUIZZY_ZERO_TAB_LEASE_SECONDS), automatically stop the backend.
    """
    zero_tabs_start: Optional[float] = None
    while True:
        try:
            await asyncio.sleep(2.0)
            state = get_state()
            started_by = state.get("started_by") or ""
            is_auto = (
                started_by in ("ccbp_auto", "heartbeat_recovery", "content_script")
                or started_by.startswith("extension:")
            )

            if state.get("online") and is_auto:
                now = time.time()
                last_hb = state.get("last_heartbeat", 0.0)
                active_tabs = state.get("active_ccbp_tabs", 0)

                # Lease expired if 0 tabs persist continuously for >= zero_tab_lease seconds (default 60.0s)
                zero_tab_lease = float(os.getenv("QUIZZY_ZERO_TAB_LEASE_SECONDS", "60.0"))
                if active_tabs == 0:
                    if zero_tabs_start is None:
                        zero_tabs_start = now
                    elif now - zero_tabs_start >= zero_tab_lease:
                        logger.info("[Watchdog] Lease expired (0 active tabs for >=%.1fs). Stopping backend...", zero_tab_lease)
                        stop_backend("lease_expired_zero_tabs")
                        zero_tabs_start = None
                else:
                    zero_tabs_start = None

                # Lease expired if no heartbeat for > 45.0 seconds
                if now - last_hb > LEASE_TIMEOUT_SECONDS:
                    logger.info("[Watchdog] Lease expired (heartbeat timeout >%.1fs). Stopping backend...", LEASE_TIMEOUT_SECONDS)
                    stop_backend("lease_expired_timeout")
                    zero_tabs_start = None
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("[Watchdog] Exception in lifecycle watchdog: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    watchdog_task = asyncio.create_task(run_lifecycle_watchdog())
    yield
    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="QuizZy Observability Dashboard Server",
    version="1.0.0",
    description="Decoupled monitoring server reporting real-time telemetry for QuizZy backend.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://.*|https?://learning\.ccbp\.in|https?://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_dashboard_ui() -> FileResponse:
    """Serve the single-page dashboard HTML application."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():
        return JSONResponse(
            status_code=404,
            content={"error": "Dashboard UI not found in backend/static/dashboard/index.html"},
        )
    return FileResponse(index_file)


@app.get("/styles.css")
async def serve_dashboard_css() -> FileResponse:
    """Direct root fallback for stylesheet."""
    return FileResponse(STATIC_DIR / "styles.css")


@app.get("/dashboard.js")
async def serve_dashboard_script() -> FileResponse:
    """Direct root fallback for client script."""
    return FileResponse(STATIC_DIR / "dashboard.js")


@app.get("/api/dashboard/ping")
async def ping_dashboard() -> Dict[str, str]:
    """Lightweight readiness check for CLI launcher."""
    return {"dashboard": "online"}


@app.post("/api/lifecycle/start")
async def lifecycle_start(request: Request) -> Dict[str, Any]:
    """Idempotently start QuizZy backend server on 127.0.0.1:8000."""
    try:
        body = await request.json()
        source = body.get("source", "ccbp_auto") if isinstance(body, dict) else "ccbp_auto"
    except Exception:
        source = "ccbp_auto"
    return await asyncio.to_thread(lifecycle_manager.start_backend, source=source)


@app.post("/api/lifecycle/stop")
async def lifecycle_stop(request: Request) -> Dict[str, Any]:
    """Idempotently stop QuizZy backend server on 127.0.0.1:8000."""
    try:
        body = await request.json()
        reason = (body.get("reason") or body.get("source") or "ccbp_closed") if isinstance(body, dict) else "ccbp_closed"
    except Exception:
        reason = "ccbp_closed"
    return await asyncio.to_thread(lifecycle_manager.stop_backend, reason=reason)


@app.post("/api/lifecycle/heartbeat")
async def lifecycle_heartbeat(request: Request) -> Dict[str, Any]:
    """Receive heartbeat from active learning.ccbp.in tabs to sustain lease."""
    try:
        body = await request.json()
        active_tabs = body.get("active_tabs", 1) if isinstance(body, dict) else 1
        url = body.get("url", "") if isinstance(body, dict) else ""
    except Exception:
        active_tabs = 1
        url = ""
    return await asyncio.to_thread(lifecycle_manager.record_heartbeat, active_tabs=active_tabs, url=url)


@app.get("/api/lifecycle/status")
async def lifecycle_status() -> Dict[str, Any]:
    """Retrieve full backend process and lease state."""
    state = lifecycle_manager.get_state()
    healthy = await asyncio.to_thread(lifecycle_manager.is_backend_healthy)
    state["healthy"] = healthy
    state["online"] = healthy
    state["port"] = 8000
    state["state"] = lifecycle_manager.get_state()
    return state


@app.get("/api/dashboard/backend-status")
async def get_backend_status() -> Dict[str, Any]:
    """
    Actively ping the main QuizZy backend at 127.0.0.1:8000/health.
    Reports ON with ping latency (ms) and uptime details, or OFF if unreachable.
    Always returns HTTP 200 so the dashboard UI handles status changes gracefully.
    """
    t0 = time.perf_counter()
    try:
        fast_timeout = httpx.Timeout(0.6, connect=0.4)
        async with httpx.AsyncClient(timeout=fast_timeout) as client:
            resp = await client.get(BACKEND_HEALTH_URL)
            ping_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if resp.status_code == 200:
                data = resp.json()
                active_model = get_active_model_selection()
                return {
                    "online": True,
                    "status_label": "ON",
                    "url": "http://127.0.0.1:8000",
                    "ping_ms": ping_ms,
                    "active_model": active_model,
                    "active_model_label": get_model_display_label(active_model),
                    "details": data,
                }
            active_model = get_active_model_selection()
            return {
                "online": False,
                "status_label": "OFF",
                "url": "http://127.0.0.1:8000",
                "ping_ms": ping_ms,
                "active_model": active_model,
                "active_model_label": get_model_display_label(active_model),
                "error": f"HTTP {resp.status_code}",
            }
    except Exception as exc:
        active_model = get_active_model_selection()
        return {
            "online": False,
            "status_label": "OFF",
            "url": "http://127.0.0.1:8000",
            "ping_ms": None,
            "active_model": active_model,
            "active_model_label": get_model_display_label(active_model),
            "error": str(exc) or "Connection refused / Unreachable",
        }


@app.get("/api/dashboard/model-selection")
async def get_dashboard_model_selection() -> Dict[str, Any]:
    """Retrieve active model selection, label, and full catalog for UI dropdown."""
    active = get_active_model_selection()
    return {
        "status": "ok",
        "selected_model": active,
        "selected_label": get_model_display_label(active),
        "catalog": get_available_models_catalog(),
    }


@app.post("/api/dashboard/model-selection")
async def set_dashboard_model_selection(request: Request) -> Dict[str, Any]:
    """
    Update active model selection in SQLite telemetry database,
    and notify main backend if online.
    """
    try:
        body = await request.json()
        model_name = body.get("model") or body.get("selected_model") or "auto"
    except Exception:
        model_name = "auto"

    result = set_active_model_selection(model_name)
    backend_synced = False

    try:
        fast_timeout = httpx.Timeout(0.5, connect=0.3)
        async with httpx.AsyncClient(timeout=fast_timeout) as client:
            resp = await client.post(
                "http://127.0.0.1:8000/api/model-selection",
                json={"model": result["selected_model"]},
            )
            if resp.status_code == 200:
                backend_synced = True
    except Exception:
        backend_synced = False

    result["backend_synced"] = backend_synced
    return result


@app.get("/api/dashboard/metrics")
async def get_metrics(
    window: str = Query("all", description="Window filter: all, 24h, 1h")
) -> Dict[str, Any]:
    """Retrieve aggregated telemetry KPIs, rates, latency percentiles, and timeline."""
    return get_dashboard_summary(window=window)


@app.get("/api/dashboard/requests")
async def list_requests(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
    error_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    window: str = Query("all"),
) -> Dict[str, Any]:
    """Retrieve paginated, filtered request logs for deep inspection."""
    return get_request_logs(
        limit=limit,
        offset=offset,
        status_filter=status,
        error_filter=error_type,
        search=search,
        window=window,
    )


@app.get("/api/dashboard/requests/{request_id}")
async def get_single_request(request_id: int) -> Dict[str, Any]:
    """Retrieve single request record by ID."""
    detail = get_request_detail(request_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Request log not found")
    return detail


@app.post("/api/dashboard/clear")
async def clear_telemetry() -> Dict[str, Any]:
    """Clear all request telemetry logs from the database."""
    deleted_count = clear_logs()
    return {"status": "ok", "deleted": deleted_count}

