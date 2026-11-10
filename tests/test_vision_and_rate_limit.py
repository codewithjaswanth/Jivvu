"""
Tests for Vision/Multimodal extraction, binary tree diameter prompt rules,
single-primary cascade, OpenRouter 429 provider cooldown, and pythonw.exe process isolation.
"""

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.cache import compute_cache_key, cache_solution, get_cached_solution, clear_cache
from backend.core.llm_client import (
    SolverResponse,
    solve_question,
    set_provider_cooldown,
    is_provider_in_cooldown,
    reset_provider_cooldown,
    reset_key_quarantine,
    reset_model_quarantine,
    set_model_cooldown,
    is_model_in_cooldown,
    reset_model_cooldown,
    is_key_quarantined,
    call_single_completion,
    run_hedged_openrouter_fallback,
    ModelResponse,
    extract_svg_topology_from_xml,
    download_image_as_base64_raster,
)
from backend.core.normalizer import NormalizedAnswer
from backend.core.parser import ParsedLLMResponse
from backend.core.prompts import SYSTEM_PROMPT, build_prompt_messages, format_user_content
from backend.lifecycle_manager import find_python_exe
from backend.cli import get_python_executable, DEFAULT_VENV_PYTHON


def setup_function():
    clear_cache()
    reset_provider_cooldown()
    reset_key_quarantine()
    reset_model_quarantine()
    reset_model_cooldown()


def teardown_function():
    clear_cache()
    reset_provider_cooldown()
    reset_key_quarantine()
    reset_model_quarantine()
    reset_model_cooldown()


# ==============================================================================
# 1. CACHE KEY DIFFERENTIATION BY IMAGE DATA
# ==============================================================================
def test_1_cache_key_differs_when_text_stem_identical_but_image_differs():
    """Verify that identical text questions with different or missing image_data yield distinct cache keys."""
    stem = "Given the binary tree, what is the diameter of it?"
    opts = ["3", "4", "5", "6"]

    # Key without image
    key_text_only = compute_cache_key("Computer Science", "mcq", stem, opts, image_data=None)

    # Key with image A
    img_a = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    key_img_a = compute_cache_key("Computer Science", "mcq", stem, opts, image_data=img_a)

    # Key with image B
    img_b = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mNk+M9Qz8DAwMTAAAAAVgEXV0oN+gAAAABJRU5ErkJggg=="
    key_img_b = compute_cache_key("Computer Science", "mcq", stem, opts, image_data=img_b)

    assert key_text_only != key_img_a, "Cache key without image must differ from key with image"
    assert key_img_a != key_img_b, "Cache key with Image A must differ from key with Image B"

    # Verify cache isolation in storage
    sol_a = SolverResponse(
        status="OK",
        confidence_tier="HIGH_VISION_SOLVER",
        canonical_key="C",
        selected_index=2,
        selected_text="5",
        reason="Longest path has 5 edges.",
    )
    cache_solution("Computer Science", "mcq", stem, opts, sol_a, image_data=img_a)

    # Lookup with image_a finds solution
    cached_a = get_cached_solution("Computer Science", "mcq", stem, opts, image_data=img_a)
    assert cached_a is not None
    assert cached_a["answer"] == "C"
    assert cached_a["selected_index"] == 2
    assert cached_a["selected_text"] == "5"

    # Lookup with image_b or None returns None (no poison across images)
    cached_b = get_cached_solution("Computer Science", "mcq", stem, opts, image_data=img_b)
    assert cached_b is None

    cached_none = get_cached_solution("Computer Science", "mcq", stem, opts, image_data=None)
    assert cached_none is None


# ==============================================================================
# 2. TREE DIAMETER PROMPT INSTRUCTIONS & EDGE VS NODE REASONING
# ==============================================================================
def test_2_tree_diameter_prompt_rules_and_multimodal_formatting():
    """Verify Rule 10 in SYSTEM_PROMPT mandates edge-counting for binary tree diameter."""
    assert "VISUAL & GRAPH/TREE REASONING DIRECTIVE" in SYSTEM_PROMPT
    assert "BINARY TREE DIAMETER QUESTIONS" in SYSTEM_PROMPT
    assert "CCBP / GRAPH CONVENTION" in SYSTEM_PROMPT
    assert "Diameter is defined as the number of EDGES" in SYSTEM_PROMPT
    assert "select the EDGE count" in SYSTEM_PROMPT

    # Verify raster multimodal message construction
    raster_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    messages_raster = build_prompt_messages(
        subject="Data Structures",
        q_type="mcq",
        question="Given the binary tree, what is the diameter of it?",
        options=["3", "4", "5", "6"],
        image_data=raster_url,
    )
    assert len(messages_raster) == 2
    assert messages_raster[0]["role"] == "system"
    assert messages_raster[1]["role"] == "user"

    user_content_raster = messages_raster[1]["content"]
    assert isinstance(user_content_raster, list), "Multimodal content must be a list of parts"
    assert user_content_raster[0]["type"] == "text"
    assert user_content_raster[1]["type"] == "image_url"
    assert user_content_raster[1]["image_url"]["url"] == raster_url

    # Verify SVG Guard: SVG data URL is NEVER formatted as image_url (Vision APIs reject SVG with HTTP 400)
    svg_url = "data:image/svg+xml;charset=utf-8,%3Csvg%3E%3C/svg%3E"
    messages_svg = build_prompt_messages(
        subject="Data Structures",
        q_type="mcq",
        question="Given the binary tree, what is the diameter of it?",
        options=["3", "4", "5", "6"],
        image_data=svg_url,
    )
    user_content_svg = messages_svg[1]["content"]
    assert isinstance(user_content_svg, str), "SVG must not be formatted as an image_url object"
    assert "data:image/svg" not in str(user_content_svg)


# ==============================================================================
# 3. OPENROUTER :FREE 429 TRIGGERS 30S COOLDOWN & PREVENTS FAN-OUT
# ==============================================================================
def test_3_openrouter_429_sets_provider_cooldown_and_prevents_fanout():
    """Verify that when OpenRouter returns 429, a 30s provider cooldown stops subsequent fallback calls."""
    reset_provider_cooldown()
    assert not is_provider_in_cooldown("openrouter")

    # Set cooldown
    set_provider_cooldown("openrouter", 30.0)
    assert is_provider_in_cooldown("openrouter")

    # Calling run_hedged_openrouter_fallback while in cooldown must exit immediately without network calls
    mock_client = MagicMock()
    res, models, details = asyncio.run(
        run_hedged_openrouter_fallback(
            client=mock_client,
            fallback_pool=["google/gemma-4-31b-it:free", "nvidia/nemotron-3.5-lightning:free"],
            messages=[{"role": "user", "content": "test"}],
            q_type="mcq",
            options=["A", "B"],
            timeout_ms=3000.0,
            norm_a=None,
            norm_b=None,
            succ_a=False,
            succ_b=False,
        )
    )

    assert res is None
    assert models == []
    assert details.get("error") == "RATE_LIMIT_429_COOLDOWN"
    mock_client.post.assert_not_called()
    reset_provider_cooldown()


# ==============================================================================
# 4. SINGLE-PRIMARY CASCADE (1 API CALL FOOTPRINT)
# ==============================================================================
def test_4_single_primary_cascade_returns_immediately_on_high_confidence():
    """Verify that when primary_a succeeds, single-primary cascade returns immediately without calling primary_b."""
    norm_a = NormalizedAnswer(q_type="mcq", canonical_key="B", selected_index=1, selected_text="Beta", ordered_texts=[], permutation_indices=[])
    parsed_a = ParsedLLMResponse(answer="B", reason="Derived Beta step-by-step from first principles.")

    calls = []

    async def mock_call_pri(client, provider, model, messages, api_key, timeout_ms, q_type, options, *args, **kwargs):
        calls.append(model)
        if "120b" in model:
            return ModelResponse(answer="B", reason="Derived Beta step-by-step.", norm=norm_a), 85.0, None
        return ModelResponse(answer="A", reason="Derived Alpha.", norm=None), 85.0, None

    test_cfg = {
        "single_primary_cascade": True,
        "primary_a": {"provider": "groq", "model": "openai/gpt-oss-120b"},
        "primary_b": {"provider": "groq", "model": "qwen/qwen3.8-27b"},
        "timeouts_ms": {"groq_parallel_timeout_ms": 2000, "hard_total_budget_ms": 4000},
    }

    with patch.dict(os.environ, {"QUIZZY_SINGLE_PRIMARY": "1"}):
        with patch("backend.core.llm_client.call_groq_primary", side_effect=mock_call_pri):
            sol = asyncio.run(
                solve_question(
                    subject="Physics",
                    q_type="mcq",
                    question="Which is Beta?",
                    options=["Alpha", "Beta", "Gamma"],
                    config=test_cfg,
                )
            )

    assert sol.status == "OK"
    assert sol.confidence_tier == "SINGLE_PRIMARY_PASSED"
    assert sol.canonical_key == "B"
    assert sol.selected_index == 1
    # Crucial: ONLY primary_a was called! Zero fan-out to primary_b or fallback!
    assert calls == ["openai/gpt-oss-120b"], f"Expected only primary_a, but got: {calls}"
    assert sol.models_called == ["primary_a"]


# ==============================================================================
# 5. PYTHONW.EXE ZERO-WINDOW GUI EXECUTABLE RESOLUTION
# ==============================================================================
def test_5_pythonw_executable_resolution_for_windowless_background():
    """Verify that backend and daemon resolve pythonw.exe first for true zero-window background execution."""
    resolved_exe = str(find_python_exe()).lower()
    assert "pythonw.exe" in resolved_exe, f"Expected pythonw.exe, got: {resolved_exe}"

    gui_exe = str(get_python_executable(prefer_gui=True)).lower()
    assert "pythonw.exe" in gui_exe, f"Expected pythonw.exe, got: {gui_exe}"

    default_venv = str(DEFAULT_VENV_PYTHON).lower()
    assert "pythonw.exe" in default_venv, f"Expected DEFAULT_VENV_PYTHON to end in pythonw.exe, got: {DEFAULT_VENV_PYTHON}"


# ==============================================================================
# 6. SVG GRAPH TOPOLOGY EXTRACTION FOR TREE 1 AND TREE 2 (5 EDGES EACH)
# ==============================================================================
def test_6_svg_graph_topology_tree_1_and_tree_2_diameter():
    """Verify SVG graph parser reconstructs Tree 1 and Tree 2 from CCBP screenshots, yielding 5 edges for both."""
    svg_tree_1 = """<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">
      <circle cx="200" cy="40" r="15" />
      <text x="200" y="45">11</text>
      <line x1="200" y1="40" x2="120" y2="100" />
      <circle cx="120" cy="100" r="15" />
      <text x="120" y="105">18</text>
      <line x1="200" y1="40" x2="280" y2="100" />
      <circle cx="280" cy="100" r="15" />
      <text x="280" y="105">20</text>
      <line x1="120" y1="100" x2="70" y2="160" />
      <circle cx="70" cy="160" r="15" />
      <text x="70" y="165">22</text>
      <line x1="120" y1="100" x2="150" y2="160" />
      <circle cx="150" cy="160" r="15" />
      <text x="150" y="165">3</text>
      <line x1="280" y1="100" x2="250" y2="160" />
      <circle cx="250" cy="160" r="15" />
      <text x="250" y="165">54</text>
      <line x1="280" y1="100" x2="310" y2="160" />
      <circle cx="310" cy="160" r="15" />
      <text x="310" y="165">15</text>
      <line x1="70" y1="160" x2="40" y2="220" />
      <circle cx="40" cy="220" r="15" />
      <text x="40" y="225">23</text>
      <line x1="70" y1="160" x2="90" y2="220" />
      <circle cx="90" cy="220" r="15" />
      <text x="90" y="225">21</text>
    </svg>"""

    svg_tree_2 = """<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">
      <circle cx="200" cy="40" r="15" />
      <text x="200" y="45">1</text>
      <line x1="200" y1="40" x2="120" y2="100" />
      <circle cx="120" cy="100" r="15" />
      <text x="120" y="105">2</text>
      <line x1="200" y1="40" x2="280" y2="100" />
      <circle cx="280" cy="100" r="15" />
      <text x="280" y="105">3</text>
      <line x1="120" y1="100" x2="70" y2="160" />
      <circle cx="70" cy="160" r="15" />
      <text x="70" y="165">4</text>
      <line x1="120" y1="100" x2="150" y2="160" />
      <circle cx="150" cy="160" r="15" />
      <text x="150" y="165">5</text>
      <line x1="280" y1="100" x2="250" y2="160" />
      <circle cx="250" cy="160" r="15" />
      <text x="250" y="165">6</text>
      <line x1="280" y1="100" x2="310" y2="160" />
      <circle cx="310" cy="160" r="15" />
      <text x="310" y="165">7</text>
      <line x1="150" y1="160" x2="150" y2="220" />
      <circle cx="150" cy="220" r="15" />
      <text x="150" y="225">8</text>
    </svg>"""

    res1 = extract_svg_topology_from_xml(svg_tree_1)
    assert res1 is not None
    assert "Root Node: \"11\"" in res1
    assert "Tree Diameter (Number of Edges): 5" in res1
    assert "Tree Diameter (Number of Nodes on Longest Path): 6" in res1

    res2 = extract_svg_topology_from_xml(svg_tree_2)
    assert res2 is not None
    assert "Root Node: \"1\"" in res2
    assert "Tree Diameter (Number of Edges): 5" in res2
    assert "Tree Diameter (Number of Nodes on Longest Path): 6" in res2


# ==============================================================================
# 7. SVG SAFETY GUARD (CONVERTS SVG TO TOPOLOGY, NEVER SENDS SVG TO VISION APIS)
# ==============================================================================
def test_7_svg_safety_guard_converts_svg_to_topology_in_stem_and_clears_image_data():
    """Verify solve_question strips SVG image_data and routes to topology-assisted text solver."""
    svg_data = "data:image/svg+xml;charset=utf-8," + "<svg><circle cx='10' cy='10'/><text x='10' y='10'>Root</text></svg>"

    captured_messages = []

    async def mock_call(client, provider, model, messages, api_key, timeout_ms, q_type, options, *args, **kwargs):
        captured_messages.extend(messages)
        norm = NormalizedAnswer(q_type="mcq", canonical_key="C", selected_index=2, selected_text="5", ordered_texts=[], permutation_indices=[])
        parsed = ParsedLLMResponse(answer="C", reason="Topology shows diameter of 5 edges.")
        return "", parsed, norm, 50.0, None

    with patch("backend.core.llm_client.load_groq_api_keys", return_value=["test_groq_key"]):
        with patch("backend.core.llm_client.call_model_with_budget_retry", side_effect=mock_call):
            sol = asyncio.run(
                solve_question(
                    subject="Computer Science",
                    q_type="mcq",
                    question="Given the binary tree, what is the diameter of it?",
                    options=["3", "4", "5", "6"],
                    image_data=svg_data,
                )
            )

    assert sol.status == "OK"
    assert sol.confidence_tier == "HIGH_TOPOLOGY_SOLVER"
    assert sol.canonical_key == "C"

    # Verify no message content had image_url with data:image/svg
    for msg in captured_messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "image_url":
                    assert not part["image_url"]["url"].startswith("data:image/svg"), "SVG must never be in image_url"


# ==============================================================================
# 8. GROQ VISION TIER 1 PRIORITY OVER OPENROUTER
# ==============================================================================
def test_8_groq_vision_tier_1_priority_over_openrouter():
    """Verify that raster diagram questions route to Groq Vision Tier 1 before touching OpenRouter."""
    raster_data = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    providers_called = []

    async def mock_vision_call(client, provider, model, messages, api_key, timeout_ms, q_type, options, *args, **kwargs):
        providers_called.append((provider, model))
        norm = NormalizedAnswer(q_type="mcq", canonical_key="C", selected_index=2, selected_text="5", ordered_texts=[], permutation_indices=[])
        parsed = ParsedLLMResponse(answer="C", reason="Longest path in binary tree has 5 edges.")
        return "", parsed, norm, 120.0, None

    with patch("backend.core.llm_client.load_groq_api_keys", return_value=["test_groq_key"]):
        with patch("backend.core.llm_client.load_openrouter_api_keys", return_value=["test_or_key"]):
            with patch("backend.core.llm_client.call_model_with_budget_retry", side_effect=mock_vision_call):
                sol = asyncio.run(
                    solve_question(
                        subject="Computer Science",
                        q_type="mcq",
                        question="Given the binary tree, what is the diameter of it?",
                        options=["3", "4", "5", "6"],
                        image_data=raster_data,
                    )
                )

    assert sol.status == "OK"
    assert sol.confidence_tier == "HIGH_VISION_SOLVER"
    assert len(providers_called) == 1
    # Crucial: First and only provider called was Groq Vision!
    assert providers_called[0][0] == "groq"
    assert providers_called[0][1] == "meta-llama/llama-4-scout-17b-16e-instruct"
    assert sol.models_called == ["vision:groq/meta-llama/llama-4-scout-17b-16e-instruct"]


# ==============================================================================
# 9. CROSS-ORIGIN IMAGE_URL DOWNLOAD & RASTERIZATION
# ==============================================================================
def test_9_cross_origin_image_url_download_rasterization():
    """Verify download_image_as_base64_raster fetches cross-origin image bytes and outputs valid raster data URL."""
    fake_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_png_bytes
    mock_resp.headers = {"content-type": "image/png"}

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    res = asyncio.run(
        download_image_as_base64_raster("https://assets.ccbp.in/frontend/content/diagram.png", client=mock_client)
    )
    assert res is not None
    assert res.startswith("data:image/png;base64,")
    assert not res.startswith("data:image/svg")


# ==============================================================================
# 10. GROQ PER-MODEL COOLDOWN ROTATION WITHOUT KEY QUARANTINE
# ==============================================================================
def test_10_groq_per_model_cooldown_rotates_without_key_quarantine():
    """Verify that a 429 on one Groq model sets per-model cooldown, leaves the API key active, and cascades to the next Groq model."""
    called_models = []

    async def mock_call(client, provider, model, messages, api_key, timeout_ms, q_type, options, *args, **kwargs):
        called_models.append((provider, model))
        if provider == "groq" and model == "openai/gpt-oss-120b":
            # Simulate 429 rate limit
            set_model_cooldown("groq", model, 12.0)
            return "", None, None, 45.0, "HTTP 429: Rate limit exceeded"

        # Next model succeeds
        norm = NormalizedAnswer(q_type="mcq", canonical_key="A", selected_index=0, selected_text="O(log n)", ordered_texts=[], permutation_indices=[])
        parsed = ParsedLLMResponse(answer="A", reason="Calculated correctly.")
        return "", parsed, norm, 80.0, None

    with patch("backend.core.llm_client.load_groq_api_keys", return_value=["gsk_testkey_123"]):
        with patch("backend.core.llm_client.load_openrouter_api_keys", return_value=["or_testkey_456"]):
            with patch("backend.core.llm_client.call_model_with_budget_retry", side_effect=mock_call):
                sol = asyncio.run(
                    solve_question(
                        subject="Computer Science",
                        q_type="mcq",
                        question="What is the time complexity of binary search?",
                        options=["O(log n)", "O(n)", "O(n log n)", "O(1)"],
                    )
                )

    assert sol.status == "OK"
    assert is_model_in_cooldown("groq", "openai/gpt-oss-120b") is True
    # Crucial: Groq API key is NOT quarantined on 429
    assert is_key_quarantined("gsk_testkey_123") is False
    # Crucial: Provider groq as a whole is NOT in cooldown
    assert is_provider_in_cooldown("groq") is False
    # Verified rotation: gpt-oss-120b was tried first, then rotated to next model which succeeded
    assert len(called_models) >= 2
    assert called_models[0] == ("groq", "openai/gpt-oss-120b")
    assert called_models[1][0] == "groq"
    assert called_models[1][1] != "openai/gpt-oss-120b"


# ==============================================================================
# 11. SELF-HEALING SERVER-SIDE RETRY ABSORBS TRANSIENT 429
# ==============================================================================
def test_11_self_healing_server_side_retry_absorbs_transient_429():
    """Verify that when models hit 429 in Wave 1, the solver self-heals by retrying in Wave 2 and succeeding."""
    attempt_count = 0

    async def mock_wave_call(client, provider, model, messages, api_key, timeout_ms, q_type, options, *args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            set_model_cooldown(provider, model, 0.05)
            return "", None, None, 50.0, "HTTP 429: Rate limit exceeded"

        # Succeed on subsequent attempt
        reset_model_cooldown(provider, model)
        norm = NormalizedAnswer(q_type="mcq", canonical_key="B", selected_index=1, selected_text="O(n)", ordered_texts=[], permutation_indices=[])
        parsed = ParsedLLMResponse(answer="B", reason="Linear search complexity is O(n).")
        return "", parsed, norm, 95.0, None

    with patch.dict(os.environ, {"QUIZZY_SINGLE_PRIMARY": "1"}):
        with patch("backend.core.llm_client.load_groq_api_keys", return_value=["gsk_test"]):
            with patch("backend.core.llm_client.call_model_with_budget_retry", side_effect=mock_wave_call):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    sol = asyncio.run(
                        solve_question(
                            subject="Computer Science",
                            q_type="mcq",
                            question="What is the time complexity of linear search?",
                            options=["O(1)", "O(n)", "O(n^2)", "O(log n)"],
                        )
                    )

    assert sol.status == "OK"
    assert sol.canonical_key == "B"
    assert attempt_count >= 2


# ==============================================================================
# 12. IN-FLIGHT CONCURRENT REQUEST DEDUPLICATION
# ==============================================================================
def test_12_in_flight_concurrent_request_deduplication():
    """Verify that multiple concurrent requests for the exact same question coalesce to 1 upstream solve."""
    from backend.app import _solve_core
    from backend.schemas import SolveRequest

    solve_count = 0

    async def mock_solve(*args, **kwargs):
        nonlocal solve_count
        solve_count += 1
        await asyncio.sleep(0.05)
        return SolverResponse(
            status="OK",
            confidence_tier="HIGH_FIRST_PRIMARY_PASSED",
            canonical_key="A",
            selected_index=0,
            selected_text="Answer A",
            reason="Verified answer.",
            latency_ms=50.0,
            models_called=["groq/test-model"],
        )

    req = SolveRequest(
        subject="Mathematics",
        q_type="mcq",
        question="What is 2 + 2?",
        options=["4", "5", "6", "7"],
        bypass_cache=True,
    )

    async def run_concurrent():
        with patch("backend.app.solve_question", side_effect=mock_solve):
            res1, res2 = await asyncio.gather(
                _solve_core(req),
                _solve_core(req),
            )
            return res1, res2

    (resp1, trace1), (resp2, trace2) = asyncio.run(run_concurrent())

    assert resp1.status == "ok"
    assert resp2.status == "ok"
    assert resp1.answer == resp2.answer == "A"
    assert solve_count == 1


# ==============================================================================
# 13. RECENT SOLVE COALESCE SUPPRESSES DUPLICATE TELEMETRY WITHIN 2.5S
# ==============================================================================
def test_13_recent_solve_coalesce_within_2_5s():
    """Verify that a second request within 2.5s is served from coalesce cache without firing another LLM call."""
    from backend.app import _solve_core
    from backend.schemas import SolveRequest

    solve_count = 0

    async def mock_solve(*args, **kwargs):
        nonlocal solve_count
        solve_count += 1
        return SolverResponse(
            status="OK",
            confidence_tier="HIGH_FIRST_PRIMARY_PASSED",
            canonical_key="C",
            selected_index=2,
            selected_text="Carbon",
            reason="Chemistry verified.",
            latency_ms=60.0,
            models_called=["groq/test-model"],
        )

    req = SolveRequest(
        subject="Chemistry",
        q_type="mcq",
        question="What element has atomic number 6?",
        options=["Hydrogen", "Helium", "Carbon", "Oxygen"],
    )

    async def run_sequential():
        with patch("backend.app.solve_question", side_effect=mock_solve):
            res1 = await _solve_core(req)
            res2 = await _solve_core(req)
            return res1, res2

    (resp1, _), (resp2, _) = asyncio.run(run_sequential())

    assert resp1.status == "ok"
    assert resp2.status == "ok"
    assert resp1.answer == resp2.answer == "C"
    assert solve_count == 1


# ==============================================================================
# 14. URL-ENCODED CLOUDFRONT IMAGE EXTRACTION AND MULTI-PASS UNQUOTING
# ==============================================================================
def test_14_url_encoded_image_extraction_and_unquoting():
    """Verify that URL-encoded image URLs in questions (e.g. %3A%2F%2F) are unquoted and downloaded as raster."""
    encoded_url = "https%3A%2F%2Fd15dfoxfhy94.cloudfront.net%2Fbinary_tree_q13.png"
    stem = f"What is the diameter of this tree? [Image: {encoded_url}]"
    opts = ["3", "4", "5", "6"]

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "image/png"}
    mock_resp.content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    mock_client.get = AsyncMock(return_value=mock_resp)

    # 1. Direct raster download test with encoded URL
    raster_data = asyncio.run(download_image_as_base64_raster(encoded_url, client=mock_client))
    assert raster_data is not None
    assert raster_data.startswith("data:image/png;base64,")
    # Verify client called with fully unquoted URL
    called_url = mock_client.get.call_args[0][0]
    assert called_url == "https://d15dfoxfhy94.cloudfront.net/binary_tree_q13.png"

    # 2. End-to-end solve_question extracts from [Image: ...] stem and routes to Groq vision
    with patch("backend.core.llm_client.load_groq_api_keys", return_value=["gsk_mock_vision_key"]):
        with patch("backend.core.llm_client.call_model_with_budget_retry") as mock_call:
            mock_norm = NormalizedAnswer(
                canonical_key="C",
                selected_index=2,
                selected_text="5",
                raw_answer="C",
            )
            mock_parsed = ParsedLLMResponse(answer="C", reason="The tree diameter has 5 edges.")
            mock_call.return_value = ("{'answer': 'C'}", mock_parsed, mock_norm, 120.0, None)

            res = asyncio.run(solve_question(
                subject="Computer Science",
                q_type="mcq",
                question=stem,
                options=opts,
                client=mock_client,
            ))

            assert res.status == "OK"
            assert res.canonical_key == "C"
            assert res.selected_text == "5"
            assert "vision:groq" in res.models_called[0]


# ==============================================================================
# 15. THINKING/REASONING PARSER RESILIENCE AND OPTION SUBSTRING FALLBACK
# ==============================================================================
def test_15_reasoning_model_think_parse_and_option_fallback():
    """Verify that <think> tags and narrative conclusions parse cleanly without JSON syntax errors."""
    from backend.core.parser import parse_llm_response

    raw_with_think = (
        "<think>\n"
        "The worst case space complexity of maxPathSum on a skewed binary tree is determined by recursion depth.\n"
        "On a skewed binary tree, recursion depth is O(N).\n"
        "</think>\n"
        "```json\n"
        "{\n"
        '  "answer": "O(N)",\n'
        '  "reason": "Recursion call stack reaches O(N) frames on skewed tree."\n'
        "}\n"
        "```"
    )
    opts = ["O(1)", "O(log N)", "O(N)", "O(N^2)"]
    parsed = parse_llm_response(raw_with_think, options=opts)
    assert parsed.answer == "O(N)"
    assert "Recursion call stack" in parsed.reason

    # Narrative text without JSON block
    raw_narrative = (
        "<think>Evaluating options: A is O(1), B is O(log N), C is O(N), D is O(N^2).</think>\n"
        "Therefore, option C is correct because the space complexity of maxPathSum on skewed tree is O(N)."
    )
    parsed_narrative = parse_llm_response(raw_narrative, options=opts)
    assert parsed_narrative.answer == "C"


# ==============================================================================
# 16. PER-CALL TIMEOUT ENFORCEMENT (6.5S CAP) AND REASONING MAX_TOKENS FLOOR
# ==============================================================================
def test_16_per_call_timeout_and_max_tokens_enforcement():
    """Verify call_single_completion caps per-call timeout to 6.5s and elevates max_tokens for reasoning models."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={
        "choices": [{"message": {"content": '{"answer": "A"}'}}]
    })
    mock_client.post = AsyncMock(return_value=mock_resp)

    # 1. 20000ms timeout passed -> must be capped to 6.5s in client.post
    asyncio.run(call_single_completion(
        client=mock_client,
        provider="groq",
        model="qwen/qwen3-32b",
        messages=[{"role": "user", "content": "hi"}],
        api_key="gsk_mock_test",
        timeout_ms=20000.0,
        max_tokens=1024,
    ))

    post_call = mock_client.post.call_args
    assert post_call is not None
    _, kwargs = post_call
    assert kwargs.get("timeout") == 6.5, "Timeout must be capped to 6.5s"
    payload = kwargs.get("json", {})
    assert payload.get("max_tokens") == 2048, "Reasoning model qwen3-32b must enforce max_tokens >= 2048"


# ==============================================================================
# 17. ZERO INTERNAL_SERVER_ERROR GUARANTEE IN TELEMETRY CLASSIFICATION
# ==============================================================================
def test_17_telemetry_error_classification_zero_500():
    """Verify that cascade failures and uncertain responses are classified as CASCADE_EXHAUSTED, not 500."""
    from backend.telemetry_db import validate_accuracy_and_classify_error

    # Uncertain response when all cascade models are exhausted
    mock_resp = SolverResponse(
        status="UNCERTAIN",
        confidence_tier="CASCADE_FAILED",
        error="All cascade models failed",
        models_called=["groq/openai/gpt-oss-120b"],
        agreement_details={},
    )
    is_acc, err_type, status = validate_accuracy_and_classify_error(
        q_type="mcq",
        options=["A", "B", "C", "D"],
        response=mock_resp,
    )
    assert status == "FAILURE"
    assert err_type == "CASCADE_EXHAUSTED", f"Expected CASCADE_EXHAUSTED, got {err_type}"
    assert err_type != "INTERNAL_SERVER_ERROR"


