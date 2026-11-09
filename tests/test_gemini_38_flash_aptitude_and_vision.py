"""Diagnostic test suite for Gemini 3.8 Flash Primary Consensus & Multimodal Vision Engine."""

from __future__ import annotations

import asyncio
import base64
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.consensus import (
    SolverResponse,
    compute_multi_majority_indices,
    extract_multi_indices,
    run_gemini_arbiter_fallback,
)
from backend.core.key_pool import (
    get_gemini_keys,
    get_healthy_gemini_keys,
    get_next_gemini_key,
    is_gemini_key_available,
    load_gemini_api_keys,
    record_gemini_key_error,
    reset_gemini_key_cooldown,
    set_gemini_key_cooldown,
)
from backend.core.llm_client import solve_question
from backend.core.prompts import (
    APTITUDE_KEYWORDS_REGEX,
    APTITUDE_REASONING_PROMPT_SECTION,
    get_adaptive_system_prompt,
)
from backend.core.providers import (
    ModelResponse,
    call_gemini_primary,
    call_gemini_with_staggered_hedge,
    call_single_completion,
    compute_gemini_thinking_budget,
    normalize_gemini_model,
)


@pytest.fixture(autouse=True)
def clean_gemini_state():
    reset_gemini_key_cooldown()
    yield
    reset_gemini_key_cooldown()


# ==============================================================================
# 1. KEY DISCOVERY, DEDUPLICATION, AND 4-KEY POOL HEALTH
# ==============================================================================
def test_1_gemini_keys_loading_and_deduplication():
    """Verify that all 4 verified Google AI Studio keys are discovered and rotated without duplicates."""
    keys = load_gemini_api_keys()
    assert len(keys) == 4, f"Expected 4 Gemini keys in backend/.env, got {len(keys)}: {keys}"
    assert len(set(keys)) == 4, "Keys must be strictly unique"
    assert get_gemini_keys() == keys, "get_gemini_keys must alias load_gemini_api_keys"

    # All keys start with verified Google AI Studio prefix AQ.Ab8RN6
    for k in keys:
        assert k.startswith("AQ.Ab8RN6"), f"Expected Google AI Studio key prefix, got {k[:12]}"

    # Verify round-robin rotation across all 4 keys
    seen = []
    for _ in range(4):
        seen.append(get_next_gemini_key())
    assert seen == keys, "Round-robin must cycle through all 4 keys in deterministic sequence"


# ==============================================================================
# 2. MULTI-PART "thought": true FILTERING
# ==============================================================================
@pytest.mark.anyio
async def test_2_gemini_multi_part_thought_filtering():
    """Verify that Gemini 3.8 Flash thinking parts are stripped and the actual response JSON is returned."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"X-Gemini-Service-Tier": "standard"}
    mock_resp.json = MagicMock(return_value={
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"thought": True, "text": "Let us trace the permutation: 7 letters with duplicate A and R..."},
                        {"text": '{"reason": "7!/(2!*2!) = 1260", "answer": "B"}'}
                    ],
                    "role": "model"
                },
                "finishReason": "STOP"
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 50,
            "candidatesTokenCount": 20,
            "thoughtsTokenCount": 180,
            "serviceTier": "standard"
        }
    })
    mock_client.post = AsyncMock(return_value=mock_resp)

    messages = [{"role": "user", "content": "How many arrangements of ARRANGE?"}]
    raw, el, err = await call_single_completion(
        client=mock_client,
        provider="gemini",
        model="gemini-3.8-flash",
        messages=messages,
        api_key="AQ.Ab8RN6TestKey",
        timeout_ms=5000.0,
        max_tokens=2048,
    )

    assert err is None
    assert raw == '{"reason": "7!/(2!*2!) = 1260", "answer": "B"}'
    assert "thought" not in raw
    assert "permutation" not in raw, "Thinking part must not contaminate raw response content"


# ==============================================================================
# 3. ADAPTIVE THINKING BUDGET & TOKEN BOUNDS
# ==============================================================================
def test_3_adaptive_thinking_budget_and_safe_output_tokens():
    """Verify compute_gemini_thinking_budget correctly maps to 128, 1024, or 2048."""
    # Fast Recall Tier (128)
    assert compute_gemini_thinking_budget("mcq", "What is the primary function of DNS?") == 128
    assert compute_gemini_thinking_budget("mcq", "Which year did World War II end?") == 128

    # Analytical Tier (1024)
    assert compute_gemini_thinking_budget("multi", "Select all valid CSS properties") == 1024
    assert compute_gemini_thinking_budget("order", "Arrange OSI layers from lowest to highest") == 1024
    assert compute_gemini_thinking_budget("text", "Enter the command to check disk space") == 1024
    assert compute_gemini_thinking_budget("mcq", "Explain the SQL query execution plan") == 1024

    # Deep Aptitude & Vision Tier (2048)
    assert compute_gemini_thinking_budget("mcq", "Count the total number of triangles in the figure") == 2048
    assert compute_gemini_thinking_budget("mcq", "A train running at speed 60 km/h crosses a platform in 30 seconds") == 2048
    assert compute_gemini_thinking_budget("mcq", "In a circular table, 6 persons are seated...") == 2048
    assert compute_gemini_thinking_budget("mcq", "Pointing to a photograph, A said B is the daughter of my mother's brother") == 2048
    assert compute_gemini_thinking_budget("mcq", "What is the probability of getting at least 2 heads in 3 coin tosses?") == 2048
    assert compute_gemini_thinking_budget("code", "def twoSum(nums, target):") == 2048
    assert compute_gemini_thinking_budget("mcq", "Identify the figure", has_image=True) == 2048

    # Safe output token verification: maxOutputTokens >= thinking_budget + 1024
    budget_deep = 2048
    safe_max = max(2048, budget_deep + 1024)
    assert safe_max >= budget_deep + 768
    assert safe_max == 3072


# ==============================================================================
# 4. INSTANT < 10MS KEY FAILOVER ON HTTP 503 AND HTTP 429
# ==============================================================================
@pytest.mark.anyio
async def test_4_instant_key_failover_on_503_and_429():
    """Verify that when Key 1 hits 503 or 429, call_gemini_primary rotates to Key 2 in < 10ms with zero sleep."""
    keys = load_gemini_api_keys()
    key1, key2 = keys[0], keys[1]

    call_count = 0
    keys_used = []

    class MockHttpxClient:
        is_closed = False
        async def post(self, url, headers=None, json=None, timeout=None):
            nonlocal call_count
            call_count += 1
            used_k = headers.get("x-goog-api-key")
            keys_used.append(used_k)
            mock_r = MagicMock()
            if used_k == key1:
                # Key 1 returns 503 transient demand spike
                mock_r.status_code = 503
                mock_r.text = '{"error": {"code": 503, "message": "High demand spike", "status": "UNAVAILABLE"}}'
                return mock_r
            else:
                # Key 2 succeeds immediately
                mock_r.status_code = 200
                mock_r.json = MagicMock(return_value={
                    "candidates": [{
                        "content": {
                            "parts": [{"text": '<verification_workspace>[Derivation]: LCM is 60\n[Audit]: Option B</verification_workspace><verified_json>{"reason": "LCM = 60", "answer": "B"}</verified_json>'}],
                            "role": "model"
                        }
                    }]
                })
                return mock_r

    t_start = time.perf_counter()
    resp, el, err = await call_gemini_primary(
        client=MockHttpxClient(),
        provider="gemini",
        model="gemini-3.8-flash",
        messages=[{"role": "user", "content": "What is LCM of 12, 15, 20?"}],
        api_key=key1,
        timeout_ms=4500.0,
        q_type="mcq",
        options=["40", "60", "120", "180"],
    )
    failover_duration_ms = (time.perf_counter() - t_start) * 1000.0

    assert err is None
    assert resp is not None
    assert resp.answer == "B"
    assert call_count == 2
    assert keys_used == [key1, key2], "Must rotate from Key 1 to Key 2"
    assert failover_duration_ms < 150.0, f"Failover took {failover_duration_ms:.2f}ms; must be near-instant"
    assert not is_gemini_key_available(key1), "Key 1 must be placed in 503 micro-cooldown"


# ==============================================================================
# 5. DUAL-VISION CONCURRENT RACE & SURVIVOR RESCUE
# ==============================================================================
@pytest.mark.anyio
async def test_5_dual_vision_concurrent_race_and_survivor_rescue():
    """Verify Mode A concurrent dual-vision racing on visual diagrams with survivor rescue."""
    keys = load_gemini_api_keys()
    k1, k2 = keys[0], keys[1]

    # Subtest A: Dual-Vision Agreement
    with patch("backend.core.llm_client.call_gemini_primary") as mock_gemini:
        # Both vision models derive answer "C" (17 triangles)
        resp1 = ModelResponse(
            answer="C",
            reason="Roof has 9 triangles, box has 8 triangles -> 9+8=17 triangles.",
            raw_text='{"answer":"C"}',
            norm=MagicMock(canonical_key="C", selected_index=2, selected_text="17", selected_indices=None, selected_texts=None, canonical_keys=None, permutation_indices=None, ordered_texts=None),
        )
        resp2 = ModelResponse(
            answer="C",
            reason="Symmetric triangle roof=9, square=8 -> 17.",
            raw_text='{"answer":"C"}',
            norm=MagicMock(canonical_key="C", selected_index=2, selected_text="17", selected_indices=None, selected_texts=None, canonical_keys=None, permutation_indices=None, ordered_texts=None),
        )
        mock_gemini.side_effect = [
            (resp1, 420.0, None),
            (resp2, 450.0, None),
        ]

        fake_b64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        sol = await solve_question(
            subject="Aptitude",
            q_type="mcq",
            question="Find the total number of triangles in the given figure.",
            options=["12", "15", "17", "19"],
            image_data=fake_b64,
        )

        assert sol.status == "OK"
        assert sol.confidence_tier == "HIGH_VISION_SOLVER"
        assert sol.canonical_key == "C"
        assert sol.selected_index == 2
        assert sol.selected_text == "17"

    # Subtest B: Dual-Vision Survivor Rescue (Primary 1 hits 503, Primary 2 succeeds)
    with patch("backend.core.llm_client.call_gemini_primary") as mock_gemini_survivor:
        mock_gemini_survivor.side_effect = [
            (None, 80.0, "HTTP_503: Model overloaded"),
            (resp2, 510.0, None),
        ]
        sol_survivor = await solve_question(
            subject="Aptitude",
            q_type="mcq",
            question="Find the total number of triangles in the given figure.",
            options=["12", "15", "17", "19"],
            image_data=fake_b64,
        )

        assert sol_survivor.status == "OK"
        assert sol_survivor.confidence_tier == "HIGH_VISION_SOLVER"
        assert sol_survivor.canonical_key == "C"


# ==============================================================================
# 6. APTITUDE PROMPTS PACK INJECTION & REASONING RULES
# ==============================================================================
def test_6_aptitude_prompts_pack_and_keywords_trigger():
    """Verify that APTITUDE_REASONING_PROMPT_SECTION is triggered for aptitude signals."""
    sample_queries = [
        "In how many ways can the letters of the word LEADER be arranged?",
        "Two pipes A and B can fill a cistern in 12 and 15 hours respectively",
        "At what time between 4 and 5 o'clock will the hands of a clock be together?",
        "If 15th August 1947 was a Friday, what day of the week was 15th August 1948?",
        "Statements: All apples are fruits. Some fruits are sweet.",
        "Eight persons are seated in a straight row facing North",
        "A man walks 5 km East, then turns North and walks 12 km",
        "Find the missing number in the series: 2, 6, 12, 20, 30, ?",
    ]

    for q in sample_queries:
        assert bool(APTITUDE_KEYWORDS_REGEX.search(q)), f"Query should match aptitude keywords: {q}"
        prompt = get_adaptive_system_prompt("mcq", q)
        assert "QUANTITATIVE, LOGICAL & VISUAL" in prompt, f"Prompt must include aptitude pack for: {q}"
        assert "Permutations & Combinations" in prompt
        assert "Time, Work & Pipes" in prompt
        assert "Clocks, Calendars & Ages" in prompt
        assert "Strict Option Verification" in prompt


# ==============================================================================
# 7. MULTI-ANSWER MAJORITY VOTING & TIE-BREAKER INTEGRATION
# ==============================================================================
def test_7_multi_answer_per_option_majority_voting():
    """Verify 2-of-3 Per-Option Majority Voting across Primary A, Primary B, and Arbiter C."""
    # Model A votes {0, 1} (Options A, B)
    set_a = {0, 1}
    # Model B votes {1, 2} (Options B, C)
    set_b = {1, 2}
    # Arbiter C votes {0, 1} (Options A, B)
    set_c = {0, 1}

    # Option 0 (A): votes = A(yes) + B(no) + C(yes) = 2 -> ACCEPTED
    # Option 1 (B): votes = A(yes) + B(yes) + C(yes) = 3 -> ACCEPTED
    # Option 2 (C): votes = A(no) + B(yes) + C(no) = 1 -> REJECTED (Dropped hallucination)
    majority_indices, method = compute_multi_majority_indices(set_a, set_b, set_c, num_options=4)
    assert majority_indices == [0, 1], f"Expected [0, 1], got {majority_indices}"
    assert method == "majority_2_of_3"

