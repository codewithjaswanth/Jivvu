"""QuizZy local FastAPI backend application."""

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
import csv
import hashlib
import json
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import (
    http_exception_handler as _fastapi_http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.cache import (
    cache_solution,
    compute_cache_key,
    get_cache_stats,
    get_cached_solution,
    get_wrong_answers,
    init_db,
    record_wrong_answer,
)
from backend.telemetry_db import (
    record_request_log,
    validate_accuracy_and_classify_error,
)
from backend.core.key_pool import (
    get_healthy_groq_keys,
    load_groq_api_keys,
    reset_key_quarantine,
    reset_model_cooldown,
    reset_model_quarantine,
    reset_provider_cooldown,
)
from backend.core.llm_client import (
    get_active_fallback_pool,
    load_models_config,
    load_openrouter_api_keys,
    solve_question,
)
from backend.core.normalizer import normalize_text_answer, snap_to_closest_option
from backend.core.searxng_client import get_searxng_config, is_searxng_circuit_open
from backend.model_manager import (
    get_active_model_selection,
    get_available_models_catalog,
    get_model_display_label,
    set_active_model_selection,
)
from backend.schemas import (
    DebugSolveResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    SolveRequest,
    SolveResponse,
    StatsResponse,
)


_csv_write_lock = threading.Lock()


def save_or_update_question_csv(
    subject: str,
    q_type: str,
    question: str,
    options: List[str],
    correct_answer: Any,
    csv_path: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Append or update in-place a question row in questions.csv with deduplication.
    Uses canonical format for expected_answer (letter for MCQ, permutation for Order, text for Text).
    """
    path = Path(csv_path) if csv_path else Path(__file__).resolve().parent.parent / "questions.csv"
    norm_type = q_type.strip().lower()
    options = options or []

    # Format expected_answer
    if norm_type == "mcq":
        raw_str = str(correct_answer).strip()
        if len(raw_str) == 1 and raw_str.upper() in "ABCDEF":
            expected = raw_str.upper()
        else:
            # Find in options
            cand_norm = normalize_text_answer(raw_str)
            found_idx = None
            for idx, opt in enumerate(options):
                if normalize_text_answer(opt) == cand_norm:
                    found_idx = idx
                    break
            expected = chr(65 + found_idx) if found_idx is not None else raw_str.upper()

    elif norm_type == "order":
        if isinstance(correct_answer, list):
            # Check if list of letters or list of option texts
            if all(isinstance(x, str) and len(x.strip()) == 1 and x.strip().upper() in "ABCDEF" for x in correct_answer):
                expected = ",".join([x.strip().upper() for x in correct_answer])
            else:
                # List of option texts -> map to letters
                letters = []
                for item in correct_answer:
                    item_norm = normalize_text_answer(str(item))
                    for idx, opt in enumerate(options):
                        if normalize_text_answer(opt) == item_norm:
                            letters.append(chr(65 + idx))
                            break
                expected = ",".join(letters) if len(letters) == len(options) else str(correct_answer)
        else:
            expected = str(correct_answer).strip()

    elif norm_type == "text":
        expected = str(correct_answer).strip()

    else:
        expected = str(correct_answer).strip()

    options_json = json.dumps(options or [])
    norm_q = normalize_text_answer(question)
    sorted_norm_opts = sorted([normalize_text_answer(o) for o in options])

    existing_rows: List[Dict[str, str]] = []
    matched_id: Optional[str] = None

    with _csv_write_lock:
        if path.is_file():
            with open(path, mode="r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    r_q = normalize_text_answer(row.get("question", ""))
                    try:
                        r_opts = json.loads(row.get("options_json", "[]"))
                    except Exception:
                        r_opts = []
                    r_sorted_opts = sorted([normalize_text_answer(o) for o in r_opts])

                    if r_q == norm_q and (norm_type in ("text", "code") or r_sorted_opts == sorted_norm_opts):
                        # Duplicate found -> update row in place
                        matched_id = row.get("id")
                        row["subject"] = subject
                        row["q_type"] = norm_type
                        row["question"] = question
                        row["options_json"] = options_json
                        row["expected_answer"] = expected
                    existing_rows.append(row)

        if matched_id:
            final_id = matched_id
        else:
            short_hash = hashlib.sha256(f"{subject}:{norm_type}:{question}".encode("utf-8")).hexdigest()[:8].upper()
            final_id = f"REAL_{short_hash}"
            existing_rows.append({
                "id": final_id,
                "subject": subject,
                "q_type": norm_type,
                "question": question,
                "options_json": options_json,
                "expected_answer": expected,
            })

        fieldnames = ["id", "subject", "q_type", "question", "options_json", "expected_answer"]
        with open(path, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in existing_rows:
                writer.writerow({k: r.get(k, "") for k in fieldnames})

    return True, final_id


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage lifecycle of shared HTTP client, models configuration, and SQLite cache."""
    # Startup
    init_db()
    app.state.models_config = load_models_config()
    app.state.http_client = httpx.AsyncClient()
    yield
    # Shutdown
    await app.state.http_client.aclose()


app = FastAPI(
    title="QuizZy Backend API",
    version="1.0.0",
    description="Fast, local, order-invariant quiz solver engine backend.",
    lifespan=lifespan,
)

APP_START_TIME = time.time()

# CORS: Allow localhost (any port) and Chrome extension origins
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://.*|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_timing_middleware(request: Request, call_next):
    """Record request arrival monotonic timestamp for accurate latency reporting on all outcomes."""
    request.state.start_time = time.perf_counter()
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Capture request validation errors into telemetry database with real latency and partial stem without altering 422 contract."""
    path = request.url.path
    if "/solve" in path and not getattr(request.state, "telemetry_logged", False):
        request.state.telemetry_logged = True
        start_t = getattr(request.state, "start_time", None)
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0 if start_t else 0.5
        if elapsed_ms < 0.1:
            elapsed_ms = 0.1
        elapsed_ms = round(elapsed_ms, 2)

        # Extract any partial question stem available in body
        partial_stem = ""
        q_type_val = "mcq"
        options_list = None
        subject_val = "General"
        body = exc.body
        if isinstance(body, dict):
            for key in ("question", "stem", "question_text", "prompt", "text"):
                val = body.get(key)
                if val:
                    cleaned = str(val).strip()
                    if cleaned:
                        partial_stem = cleaned
                        break
            if "q_type" in body and body["q_type"]:
                q_type_val = str(body["q_type"])
            if "subject" in body and body["subject"]:
                subject_val = str(body["subject"])
            opts = body.get("options")
            if isinstance(opts, list):
                options_list = [str(o) for o in opts]

        # Extract human-readable error summary from exc.errors()
        err_msgs = []
        for err in exc.errors():
            loc = " -> ".join(str(p) for p in err.get("loc", []) if p != "body")
            msg = err.get("msg", "Validation error")
            if loc:
                err_msgs.append(f"Field '{loc}': {msg}")
            else:
                err_msgs.append(msg)
        error_summary = "; ".join(err_msgs) if err_msgs else str(exc)

        record_request_log(
            endpoint=path,
            subject=subject_val,
            q_type=q_type_val,
            question_text=partial_stem,
            options=options_list,
            status="FAILURE",
            is_accurate=False,
            error_type="VALIDATION_ERROR",
            error_message=error_summary,
            latency_ms=elapsed_ms,
        )
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Capture HTTP exceptions into telemetry database with measured latency."""
    path = request.url.path
    if "/solve" in path and not getattr(request.state, "telemetry_logged", False):
        request.state.telemetry_logged = True
        start_t = getattr(request.state, "start_time", None)
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0 if start_t else 0.5
        if elapsed_ms < 0.1:
            elapsed_ms = 0.1
        elapsed_ms = round(elapsed_ms, 2)

        err_type = (
            "VALIDATION_ERROR"
            if exc.status_code in (400, 422)
            else (
                "RATE_LIMIT_429"
                if exc.status_code == 429
                else (
                    "PROVIDER_API_ERROR"
                    if exc.status_code >= 500
                    else "INTERNAL_SERVER_ERROR"
                )
            )
        )
        record_request_log(
            endpoint=path,
            status="FAILURE",
            is_accurate=False,
            error_type=err_type,
            error_message=str(exc.detail),
            latency_ms=elapsed_ms,
        )
    return await _fastapi_http_exception_handler(request, exc)


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """System health check and loaded models summary with server uptime and timestamp."""
    stats = get_cache_stats()
    models_summary = getattr(app.state, "models_config", {})
    _, searxng_enabled = get_searxng_config()
    openrouter_keys = load_openrouter_api_keys()
    active_pool = get_active_fallback_pool(models_summary)
    active_model = get_active_model_selection()
    now_ts = time.time()
    uptime_sec = round(now_ts - APP_START_TIME, 2)
    return HealthResponse(
        status="ok",
        models=models_summary,
        cache_entries=stats.get("cache_entries", 0),
        searxng_enabled=searxng_enabled,
        searxng_circuit_open=is_searxng_circuit_open(),
        openrouter_keys_loaded=len(openrouter_keys),
        active_fallback_pool=active_pool,
        active_model_selection=active_model,
        timestamp=now_ts,
        uptime_seconds=uptime_sec,
    )


@app.get("/api/model-selection")
async def get_backend_model_selection() -> Dict[str, Any]:
    """Retrieve active model selection, user label, and structured model catalog."""
    active = get_active_model_selection()
    return {
        "status": "ok",
        "selected_model": active,
        "selected_label": get_model_display_label(active),
        "catalog": get_available_models_catalog(getattr(app.state, "models_config", None)),
    }


@app.post("/api/model-selection")
async def set_backend_model_selection(request: Request) -> Dict[str, Any]:
    """Update active model selection on the backend."""
    try:
        body = await request.json()
        model_name = body.get("model") or body.get("selected_model") or "auto"
    except Exception:
        model_name = "auto"
    return set_active_model_selection(model_name)


@app.post("/shutdown")
async def shutdown_backend(request: Request) -> dict:
    """Gracefully terminate local backend process when authorized."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "localhost", "::1", "testclient"):
        raise HTTPException(status_code=403, detail="Shutdown restricted to localhost")

    import os
    import sys
    import signal
    import threading

    # Only terminate process when running in production server, not inside test runners
    if "PYTEST_CURRENT_TEST" not in os.environ and "pytest" not in sys.modules:
        def _terminate():
            time.sleep(0.3)
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=_terminate, daemon=True).start()

    return {"status": "ok", "message": "Backend server is shutting down"}


@app.get("/stats", response_model=StatsResponse)
async def cache_statistics() -> StatsResponse:
    """Retrieve cache hit/miss operational metrics and wrong answer counts."""
    stats = get_cache_stats()
    return StatsResponse(
        status="ok",
        cache_entries=stats["cache_entries"],
        total_hits=stats["total_hits"],
        total_misses=stats["total_misses"],
        total_requests=stats["total_requests"],
        hit_rate_pct=stats["hit_rate_pct"],
        wrong_answers_count=stats.get("wrong_answers_count", 0),
        user_overrides_count=stats.get("user_overrides_count", 0),
    )


def _sanitize_solve_request(req: SolveRequest) -> None:
    """Normalize misclassified q_types and enforce cache bypass on retry."""
    # Sanitize false-positive order classification
    if req.q_type == "order" and req.options:
        opt_lower = [str(o).strip().lower() for o in req.options]
        has_mcq_opt = any(
            o in ("none of these", "none of the above", "all of the above", "all of these")
            or o.startswith("both ")
            or o.startswith("neither ")
            for o in opt_lower
        )
        has_not_same_order = bool(
            re.search(r"\bnot(?:\s+necessarily)?\s+in(?:\s+the)?\s+same\s+order\b", req.question, re.I)
        )
        has_order_disclaimer = bool(
            re.search(
                r"\b(in\s+order\s+to|order\s+of\s+magnitude|first\s+order|second\s+order|out\s+of\s+order)\b",
                req.question,
                re.I,
            )
        )
        has_order_directive = bool(
            re.search(
                r"\b(arrange|chronological|rearrange|put\s+(?:the\s+following\s+)?in\s+order|sort\s+the\s+following|rank\s+the\s+following)\b",
                req.question,
                re.I,
            )
        )
        if has_mcq_opt or has_not_same_order or (has_order_disclaimer and not has_order_directive):
            req.q_type = "mcq"

    # Auto-upgrade misclassified MCQ to order if stem has ordering directives
    # and options are individual statements rather than pre-combined letter sequences like "B, D, A, C"
    if req.q_type == "mcq" and req.options and len(req.options) >= 2:
        is_order_stem = bool(
            re.search(
                r"\b(rearrange\s+the\s+following.*in\s+(?:the\s+)?correct\s+order|drag\s+and\s+drop\s+to\s+order|drag\s+to\s+reorder|put\s+the\s+following.*in\s+order|arrange\s+the\s+following.*in\s+(?:the\s+)?correct\s+order)\b",
                req.question,
                re.I,
            )
        )
        options_are_sequences = any(
            bool(re.search(r"^[A-Fa-f0-9](\s*[,-\u2013>]+\s*[A-Fa-f0-9]){2,}", str(o).strip()))
            for o in req.options
        )
        if is_order_stem and not options_are_sequences:
            req.q_type = "order"

    # Mandatory Cache Bypass on Retry: never serve stale Attempt 1 failure on Attempt 2
    if req.bypass_cache or bool(req.error_context and str(req.error_context).strip()):
        req.bypass_cache = True


def _build_cached_response(req: SolveRequest, cached: Dict[str, Any], elapsed_ms: float) -> SolveResponse:
    """Map a cache row onto the public SolveResponse contract."""
    return SolveResponse(
        status="ok",
        q_type=req.q_type,
        answer=cached.get("answer", ""),
        selected_index=cached.get("selected_index"),
        selected_text=cached.get("selected_text"),
        selected_indices=cached.get("selected_indices"),
        selected_texts=cached.get("selected_texts"),
        canonical_keys=cached.get("canonical_keys"),
        permutation_indices=cached.get("permutation_indices"),
        ordered_texts=cached.get("ordered_texts"),
        reason=cached.get("reason", ""),
        confidence_tier=cached.get("confidence_tier", ""),
        confidence_label=cached.get("confidence_label", "high"),
        confidence_score=cached.get("confidence_score", 1.0),
        auto_eligible=cached.get("auto_eligible", True),
        cache_hit=True,
        latency_ms=round(elapsed_ms, 2),
        searxng_used=cached.get("searxng_used", False),
        search_sources=cached.get("search_sources", []),
        language=req.language if req.q_type == "code" else None,
    )


# Deduplication and Coalescing State
_in_flight_locks: Dict[str, asyncio.Future] = {}
_in_flight_mutex = asyncio.Lock()
_recent_solved_coalesce: Dict[str, Tuple[float, SolveResponse, Dict[str, Any]]] = {}

MAX_COALESCE_ENTRIES = 128
COALESCE_TTL_SECONDS = 2.5


def _prune_coalesce_cache(now: float) -> None:
    """Evict expired coalesce entries (>2.5s) and enforce MAX_COALESCE_ENTRIES bound."""
    expired_keys = [k for k, (ts, _, _) in _recent_solved_coalesce.items() if now - ts > COALESCE_TTL_SECONDS]
    for k in expired_keys:
        _recent_solved_coalesce.pop(k, None)

    if len(_recent_solved_coalesce) > MAX_COALESCE_ENTRIES:
        sorted_keys = sorted(_recent_solved_coalesce.keys(), key=lambda k: _recent_solved_coalesce[k][0])
        excess = len(_recent_solved_coalesce) - MAX_COALESCE_ENTRIES
        for k in sorted_keys[:excess]:
            _recent_solved_coalesce.pop(k, None)


@app.post("/api/reset-state")
@app.post("/reset-state")
async def reset_backend_state() -> Dict[str, Any]:
    """
    Clear in-flight solve locks, recent solve coalesce cache, and rate-limit quarantines/cooldowns.
    Resets in-memory rate limit and execution states immediately without altering persistent SQLite caches.
    """
    global _recent_solved_coalesce
    async with _in_flight_mutex:
        for fut in list(_in_flight_locks.values()):
            if fut and not fut.done():
                try:
                    fut.cancel()
                except Exception:
                    pass
        _in_flight_locks.clear()
    _recent_solved_coalesce.clear()
    reset_key_quarantine()
    reset_model_quarantine()
    reset_provider_cooldown()
    reset_model_cooldown()
    return {"ok": True, "status": "ok", "state_reset": True, "message": "Backend state reset successfully"}


def remap_response_to_follower_options(res: SolveResponse, follower_options: List[str]) -> SolveResponse:
    """
    Dynamically remap leader SolveResponse indices, answers, canonical keys, and permutation
    indices to the follower request's visual options permutation.
    """
    if not follower_options or res.q_type not in ("mcq", "order", "multi"):
        return res

    norm_type = res.q_type.strip().lower()

    if norm_type == "mcq":
        target_text = res.selected_text or ""
        if not target_text and res.selected_index is not None:
            return res
        target_norm = normalize_text_answer(str(target_text))
        found_idx = None
        for idx, opt in enumerate(follower_options):
            if normalize_text_answer(opt) == target_norm:
                found_idx = idx
                break
        if found_idx is None:
            for idx, opt in enumerate(follower_options):
                opt_norm = normalize_text_answer(opt)
                if target_norm and (target_norm in opt_norm or opt_norm in target_norm):
                    found_idx = idx
                    break
        if found_idx is not None:
            remapped_answer = chr(65 + found_idx)
            return res.model_copy(update={
                "selected_index": found_idx,
                "selected_text": follower_options[found_idx],
                "answer": remapped_answer,
            })
        return res

    elif norm_type == "order":
        ordered_targets = res.ordered_texts or []
        if not ordered_targets:
            return res
        used_indices: set = set()
        remapped_indices: List[int] = []
        for target_item in ordered_targets:
            item_norm = normalize_text_answer(str(target_item))
            found_i = None
            for idx, opt in enumerate(follower_options):
                if idx not in used_indices and normalize_text_answer(opt) == item_norm:
                    found_i = idx
                    break
            if found_i is None:
                for idx, opt in enumerate(follower_options):
                    opt_n = normalize_text_answer(opt)
                    if idx not in used_indices and item_norm and (item_norm in opt_n or opt_n in item_norm):
                        found_i = idx
                        break
            if found_i is not None:
                used_indices.add(found_i)
                remapped_indices.append(found_i)

        if len(remapped_indices) == len(follower_options):
            letters = [chr(65 + i) for i in remapped_indices]
            canonical_str = ",".join(letters)
            return res.model_copy(update={
                "permutation_indices": remapped_indices,
                "ordered_texts": [follower_options[i] for i in remapped_indices],
                "canonical_keys": letters,
                "answer": canonical_str,
            })
        return res

    elif norm_type == "multi":
        target_texts = res.selected_texts or []
        if not target_texts:
            return res
        used_indices_multi = set()
        remapped_indices_multi = []
        for item in target_texts:
            item_norm = normalize_text_answer(str(item))
            for idx, opt in enumerate(follower_options):
                if idx not in used_indices_multi and normalize_text_answer(opt) == item_norm:
                    used_indices_multi.add(idx)
                    remapped_indices_multi.append(idx)
                    break
        if remapped_indices_multi:
            sorted_indices = sorted(remapped_indices_multi)
            letters = [chr(65 + i) for i in sorted_indices]
            sep = ", " if ", " in (res.answer or "") else ","
            canonical_str = sep.join(letters)
            selected_texts = [follower_options[i] for i in sorted_indices]
            return res.model_copy(update={
                "selected_indices": sorted_indices,
                "selected_texts": selected_texts,
                "selected_index": sorted_indices[0],
                "selected_text": ", ".join(selected_texts),
                "canonical_keys": letters,
                "answer": canonical_str,
            })
        return res

    return res


async def _solve_core(
    req: SolveRequest,
    endpoint: str = "/solve",
    request: Optional[Request] = None,
) -> Tuple[SolveResponse, Dict[str, Any]]:
    """
    Shared solve pipeline used by /solve and /debug/solve.

    Returns the public response plus a debug trace (models_called,
    agreement_details). Deduplicates concurrent in-flight requests and
    coalesces rapid repeated calls within 2.5s to eliminate redundant LLM calls.
    """
    t0 = time.perf_counter()
    _sanitize_solve_request(req)

    # Compute deterministic coalescing / cache key
    img_sig = getattr(req, "image_data", None) or getattr(req, "image_url", None)
    c_key = compute_cache_key(
        subject=req.subject,
        q_type=req.q_type,
        question=req.question,
        options=req.options,
        language=req.language,
        starter_code=req.starter_code,
        image_data=img_sig,
    )

    # 1. Order-Invariant Persistent Cache Lookup (unless bypassed)
    if req.bypass_cache:
        _recent_solved_coalesce.pop(c_key, None)
    else:
        cached = get_cached_solution(
            subject=req.subject,
            q_type=req.q_type,
            question=req.question,
            options=req.options,
            language=req.language,
            starter_code=req.starter_code,
            image_data=getattr(req, "image_data", None),
            image_url=getattr(req, "image_url", None),
        )
        if cached:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            response = _build_cached_response(req, cached, elapsed_ms)
            trace = {
                "models_called": list(cached.get("models_called", []) or []),
                "agreement_details": {},
            }
            _recent_solved_coalesce[c_key] = (time.time(), response, trace)
            return response, trace

        # 2. Coalesce check for rapid repeated requests (< 2.5s) to avoid duplicate solves on rapid double-clicks/DOM churn
        now_ts = time.time()
        _prune_coalesce_cache(now_ts)
        recent = _recent_solved_coalesce.get(c_key)
        if recent is not None:
            ts, cached_res, cached_trace = recent
            if now_ts - ts <= 2.5:
                if request is not None:
                    request.state.telemetry_logged = True
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                remapped_res = remap_response_to_follower_options(cached_res, req.options or [])
                cloned_res = remapped_res.model_copy(update={"latency_ms": round(elapsed_ms, 2), "cache_hit": True})
                return cloned_res, cached_trace

    # 3. In-Flight Coalescing: join existing solver if another coroutine is already solving this question
    is_leader = False
    in_flight_future: Optional[asyncio.Future] = None
    async with _in_flight_mutex:
        if c_key in _in_flight_locks:
            in_flight_future = _in_flight_locks[c_key]
        else:
            loop = asyncio.get_running_loop()
            in_flight_future = loop.create_future()
            _in_flight_locks[c_key] = in_flight_future
            is_leader = True

    if not is_leader:
        # Await the in-flight leader coroutine's result
        try:
            leader_res, leader_trace = await in_flight_future
            if request is not None:
                request.state.telemetry_logged = True
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            remapped_leader = remap_response_to_follower_options(leader_res, req.options or [])
            cloned = remapped_leader.model_copy(update={"latency_ms": round(elapsed_ms, 2)})
            return cloned, leader_trace
        except Exception:
            raise

    # 4. As leader, execute live solver
    response: Optional[SolveResponse] = None
    trace: Dict[str, Any] = {}
    solver_res = None
    captured_exc: Optional[Exception] = None

    try:
        client = getattr(app.state, "http_client", None)
        if client is None or getattr(client, "is_closed", False):
            client = httpx.AsyncClient()
            app.state.http_client = client
        config = getattr(app.state, "models_config", None)

        chosen_model = getattr(req, "model", None) or get_active_model_selection()
        solver_res = await solve_question(
            subject=req.subject,
            q_type=req.q_type,
            question=req.question,
            options=req.options,
            config=config,
            client=client,
            language=req.language,
            starter_code=req.starter_code,
            error_context=req.error_context,
            image_data=getattr(req, "image_data", None),
            image_url=getattr(req, "image_url", None),
            selected_model=chosen_model,
        )

        tier = solver_res.confidence_tier
        is_ok = (solver_res.status.upper() == "OK")

        # Map Confidence Tiers to API contracts
        if tier.startswith("HIGH_") or tier == "USER_VERIFIED_OVERRIDE":
            confidence_label = "high"
            confidence_score = 1.0
            auto_eligible = is_ok
        elif tier == "MEDIUM_SEARXNG_GROUNDED":
            confidence_label = "medium"
            confidence_score = 0.75
            auto_eligible = is_ok
        elif tier in ("MEDIUM_TIEBREAKER_AGREE", "SINGLE_PRIMARY_PASSED"):
            confidence_label = "medium"
            confidence_score = 0.67
            auto_eligible = is_ok
        elif tier in ("LOW_FALLBACK_RESCUE", "LOW_FALLBACK"):
            confidence_label = "low"
            confidence_score = 0.5
            auto_eligible = False
        else:
            confidence_label = "uncertain"
            confidence_score = 0.0
            auto_eligible = False

        if is_ok:
            if req.q_type in ("mcq", "order", "multi"):
                answer_val = solver_res.canonical_key or ""
            else:
                answer_val = solver_res.selected_text or getattr(solver_res, "raw_answer", None) or solver_res.canonical_key or ""
        else:
            answer_val = ""

        # Zero-Poison Rule: Cache ONLY high and medium confidence solutions
        if is_ok and (
            tier.startswith("HIGH_")
            or tier in (
                "MEDIUM_TIEBREAKER_AGREE",
                "MEDIUM_SEARXNG_GROUNDED",
                "USER_VERIFIED_OVERRIDE",
                "SINGLE_PRIMARY_PASSED",
            )
        ):
            cache_solution(
                subject=req.subject,
                q_type=req.q_type,
                question=req.question,
                options=req.options,
                solver_res=solver_res,
                language=req.language,
                starter_code=req.starter_code,
                image_data=getattr(req, "image_data", None),
                image_url=getattr(req, "image_url", None),
            )

        # Enforce strict post-check for Hallucinated Options
        final_sel_idx = solver_res.selected_index
        final_sel_text = solver_res.selected_text
        final_sel_indices = getattr(solver_res, "selected_indices", None)
        final_sel_texts = getattr(solver_res, "selected_texts", None)
        final_canonical_keys = getattr(solver_res, "canonical_keys", None)

        if req.q_type == "mcq" and req.options:
            if final_sel_idx is None or final_sel_idx < 0 or final_sel_idx >= len(req.options):
                snapped = snap_to_closest_option(final_sel_text or str(answer_val), req.options)
                final_sel_idx = snapped if snapped is not None else 0
            final_sel_text = req.options[final_sel_idx]
            answer_val = chr(65 + final_sel_idx)

        elif req.q_type == "multi" and req.options:
            if final_sel_indices:
                valid_indices = [idx for idx in final_sel_indices if 0 <= idx < len(req.options)]
                if not valid_indices:
                    snapped = snap_to_closest_option(str(answer_val), req.options)
                    valid_indices = [snapped] if snapped is not None else [0]
                final_sel_indices = sorted(list(set(valid_indices)))
                final_sel_texts = [req.options[idx] for idx in final_sel_indices]
                final_canonical_keys = [chr(65 + idx) for idx in final_sel_indices]
                final_sel_idx = final_sel_indices[0]
                final_sel_text = ", ".join(final_sel_texts)
                answer_val = ", ".join(final_canonical_keys)

        response = SolveResponse(
            status="ok" if is_ok else "uncertain",
            q_type=req.q_type,
            answer=answer_val,
            selected_index=final_sel_idx,
            selected_text=final_sel_text,
            selected_indices=final_sel_indices,
            selected_texts=final_sel_texts,
            canonical_keys=final_canonical_keys,
            permutation_indices=solver_res.permutation_indices,
            ordered_texts=solver_res.ordered_texts,
            reason=solver_res.reason or "",
            confidence_tier=tier,
            confidence_label=confidence_label,
            confidence_score=confidence_score,
            auto_eligible=auto_eligible,
            cache_hit=False,
            latency_ms=round(solver_res.latency_ms, 2),
            searxng_used=solver_res.searxng_used,
            search_sources=solver_res.search_sources,
            language=req.language if req.q_type == "code" else None,
            error=solver_res.error,
        )
        trace = {
            "models_called": list(getattr(solver_res, "models_called", []) or []),
            "agreement_details": dict(getattr(solver_res, "agreement_details", {}) or {}),
        }

        # Update coalesce cache (only store high/medium confidence results, never uncertain/low-fallback)
        if (
            response.status == "ok"
            and response.confidence_tier not in ("LOW_FALLBACK_RESCUE", "UNCERTAIN", None)
            and not req.bypass_cache
        ):
            _recent_solved_coalesce[c_key] = (time.time(), response, trace)
        else:
            _recent_solved_coalesce.pop(c_key, None)

        if in_flight_future and not in_flight_future.done():
            in_flight_future.set_result((response, trace))

        return response, trace
    except Exception as exc:
        captured_exc = exc
        if in_flight_future and not in_flight_future.done():
            in_flight_future.set_exception(exc)
        raise
    finally:
        async with _in_flight_mutex:
            _in_flight_locks.pop(c_key, None)
            _prune_coalesce_cache(time.time())

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        model_name = ""
        cache_hit = False
        raw_preview = None

        if response is not None:
            cache_hit = getattr(response, "cache_hit", False)
            if cache_hit:
                model_name = "cache"
            raw_preview = response.model_dump_json()[:2000]

        if not model_name:
            if trace and trace.get("models_called"):
                model_name = ",".join(trace.get("models_called", []))
            elif solver_res is not None and getattr(solver_res, "models_called", None):
                model_name = ",".join(getattr(solver_res, "models_called", []))

        is_accurate, error_type, status_val = validate_accuracy_and_classify_error(
            q_type=req.q_type,
            options=req.options,
            response=response,
            exception=captured_exc,
            error_msg=str(captured_exc) if captured_exc else (getattr(response, "error", None) if response else None),
        )

        if request is not None and getattr(request.state, "telemetry_logged", False):
            pass
        else:
            if request is not None:
                request.state.telemetry_logged = True
            record_request_log(
                endpoint=endpoint,
                subject=req.subject,
                q_type=req.q_type,
                question_text=req.question,
                options=req.options,
                selected_answer=getattr(response, "answer", None) if response else None,
                selected_index=getattr(response, "selected_index", None) if response else None,
                canonical_key=(
                    ", ".join(response.canonical_keys)
                    if (response and getattr(response, "canonical_keys", None))
                    else (getattr(response, "canonical_key", None) or (getattr(response, "answer", None) if response else None))
                ),
                status=status_val,
                is_accurate=is_accurate,
                error_type=error_type,
                error_message=str(captured_exc) if captured_exc else (getattr(response, "error", None) if response else None),
                latency_ms=elapsed_ms,
                model_name=model_name,
                cache_hit=cache_hit,
                raw_response_preview=raw_preview,
            )


@app.post("/solve", response_model=SolveResponse)
async def solve_endpoint(req: SolveRequest, request: Request) -> SolveResponse:
    """
    Solve a quiz question across MCQ, unlabelled order blocks, free text, and code.
    Checks the order-invariant SQLite cache first (unless bypass_cache=True).
    On cache miss, runs parallel primary models + fallback solver, enforcing
    Zero-Poison caching rules and derived cross-model confidence.
    """
    response, _ = await _solve_core(req, endpoint="/solve", request=request)
    return response


@app.post("/debug/solve", response_model=DebugSolveResponse)
async def debug_solve_endpoint(req: SolveRequest, request: Request) -> DebugSolveResponse:
    """
    Development-only variant of /solve that also returns the full decision
    trace: models_called and agreement_details (per-model latencies and
    consensus path). Cache hits return an empty trace.
    """
    response, trace = await _solve_core(req, endpoint="/debug/solve", request=request)
    return DebugSolveResponse(**response.model_dump(), **trace)


@app.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(req: FeedbackRequest) -> FeedbackResponse:
    """
    Process user feedback on wrong model answers.
    - Case A (no correct_answer): Evicts entry from solve_cache and logs to wrong_answers.
    - Case B (correct_answer provided): Overrides solve_cache with USER_VERIFIED_OVERRIDE,
      logs to wrong_answers, and optionally exports deduplicated question to questions.csv.
    """
    action, cache_key = record_wrong_answer(
        subject=req.subject,
        q_type=req.q_type,
        question=req.question,
        options=req.options,
        wrong_model_answer=req.wrong_model_answer,
        correct_answer=req.correct_answer,
        confidence_tier_at_failure=req.confidence_tier_at_failure,
        notes=req.notes,
        language=req.language,
        starter_code=req.starter_code,
        image_data=getattr(req, "image_data", None),
        image_url=getattr(req, "image_url", None),
    )

    csv_exported = False
    csv_row_id = None

    if req.export_to_csv and req.correct_answer is not None:
        try:
            csv_exported, csv_row_id = await asyncio.to_thread(
                save_or_update_question_csv,
                subject=req.subject,
                q_type=req.q_type,
                question=req.question,
                options=req.options,
                correct_answer=req.correct_answer,
            )
        except Exception:
            csv_exported = False

    return FeedbackResponse(
        status="ok",
        action=action,
        cache_key=cache_key,
        csv_exported=csv_exported,
        csv_row_id=csv_row_id,
    )


@app.get("/feedback")
async def get_feedback_endpoint(only_with_correction: bool = False) -> Dict[str, Any]:
    """Retrieve logged wrong answers and summary counts."""
    items = get_wrong_answers(only_with_correction=only_with_correction)
    stats = get_cache_stats()
    return {
        "status": "ok",
        "total_wrong": len(items),
        "wrong_answers_count": stats.get("wrong_answers_count", 0),
        "user_overrides_count": stats.get("user_overrides_count", 0),
        "items": items,
    }

