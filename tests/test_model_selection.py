"""Comprehensive test suite for Model Selection option in the Dashboard & Backend."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.app import app as backend_app
from backend.dashboard_server import app as dashboard_app
from backend.model_manager import (
    AUTO_ID,
    GROQ_ONLY_ID,
    OPENROUTER_ONLY_ID,
    PRIMARY_A_ID,
    PRIMARY_B_ID,
    build_custom_text_cascade,
    get_active_model_selection,
    get_available_models_catalog,
    get_model_display_label,
    normalize_model_identifier,
    reset_active_model_selection,
    set_active_model_selection,
)
from backend.schemas import SolveRequest
from backend.telemetry_db import get_db_connection, init_db


@pytest.fixture
def temp_db() -> Generator[Path, None, None]:
    """Provide an isolated temporary telemetry database."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_model_selection_")
    os.close(fd)
    db_path = Path(path)
    init_db(db_path)
    reset_active_model_selection(db_path)
    try:
        yield db_path
    finally:
        reset_active_model_selection(db_path)
        if db_path.exists():
            try:
                db_path.unlink()
            except Exception:
                pass


@pytest.fixture
def dashboard_client() -> Generator[TestClient, None, None]:
    """Provide TestClient for dashboard server."""
    with TestClient(dashboard_app) as client:
        yield client


@pytest.fixture
def backend_client() -> Generator[TestClient, None, None]:
    """Provide TestClient for main backend server."""
    with TestClient(backend_app) as client:
        yield client


# ==============================================================================
# 1. Normalization & Display Label Tests
# ==============================================================================
def test_normalize_model_identifier_variants() -> None:
    """Verify deterministic normalization of strings, aliases, and labels."""
    # Auto
    assert normalize_model_identifier("auto") == "auto"
    assert normalize_model_identifier("Auto") == "auto"
    assert normalize_model_identifier("default") == "auto"
    assert normalize_model_identifier(None) == "auto"
    assert normalize_model_identifier("") == "auto"
    assert normalize_model_identifier("   ") == "auto"

    # Primary Profiles
    assert normalize_model_identifier("primary_a") == "primary_a"
    assert normalize_model_identifier("Primary A") == "primary_a"
    assert normalize_model_identifier("Primary A (openai/gpt-oss-120b)") == "primary_a"
    assert normalize_model_identifier("pri_a") == "primary_a"
    assert normalize_model_identifier("primary_b") == "primary_b"
    assert normalize_model_identifier("Primary B") == "primary_b"
    assert normalize_model_identifier("pri_b") == "primary_b"

    # Provider Modes
    assert normalize_model_identifier("groq_only") == "groq_only"
    assert normalize_model_identifier("Groq Only") == "groq_only"
    assert normalize_model_identifier("groq-only") == "groq_only"
    assert normalize_model_identifier("groq") == "groq_only"
    assert normalize_model_identifier("openrouter_only") == "openrouter_only"
    assert normalize_model_identifier("OpenRouter Only") == "openrouter_only"
    assert normalize_model_identifier("openrouter") == "openrouter_only"

    # Specific Models
    assert normalize_model_identifier("groq:openai/gpt-oss-120b") == "groq:openai/gpt-oss-120b"
    assert normalize_model_identifier("openai/gpt-oss-20b") == "groq:openai/gpt-oss-20b"
    assert normalize_model_identifier("openrouter:nvidia/nemotron-3.5-super-vl:free") == "openrouter:nvidia/nemotron-3.5-super-vl:free"
    assert normalize_model_identifier("google/gemma-4-31b-it:free") == "openrouter:google/gemma-4-31b-it:free"


def test_get_model_display_labels() -> None:
    """Verify clean, human-readable display labels for all model identifiers."""
    assert "Auto" in get_model_display_label("auto")
    assert "Primary A" in get_model_display_label("primary_a")
    assert "Primary B" in get_model_display_label("primary_b")
    assert "Groq Only" in get_model_display_label("groq_only")
    assert "OpenRouter Only" in get_model_display_label("openrouter_only")
    assert "Groq • openai/gpt-oss-20b" == get_model_display_label("groq:openai/gpt-oss-20b")
    assert "OpenRouter • gemma" in get_model_display_label("openrouter:gemma")


# ==============================================================================
# 2. Persistence in SQLite WAL & In-Memory Cache
# ==============================================================================
def test_persistence_in_sqlite(temp_db: Path) -> None:
    """Verify that model selection persists to system_settings in SQLite."""
    conn = get_db_connection(temp_db)
    try:
        # Table exists
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='system_settings';"
            ).fetchall()
        ]
        assert len(tables) == 1
    finally:
        conn.close()

    # Initial state should be default auto
    init_val = get_active_model_selection(db_path=temp_db)
    assert init_val == AUTO_ID

    # Set to Primary B
    res_b = set_active_model_selection("Primary B", db_path=temp_db)
    assert res_b["status"] == "ok"
    assert res_b["selected_model"] == PRIMARY_B_ID
    assert get_active_model_selection(db_path=temp_db) == PRIMARY_B_ID

    # Read directly from SQLite row
    conn = get_db_connection(temp_db)
    try:
        row = conn.execute("SELECT value FROM system_settings WHERE key = 'active_model_selection';").fetchone()
        assert row is not None
        assert row["value"] == PRIMARY_B_ID
    finally:
        conn.close()

    # Set to Groq Only
    set_active_model_selection("groq_only", db_path=temp_db)
    assert get_active_model_selection(db_path=temp_db) == GROQ_ONLY_ID

    # Reset
    reset_active_model_selection(db_path=temp_db)
    assert get_active_model_selection(db_path=temp_db) == AUTO_ID


# ==============================================================================
# 3. Model Catalog Generation
# ==============================================================================
def test_get_available_models_catalog() -> None:
    """Verify catalog structure and all required groups are present."""
    catalog = get_available_models_catalog()
    assert "groups" in catalog
    groups = {g["label"]: g["options"] for g in catalog["groups"]}

    assert "Automated" in groups
    assert any(o["id"] == "auto" for o in groups["Automated"])

    assert "Primary Profiles" in groups
    assert any(o["id"] == "primary_a" for o in groups["Primary Profiles"])
    assert any(o["id"] == "primary_b" for o in groups["Primary Profiles"])

    assert "Provider Modes" in groups
    assert any(o["id"] == "groq_only" for o in groups["Provider Modes"])
    assert any(o["id"] == "openrouter_only" for o in groups["Provider Modes"])

    assert "Specific Groq Models" in groups
    assert any("gpt-oss-120b" in o["id"] for o in groups["Specific Groq Models"])
    assert any("qwen" in o["id"] for o in groups["Specific Groq Models"])
    assert any("gpt-oss-20b" in o["id"] for o in groups["Specific Groq Models"])

    assert "Specific OpenRouter Models" in groups
    assert any("nemotron" in o["id"] for o in groups["Specific OpenRouter Models"])
    assert any("gemma" in o["id"] for o in groups["Specific OpenRouter Models"])


# ==============================================================================
# 4. Dynamic Cascade Construction
# ==============================================================================
def test_build_custom_text_cascade_behavior() -> None:
    """Verify tailored cascade priority and provider filtering for each choice."""
    base_cascade = [
        {"provider": "groq", "model": "openai/gpt-oss-120b", "tier": "S", "priority": 1},
        {"provider": "groq", "model": "qwen/qwen3.8-27b", "tier": "S", "priority": 2},
        {"provider": "groq", "model": "openai/gpt-oss-20b", "tier": "A", "priority": 3},
        {"provider": "openrouter", "model": "nvidia/nemotron-3.5-super-vl:free", "tier": "S", "priority": 4},
        {"provider": "openrouter", "model": "google/gemma-4-31b-it:free", "tier": "S", "priority": 5},
    ]
    config = {
        "primary_a": {"provider": "groq", "model": "openai/gpt-oss-120b"},
        "primary_b": {"provider": "groq", "model": "qwen/qwen3.8-27b"},
    }

    # 1. AUTO: unchanged
    cascade_auto = build_custom_text_cascade("auto", base_cascade, config)
    assert len(cascade_auto) == len(base_cascade)
    assert cascade_auto[0]["model"] == "openai/gpt-oss-120b"
    assert cascade_auto[1]["model"] == "qwen/qwen3.8-27b"

    # 2. PRIMARY A: Primary A is position 0
    cascade_pri_a = build_custom_text_cascade("primary_a", base_cascade, config)
    assert cascade_pri_a[0]["model"] == "openai/gpt-oss-120b"
    assert cascade_pri_a[0]["priority"] == 1

    # 3. PRIMARY B: Primary B is position 0
    cascade_pri_b = build_custom_text_cascade("primary_b", base_cascade, config)
    assert cascade_pri_b[0]["model"] == "qwen/qwen3.8-27b"
    assert cascade_pri_b[0]["priority"] == 1
    assert cascade_pri_b[1]["model"] == "openai/gpt-oss-120b"

    # 4. GROQ ONLY: strictly excludes OpenRouter models
    cascade_groq = build_custom_text_cascade("groq_only", base_cascade, config)
    assert len(cascade_groq) == 3
    for it in cascade_groq:
        assert it["provider"].lower() == "groq"

    # 5. OPENROUTER ONLY: strictly excludes Groq models
    cascade_or = build_custom_text_cascade("openrouter_only", base_cascade, config)
    assert len(cascade_or) >= 2
    for it in cascade_or:
        assert it["provider"].lower() == "openrouter"

    # 6. SPECIFIC MODEL (Groq Fast gpt-oss-20b)
    cascade_spec = build_custom_text_cascade("groq:openai/gpt-oss-20b", base_cascade, config)
    assert cascade_spec[0]["model"] == "openai/gpt-oss-20b"
    assert cascade_spec[0]["priority"] == 1
    assert cascade_spec[0]["provider"] == "groq"


# ==============================================================================
# 5. Dashboard Server Endpoints & UI Integration
# ==============================================================================
def test_dashboard_model_selection_endpoints(dashboard_client: TestClient) -> None:
    """Verify GET and POST /api/dashboard/model-selection."""
    # Initial GET
    get_res = dashboard_client.get("/api/dashboard/model-selection")
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["status"] == "ok"
    assert "selected_model" in data
    assert "catalog" in data

    # POST new selection
    post_res = dashboard_client.post(
        "/api/dashboard/model-selection",
        json={"model": "primary_b"},
    )
    assert post_res.status_code == 200
    post_data = post_res.json()
    assert post_data["status"] == "ok"
    assert post_data["selected_model"] == "primary_b"
    assert "Primary B" in post_data["selected_label"]

    # Verify GET reflects update
    get_res2 = dashboard_client.get("/api/dashboard/model-selection")
    assert get_res2.status_code == 200
    assert get_res2.json()["selected_model"] == "primary_b"

    # Test invalid / malformed payload falls back to auto cleanly without error
    bad_post = dashboard_client.post(
        "/api/dashboard/model-selection",
        content="not-json",
        headers={"Content-Type": "application/json"},
    )
    assert bad_post.status_code == 200
    assert bad_post.json()["selected_model"] == "auto"

    # Reset back to auto
    reset_active_model_selection()


def test_dashboard_ui_html_and_js_elements(dashboard_client: TestClient) -> None:
    """Verify dashboard HTML and JS contain model selection dropdown and hooks."""
    html_res = dashboard_client.get("/")
    assert html_res.status_code == 200
    html_text = html_res.text

    # Verify model-selector elements exist
    assert 'id="model-select"' in html_text
    assert 'value="auto"' in html_text
    assert 'value="primary_a"' in html_text
    assert 'value="primary_b"' in html_text
    assert 'value="groq_only"' in html_text
    assert 'value="openrouter_only"' in html_text
    assert 'id="backend-active-model-badge"' in html_text

    # Verify dashboard.js contains model handling logic
    js_res = dashboard_client.get("/static/dashboard.js")
    assert js_res.status_code == 200
    assert "fetchModelSelection" in js_res.text
    assert "handleModelChange" in js_res.text
    assert "modelSelect" in js_res.text


# ==============================================================================
# 6. Backend API Integration & Solve Override
# ==============================================================================
def test_backend_model_selection_and_health(backend_client: TestClient) -> None:
    """Verify backend /api/model-selection and /health include active model."""
    # Health check contains active_model_selection
    health_res = backend_client.get("/health")
    assert health_res.status_code == 200
    health_data = health_res.json()
    assert "active_model_selection" in health_data

    # GET /api/model-selection
    get_res = backend_client.get("/api/model-selection")
    assert get_res.status_code == 200
    assert get_res.json()["status"] == "ok"

    # POST /api/model-selection
    post_res = backend_client.post("/api/model-selection", json={"model": "groq_only"})
    assert post_res.status_code == 200
    assert post_res.json()["selected_model"] == "groq_only"

    # Health check now reflects groq_only
    health_res2 = backend_client.get("/health")
    assert health_res2.json()["active_model_selection"] == "groq_only"

    # Reset
    backend_client.post("/api/model-selection", json={"model": "auto"})


def test_solve_endpoint_with_model_override(backend_client: TestClient) -> None:
    """Verify SolveRequest accepts model override and executes without errors."""
    req_payload = {
        "subject": "Computer Science",
        "q_type": "mcq",
        "question": "What is the powerhouse of the cell?",
        "options": ["Ribosome", "Mitochondria", "Nucleus", "Chloroplast"],
        "bypass_cache": True,
        "model": "primary_b",
    }
    # Solve request should validate and execute cleanly without 500 error
    res = backend_client.post("/solve", json=req_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ("ok", "uncertain")
    assert "answer" in data

