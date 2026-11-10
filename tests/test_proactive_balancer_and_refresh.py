"""
Targeted tests for:
1. In-place extension refresh & POST /api/reset-state
2. 100% Multi-answer set-invariant consensus & normalization
3. Proactive Zero-429 Groq Rate-Limit Balancer & Single-Primary Survival Fast-Path
"""

import asyncio
import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from backend.app import app
from backend.core.consensus import are_answers_in_consensus
from backend.core.key_pool import (
    GROQ_PRIORITY_BUCKETS,
    is_key_model_quarantined,
    is_model_bucket_healthy,
    record_model_usage,
    record_rate_limit_headers,
    reset_key_model_quarantine,
    reset_key_quarantine,
    select_healthy_groq_pair,
    set_key_model_quarantine,
)
from backend.core.llm_client import solve_question
from backend.core.normalizer import normalize_answer
from backend.core.providers import ModelResponse

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestResetStateEndpoint(unittest.TestCase):
    def setUp(self):
        reset_key_quarantine()
        reset_key_model_quarantine()

    def tearDown(self):
        reset_key_quarantine()
        reset_key_model_quarantine()

    def test_reset_state_api(self):
        client = TestClient(app)

        # Set some quarantine and model state
        set_key_model_quarantine("gsk_testkey", "openai/gpt-oss-120b", 30.0)
        self.assertTrue(is_key_model_quarantined("gsk_testkey", "openai/gpt-oss-120b"))

        # Call POST /api/reset-state
        resp = client.post("/api/reset-state")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("state_reset"))
        self.assertFalse(is_key_model_quarantined("gsk_testkey", "openai/gpt-oss-120b"))

        # Also test /reset-state alias
        set_key_model_quarantine("gsk_testkey2", "qwen/qwen3.8-27b", 30.0)
        resp2 = client.post("/reset-state")
        self.assertEqual(resp2.status_code, 200)
        self.assertFalse(is_key_model_quarantined("gsk_testkey2", "qwen/qwen3.8-27b"))


class TestMultiAnswerSetInvariance(unittest.IsolatedAsyncioTestCase):
    def test_normalize_multi_mcq_order_invariance(self):
        options = ["Python", "JavaScript", "C++", "Java"]

        norm1 = normalize_answer("multi", "A, C", options)
        norm2 = normalize_answer("multi", "C, A", options)
        norm3 = normalize_answer("multi", ["C", "A"], options)

        self.assertEqual(norm1.canonical_keys, ["A", "C"])
        self.assertEqual(norm2.canonical_keys, ["A", "C"])
        self.assertEqual(norm3.canonical_keys, ["A", "C"])
        self.assertEqual(norm1.canonical_key.replace(" ", ""), "A,C")
        self.assertEqual(norm2.canonical_key.replace(" ", ""), "A,C")
        self.assertEqual(norm3.canonical_key.replace(" ", ""), "A,C")
        self.assertEqual(norm1.selected_indices, [0, 2])
        self.assertEqual(norm2.selected_indices, [0, 2])
        self.assertEqual(norm3.selected_indices, [0, 2])
        self.assertEqual(norm1.selected_texts, ["Python", "C++"])

        # Test consensus function directly
        self.assertTrue(are_answers_in_consensus(norm1, norm2, "multi"))
        self.assertTrue(are_answers_in_consensus(norm2, norm3, "multi"))

    async def test_solve_question_multi_answer_consensus(self):
        options = ["Alpha", "Beta", "Gamma", "Delta"]

        with patch("backend.core.llm_client.call_groq_primary") as mock_groq_pri:
            mock_groq_pri.side_effect = [
                (ModelResponse(answer="A, C", reason="A and C are correct."), 120.0, None),
                (ModelResponse(answer="C, A", reason="Gamma and Alpha are true."), 140.0, None),
            ]

            res = await solve_question(
                subject="General",
                q_type="multi",
                question="Which of the following are primary components?",
                options=options,
                bypass_cache=True,
            )

            self.assertEqual(res.status, "OK")
            self.assertEqual(res.confidence_tier, "HIGH_BOTH_AGREE")
            self.assertEqual(res.canonical_keys, ["A", "C"])
            self.assertEqual(res.selected_indices, [0, 2])


class TestProactiveGroqRateLimitBalancer(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_key_quarantine()
        reset_key_model_quarantine()

    def tearDown(self):
        reset_key_quarantine()
        reset_key_model_quarantine()

    def test_qwen_18s_spacing(self):
        key = "gsk_test_qwen_spacing"
        model = "qwen/qwen3.8-27b"

        self.assertTrue(is_model_bucket_healthy("groq", key, model))

        record_model_usage("groq", key, model)
        # Should now be unhealthy due to 18.0s spacing guard
        self.assertFalse(is_model_bucket_healthy("groq", key, model))

    def test_remaining_tokens_header_rotation(self):
        key = "gsk_test_header_rotation"
        model = "openai/gpt-oss-120b"

        # Healthy initially
        self.assertTrue(is_model_bucket_healthy("groq", key, model))

        # Simulate response header with remaining tokens < 450
        record_rate_limit_headers(
            "groq", key, model,
            {"x-ratelimit-remaining-tokens": "380", "x-ratelimit-remaining-requests": "10"}
        )

        self.assertFalse(is_model_bucket_healthy("groq", key, model))

        # Test proactive selection rotates to healthy pair
        pri_a, pri_b = select_healthy_groq_pair(key)
        self.assertNotEqual(pri_a["model"], model)
        self.assertEqual(pri_a["model"], "llama-3.3-70b-versatile")

    async def test_single_primary_survival_fast_path_on_429(self):
        options = ["Paris", "London", "Berlin", "Rome"]

        with patch("backend.core.llm_client.call_groq_primary") as mock_groq_pri, \
             patch("backend.core.llm_client.run_groq_arbiter_fallback") as mock_arb, \
             patch("backend.core.llm_client.run_hedged_openrouter_fallback") as mock_or:

            # Model A succeeds, Model B hits 429
            mock_groq_pri.side_effect = [
                (ModelResponse(answer="A", reason="Paris is the capital of France."), 110.0, None),
                (None, 45.0, "HTTP 429 Too Many Requests (Rate limit exceeded)"),
            ]

            res = await solve_question(
                subject="Geography",
                q_type="mcq",
                question="What is the capital of France?",
                options=options,
                bypass_cache=True,
            )

            # Verifies survival rescue returned immediately as MEDIUM_TIEBREAKER_AGREE
            self.assertEqual(res.status, "OK")
            self.assertEqual(res.confidence_tier, "MEDIUM_TIEBREAKER_AGREE")
            self.assertEqual(res.canonical_key, "A")
            self.assertEqual(res.selected_index, 0)

            # Neither arbiter nor OpenRouter fallback was called
            mock_arb.assert_not_called()
            mock_or.assert_not_called()

    async def test_dual_429_races_unquarantined_groq_bucket(self):
        options = ["Earth", "Mars", "Jupiter", "Saturn"]

        with patch("backend.core.llm_client.call_groq_primary") as mock_groq_pri, \
             patch("backend.core.llm_client.run_groq_arbiter_fallback") as mock_arb, \
             patch("backend.core.llm_client.run_hedged_openrouter_fallback") as mock_or:

            # Both primaries fail with 429
            mock_groq_pri.side_effect = [
                (None, 30.0, "HTTP 429 Too Many Requests"),
                (None, 35.0, "HTTP 429 Rate Limit Exceeded"),
            ]

            from backend.core.consensus import SolverResponse
            mock_arb.return_value = (
                SolverResponse(
                    status="OK",
                    confidence_tier="MEDIUM_TIEBREAKER_AGREE",
                    canonical_key="B",
                    selected_index=1,
                    selected_text="Mars",
                    reason="Arbiter derivation on unquarantined bucket.",
                ),
                ["groq_arbiter:llama-3.3-70b-versatile"],
                {"canonical_key": "B"}
            )
            mock_or.return_value = (None, [], {})

            res = await solve_question(
                subject="Astronomy",
                q_type="mcq",
                question="Which planet is known as the Red Planet?",
                options=options,
                bypass_cache=True,
            )

            self.assertEqual(res.status, "OK")
            self.assertEqual(res.canonical_key, "B")
            self.assertEqual(res.selected_index, 1)


class TestExtensionSoftRefreshNodeContract(unittest.TestCase):
    def test_extension_alt_r_keybinding_syntax(self):
        hud_js = (PROJECT_ROOT / "extension" / "floating_hud.js").read_text(encoding="utf-8")
        self.assertIn("window.__QUIZZY_SOFT_REFRESH_EXTENSION__", hud_js)
        self.assertIn("#06b6d4", hud_js)  # Cyan dot flash
        self.assertIn("Refreshed", hud_js)
        self.assertIn("KeyR", hud_js)

        bg_js = (PROJECT_ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        self.assertIn("QUIZZY_SOFT_REFRESH_EXTENSION", bg_js)
        self.assertIn("/api/reset-state", bg_js)


class TestSixGroqKeyOptimization(unittest.TestCase):
    def setUp(self):
        reset_key_quarantine()
        reset_key_model_quarantine()

    def tearDown(self):
        reset_key_quarantine()
        reset_key_model_quarantine()

    def test_load_all_six_groq_keys(self):
        from backend.core.key_pool import get_healthy_groq_keys, load_groq_api_keys
        keys = load_groq_api_keys()
        self.assertGreaterEqual(len(keys), 6, f"Expected at least 6 Groq keys loaded, got {len(keys)}")
        healthy = get_healthy_groq_keys()
        self.assertGreaterEqual(len(healthy), 6)

    def test_round_robin_distinct_key_allocation(self):
        from backend.core.key_pool import get_next_groq_key
        k1 = get_next_groq_key()
        k2 = get_next_groq_key()
        k3 = get_next_groq_key()
        self.assertTrue(k1 and k2 and k3)
        # Verify distinct key exclusion
        k_alt = get_next_groq_key(exclude=k1)
        self.assertNotEqual(k1, k_alt)

    def test_key_model_quarantine_does_not_block_healthy_keys(self):
        from backend.core.key_pool import (
            get_next_groq_key,
            is_key_model_quarantined,
            is_model_in_cooldown,
            load_groq_api_keys,
            set_key_model_quarantine,
        )
        keys = load_groq_api_keys()
        self.assertGreaterEqual(len(keys), 2)
        key1, key2 = keys[0], keys[1]
        model = "openai/gpt-oss-120b"

        # Key 1 hits 429 on 120b
        set_key_model_quarantine(key1, model, 30.0)

        # Key 1 is quarantined for 120b
        self.assertTrue(is_key_model_quarantined(key1, model))
        # Key 2 is NOT quarantined for 120b
        self.assertFalse(is_key_model_quarantined(key2, model))
        # Global model cooldown is NOT set because other healthy keys exist!
        self.assertFalse(is_model_in_cooldown("groq", model))

        # get_next_groq_key(model=120b) automatically skips Key 1
        for _ in range(len(keys)):
            picked = get_next_groq_key(model=model)
            self.assertNotEqual(picked, key1, "Quarantined key for this model must be bypassed!")

    def test_all_keys_exhausted_triggers_global_model_cooldown(self):
        from backend.core.key_pool import (
            is_model_in_cooldown,
            load_groq_api_keys,
            set_key_model_quarantine,
        )
        keys = load_groq_api_keys()
        model = "openai/gpt-oss-120b"

        # Quarantine all keys for this model
        for k in keys:
            set_key_model_quarantine(k, model, 30.0)

        # Now global model cooldown MUST be active
        self.assertTrue(is_model_in_cooldown("groq", model))

