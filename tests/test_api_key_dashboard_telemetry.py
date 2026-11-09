"""Tests for Per-API-Key Telemetry, Sliding-Window Quota Accounting, and Dashboard Observability."""

import tempfile
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from backend.app import app as solver_app
from backend.core.key_pool import (
    get_all_configured_keys,
    get_key_identity,
    parse_reset_duration_seconds,
)
from backend.dashboard_server import app as dashboard_app
from backend.telemetry_db import (
    get_per_api_key_telemetry,
    init_db,
    record_api_key_call_telemetry,
    reset_key_cooldown_in_db,
)


def test_key_identity_and_masking():
    """Verify deterministic SHA256 key_id and non-leaking masked format."""
    raw = "AQ.Ab890123456789P1rw"
    ident1 = get_key_identity("gemini", raw, 0)
    ident2 = get_key_identity("gemini", raw, 0)

    assert ident1["key_id"] == ident2["key_id"]
    assert len(ident1["key_id"]) == 10
    assert ident1["provider"] == "gemini"
    assert ident1["key_label"] == "GEMINI Key #1"
    # Never expose raw key in masked format
    assert raw not in ident1["masked_key"]
    assert ident1["masked_key"] == "AQ.Ab8...P1rw"

    # Short key handling
    short_ident = get_key_identity("groq", "gsk_12345", 2)
    assert short_ident["key_label"] == "GROQ Key #3"
    assert "gsk_12345" not in short_ident["masked_key"]


def test_parse_reset_duration_seconds():
    """Test parsing various rate limit reset header formats."""
    assert abs(parse_reset_duration_seconds("2m30s") - 150.0) < 0.1
    assert abs(parse_reset_duration_seconds("450ms") - 0.45) < 0.01
    assert abs(parse_reset_duration_seconds("15s") - 15.0) < 0.1
    assert abs(parse_reset_duration_seconds("12.5") - 12.5) < 0.1
    assert parse_reset_duration_seconds("") == 0.0
    assert parse_reset_duration_seconds(None) == 0.0


def test_all_configured_keys_discovery():
    """Verify all configured keys across Gemini, Groq, OpenRouter are loaded."""
    keys = get_all_configured_keys()
    assert len(keys) >= 16

    gemini_keys = [k for k in keys if k["provider"] == "gemini"]
    groq_keys = [k for k in keys if k["provider"] == "groq"]
    openrouter_keys = [k for k in keys if k["provider"] == "openrouter"]

    assert len(gemini_keys) == 4
    assert len(groq_keys) >= 7
    assert len(openrouter_keys) >= 5


def test_telemetry_recording_and_sliding_window():
    """Test recording telemetry events into SQLite and calculating RPM/TPM."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_db = Path(tf.name)

    try:
        init_db(temp_db)
        ident = get_key_identity("gemini", "TEST_KEY_GEMINI_1", 0)

        # 1. Initially all keys are IDLE
        initial_telemetry = get_per_api_key_telemetry(db_path=temp_db)
        assert initial_telemetry["summary"]["total_keys"] >= 16

        # 2. Record 2 successful calls with token usage
        record_api_key_call_telemetry(
            key_id=ident["key_id"],
            provider=ident["provider"],
            masked_key=ident["masked_key"],
            key_label=ident["key_label"],
            model_id="gemini-3.8-flash",
            status_code=200,
            is_success=True,
            is_429=False,
            is_503=False,
            latency_ms=320.0,
            prompt_tokens=150,
            completion_tokens=50,
            thinking_tokens=30,
            total_tokens=230,
            db_path=temp_db,
        )

        record_api_key_call_telemetry(
            key_id=ident["key_id"],
            provider=ident["provider"],
            masked_key=ident["masked_key"],
            key_label=ident["key_label"],
            model_id="gemini-3.8-flash",
            status_code=200,
            is_success=True,
            is_429=False,
            is_503=False,
            latency_ms=280.0,
            prompt_tokens=100,
            completion_tokens=40,
            thinking_tokens=20,
            total_tokens=160,
            db_path=temp_db,
        )

        telemetry = get_per_api_key_telemetry(db_path=temp_db)
        key_status = next((k for k in telemetry["keys"] if k["key_id"] == ident["key_id"]), None)
        assert key_status is not None
        assert key_status["status"] == "HEALTHY"
        assert key_status["total_requests"] == 2
        assert key_status["successful_requests"] == 2
        assert key_status["failed_requests"] == 0
        assert key_status["total_tokens"] == 390
        assert key_status["thinking_tokens"] == 50
        assert key_status["rpm_current"] == 2
        assert key_status["tpm_current"] == 390

        # 3. Record a 429 rate limit with cooldown
        now = time.time()
        record_api_key_call_telemetry(
            key_id=ident["key_id"],
            provider=ident["provider"],
            masked_key=ident["masked_key"],
            key_label=ident["key_label"],
            model_id="gemini-3.8-flash",
            status_code=429,
            is_success=False,
            is_429=True,
            is_503=False,
            latency_ms=110.0,
            cooldown_until_epoch=now + 8.0,
            db_path=temp_db,
        )

        telemetry_cd = get_per_api_key_telemetry(db_path=temp_db)
        key_status_cd = next((k for k in telemetry_cd["keys"] if k["key_id"] == ident["key_id"]), None)
        assert key_status_cd["status"] == "COOLDOWN"
        assert key_status_cd["count_429"] == 1
        assert key_status_cd["cooldown_remaining_s"] > 0.0

        # 4. Reset cooldown in DB
        reset_res = reset_key_cooldown_in_db(key_id=ident["key_id"], db_path=temp_db)
        assert reset_res["status"] == "ok"
        assert reset_res["cleared_count"] == 1

        telemetry_reset = get_per_api_key_telemetry(db_path=temp_db)
        key_status_reset = next((k for k in telemetry_reset["keys"] if k["key_id"] == ident["key_id"]), None)
        assert key_status_reset["status"] == "HEALTHY"
        assert key_status_reset["cooldown_remaining_s"] == 0.0

    finally:
        if temp_db.exists():
            try:
                temp_db.unlink()
            except Exception:
                pass


def test_dashboard_and_solver_endpoints():
    """Verify REST endpoints for API key telemetry and state reset on both servers."""
    client_dash = TestClient(dashboard_app)
    client_solver = TestClient(solver_app)

    # 1. Dashboard: GET /api/dashboard/api-keys
    resp_dash = client_dash.get("/api/dashboard/api-keys")
    assert resp_dash.status_code == 200
    data_dash = resp_dash.json()
    assert "summary" in data_dash
    assert "keys" in data_dash
    assert data_dash["summary"]["total_keys"] >= 16

    # 2. Dashboard: POST /api/dashboard/api-keys/reset-cooldown
    resp_reset = client_dash.post("/api/dashboard/api-keys/reset-cooldown", json={"key_id": "all"})
    assert resp_reset.status_code == 200
    data_reset = resp_reset.json()
    assert data_reset["status"] == "ok"

    # 3. Solver: GET /api/key-pool-status
    resp_solver = client_solver.get("/api/key-pool-status")
    assert resp_solver.status_code == 200
    data_solver = resp_solver.json()
    assert data_solver["status"] == "ok"
    assert "summary" in data_solver
    assert "keys" in data_solver

    # 4. Solver: POST /api/reset-state
    resp_solver_reset = client_solver.post("/api/reset-state")
    assert resp_solver_reset.status_code == 200
    assert resp_solver_reset.json()["status"] == "ok"

