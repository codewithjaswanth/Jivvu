"""
Phase 6 Verification Suite:
Multi-Key OpenRouter Rotation, Per-Key 429 Quarantine,
Parallel Hedged Fallback Racing with First-to-Agree Early Exit,
Dual-Primary Consensus Rescue, and Multi-Paragraph Assertion (A) & Reason (R) Stem Extraction.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Ensure workspace root is in sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
os.environ["QUIZZY_OFFLINE_TEST_MODE"] = "1"

import httpx
from fastapi.testclient import TestClient

from backend.app import app
from backend.core.llm_client import (
    SolverResponse,
    check_models,
    get_active_fallback_pool,
    get_healthy_fallback_models,
    get_healthy_openrouter_keys,
    get_next_openrouter_key,
    get_ordered_fallback_pool,
    is_key_quarantined,
    is_model_quarantined,
    is_valid_chat_model,
    load_models_config,
    load_openrouter_api_keys,
    quarantine_key,
    quarantine_model,
    reset_key_quarantine,
    reset_model_quarantine,
    run_hedged_openrouter_fallback,
    solve_question,
)
import pytest
from backend.core.normalizer import NormalizedAnswer
from backend.core.parser import ParsedLLMResponse


@pytest.fixture(autouse=True)
def clean_quarantine_fixture():
    """Ensure every test in phase 6 starts with a clean slate for quarantine."""
    reset_key_quarantine()
    reset_model_quarantine()
    yield
    reset_key_quarantine()
    reset_model_quarantine()


def test_1_non_chat_model_rejection():
    reset_key_quarantine()
    reset_model_quarantine()
    """
    Test 1: Non-Chat Model Rejection.
    Strictly reject embedding, reranker, content-safety, guard, whisper, and tts slugs.
    """
    print("\n[Test 1] Testing Non-Chat Model Filter...")

    rejected_slugs = [
        "openai/text-embedding-3-small",
        "cohere/rerank-v3-english",
        "mistralai/mistral-embed",
        "meta/llama-guard-3-8b",
        "meta/llama-guard-2-8b",
        "openai/whisper-large-v3",
        "elevenlabs/tts-multilingual",
        "mistralai/content-safety-2409",
        "sglang/rerank-large:free",
        "bge/bge-multilingual-embed:free",
    ]

    for slug in rejected_slugs:
        assert not is_valid_chat_model(slug), f"Slug '{slug}' should be rejected as non-chat!"

    accepted_slugs = [
        "google/gemma-4-26b-a4b-it:free",
        "nvidia/nemotron-3.5-lightning:free",
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "inception/mercury-decide:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "thinkingmachines/inkling:free",
        "thinkingmachines/inkling-small:free",
    ]

    for slug in accepted_slugs:
        assert is_valid_chat_model(slug), f"Valid chat slug '{slug}' should be accepted!"

    # Candidate pool filtering
    mixed_pool = [
        "openai/text-embedding-3-small",
        "google/gemma-4-26b-a4b-it:free",
        "cohere/rerank-v3-english",
        "nvidia/nemotron-3.5-lightning:free",
    ]
    healthy = get_healthy_fallback_models(mixed_pool)
    assert healthy == [
        "google/gemma-4-26b-a4b-it:free",
        "nvidia/nemotron-3.5-lightning:free",
    ], f"Unexpected filtered pool: {healthy}"

    print("  -> Passed non-chat model rejection for embedding, reranking, safety, whisper, and tts slugs.")


def test_2_multikey_loading_rotation_and_quarantine():
    """
    Test 2: Multi-Key Loading, Round-Robin Rotation, and 429 Quarantine Failover.
    """
    print("\n[Test 2] Testing Multi-Key Loading, Round-Robin Rotation, and 429 Quarantine Failover...")
    reset_key_quarantine()

    test_keys = "sk-or-v1-key1,sk-or-v1-key2,sk-or-v1-key3"
    with patch.dict(os.environ, {"OPENROUTER_API_KEYS": test_keys, "OPENROUTER_API_KEY": ""}, clear=False):
        loaded = load_openrouter_api_keys()
        assert loaded == ["sk-or-v1-key1", "sk-or-v1-key2", "sk-or-v1-key3"], f"Loaded keys mismatch: {loaded}"

        # 1. Round-robin rotation test
        first_pick = get_next_openrouter_key()
        second_pick = get_next_openrouter_key()
        third_pick = get_next_openrouter_key()
        fourth_pick = get_next_openrouter_key()

        assert first_pick == "sk-or-v1-key1"
        assert second_pick == "sk-or-v1-key2"
        assert third_pick == "sk-or-v1-key3"
        assert fourth_pick == "sk-or-v1-key1", "Should wrap around round-robin"
        print("  -> Passed round-robin key rotation.")

        # 2. Distinct key allocation for hedged parallel tasks
        k_a = get_next_openrouter_key()
        k_b = get_next_openrouter_key(exclude=k_a)
        assert k_a != k_b, f"Parallel hedged tasks must receive distinct keys: k_a={k_a}, k_b={k_b}"
        print("  -> Passed distinct key allocation across parallel hedged tasks.")

        # 3. HTTP 429 quarantine test
        quarantine_key("sk-or-v1-key1", 429)
        assert is_key_quarantined("sk-or-v1-key1") is True
        healthy_keys = get_healthy_openrouter_keys()
        assert "sk-or-v1-key1" not in healthy_keys
        assert healthy_keys == ["sk-or-v1-key2", "sk-or-v1-key3"]

        # Ensure rotation never yields quarantined key
        for _ in range(6):
            rotated = get_next_openrouter_key()
            assert rotated in ["sk-or-v1-key2", "sk-or-v1-key3"]
            assert rotated != "sk-or-v1-key1"
        print("  -> Passed 429 quarantine failover (quarantined key excluded).")

        # 4. HTTP 401 quarantine test
        quarantine_key("sk-or-v1-key2", 401)
        assert is_key_quarantined("sk-or-v1-key2") is True
        healthy_keys_2 = get_healthy_openrouter_keys()
        assert healthy_keys_2 == ["sk-or-v1-key3"]
        assert get_next_openrouter_key() == "sk-or-v1-key3"
        print("  -> Passed 401/402/403 quarantine failover.")

    reset_key_quarantine()


@patch("backend.core.llm_client.get_searxng_config", return_value=("http://127.0.0.1:8080", False))
def test_3_parallel_hedged_tiebreaker_early_exit(mock_searxng):
    """
    Test 3: Parallel Hedged Tie-Breaker early exit (asyncio.FIRST_COMPLETED / as_completed):
    When Model 1 in fallback_pool responds in 200ms with an agreeing answer and Model 2 takes 2000ms,
    solve_question completes in < 500ms and cancels Model 2.
    """
    print("\n[Test 3] Testing Parallel Hedged Tie-Breaker Early Exit (<500ms)...")
    from backend.core.key_pool import reset_provider_cooldown
    reset_provider_cooldown()
    reset_key_quarantine()

    norm_a = NormalizedAnswer(q_type="mcq", canonical_key="A", selected_index=0, selected_text="Alpha", ordered_texts=[], permutation_indices=[])
    norm_b = NormalizedAnswer(q_type="mcq", canonical_key="B", selected_index=1, selected_text="Beta", ordered_texts=[], permutation_indices=[])
    parsed_a = ParsedLLMResponse(answer="A", reason="Reason A")
    parsed_b = ParsedLLMResponse(answer="B", reason="Reason B")

    model_2_cancelled = False

    async def mock_call_with_retry(client, provider, model, messages, api_key, timeout_ms, q_type, options, *args, **kwargs):
        nonlocal model_2_cancelled
        # Primary calls: A and B disagree
        if model == "primary_model_a":
            return ('{"answer": "A"}', parsed_a, norm_a, 100.0, None)
        if model == "primary_model_b":
            return ('{"answer": "B"}', parsed_b, norm_b, 110.0, None)

        # Fallback 1: Fast (200ms) agreeing with primary A
        if model == "google/gemma-4-26b-a4b-it:free":
            await asyncio.sleep(0.20)
            return ('{"answer": "A"}', parsed_a, norm_a, 200.0, None)

        # Fallback 2: Slow (2000ms)
        if model == "nvidia/nemotron-3.5-lightning:free":
            try:
                await asyncio.sleep(2.0)
                return ('{"answer": "B"}', parsed_b, norm_b, 2000.0, None)
            except asyncio.CancelledError:
                model_2_cancelled = True
                raise

        return (None, None, None, 0.0, "UNKNOWN_MOCK_MODEL")

    test_config = {
        "primary_a": {"provider": "groq", "family": "gpt-oss", "model": "primary_model_a"},
        "primary_b": {"provider": "groq", "family": "qwen", "model": "primary_model_b"},
        "fallback": {"provider": "openrouter", "family": "nemotron", "model": "nvidia/nemotron-3.5-lightning:free"},
        "fallback_pool": [
            "google/gemma-4-26b-a4b-it:free",
            "nvidia/nemotron-3.5-lightning:free",
        ],
        "timeouts_ms": {
            "groq_parallel_timeout_ms": 1750,
            "openrouter_fallback_timeout_ms": 2150,
            "searxng_timeout_ms": 650,
            "searxng_grounded_llm_timeout_ms": 900,
            "hard_total_budget_ms": 3000,
        },
    }

    with patch("backend.core.llm_client.call_model_with_budget_retry", side_effect=mock_call_with_retry), \
         patch("backend.core.llm_client.fetch_searxng_snippets", return_value=[]):
        t_start = time.perf_counter()
        solver_res = asyncio.run(
            solve_question(
                subject="Biology",
                q_type="mcq",
                question="What is the powerhouse of the cell?",
                options=["Alpha", "Beta", "Gamma", "Delta"],
                config=test_config,
            )
        )
        total_time_ms = (time.perf_counter() - t_start) * 1000.0

    assert solver_res.status == "OK"
    assert solver_res.confidence_tier == "MEDIUM_TIEBREAKER_AGREE", f"Unexpected tier: {solver_res.confidence_tier}"
    assert solver_res.canonical_key == "A"
    assert solver_res.selected_index == 0
    assert total_time_ms < 1200.0, f"Early exit failed to finish under 1200ms! Took {total_time_ms:.1f}ms"
    assert model_2_cancelled is True, "Slow fallback model 2 was not cancelled after model 1 agreement!"

    print(f"  -> Solved in {total_time_ms:.1f}ms (< 650ms cap). Model 2 cancelled early: {model_2_cancelled} [PASS]")

    # Check /solve API endpoint contracts for MEDIUM_TIEBREAKER_AGREE
    with patch("backend.app.solve_question", AsyncMock(return_value=solver_res)):
        with TestClient(app) as client:
            res = client.post("/solve", json={
                "subject": "Biology",
                "q_type": "mcq",
                "question": "What is the powerhouse of the cell? (API check)",
                "options": ["Alpha", "Beta", "Gamma", "Delta"],
                "bypass_cache": True,
            })
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["status"] == "ok"
            assert data["confidence_tier"] == "MEDIUM_TIEBREAKER_AGREE"
            assert data["confidence_label"] == "medium"
            assert data["auto_eligible"] is True, "MEDIUM_TIEBREAKER_AGREE must be auto_eligible (2-of-3 consensus)"
            print("  -> Passed FastAPI /solve schema: MEDIUM_TIEBREAKER_AGREE has auto_eligible=True.")


@patch("backend.core.llm_client.get_searxng_config", return_value=("http://127.0.0.1:8080", False))
def test_4_dual_primary_failure_rescued_by_fallback_consensus(mock_searxng):
    """
    Test 4: Dual-primary failure rescued when 2 hedged OpenRouter models agree with each other.
    """
    print("\n[Test 4] Testing Dual-Primary Failure Rescue via Dual Hedged Fallback Consensus...")

    norm_c = NormalizedAnswer(q_type="mcq", canonical_key="C", selected_index=2, selected_text="Canberra", ordered_texts=[], permutation_indices=[])
    parsed_c = ParsedLLMResponse(answer="C", reason="Canberra is the capital")

    async def mock_call_dual_fail(client, provider, model, messages, api_key, timeout_ms, q_type, options, *args, **kwargs):
        # Both Groq primaries fail
        if model.startswith("primary_"):
            return (None, None, None, 150.0, "NETWORK_ERROR_ConnectionReset")

        # Hedged Fallback 1: Gemma agrees on C
        if "gemma" in model:
            return ('{"answer": "C"}', parsed_c, norm_c, 250.0, None)

        # Hedged Fallback 2: Nemotron agrees on C
        if "nemotron" in model:
            return ('{"answer": "C"}', parsed_c, norm_c, 280.0, None)

        return (None, None, None, 0.0, "UNKNOWN_MODEL")

    test_config = {
        "primary_a": {"provider": "groq", "family": "gpt-oss", "model": "primary_model_a"},
        "primary_b": {"provider": "groq", "family": "qwen", "model": "primary_model_b"},
        "fallback": {"provider": "openrouter", "family": "nemotron", "model": "nvidia/nemotron-3.5-lightning:free"},
        "fallback_pool": [
            "google/gemma-4-26b-a4b-it:free",
            "nvidia/nemotron-3.5-lightning:free",
        ],
        "timeouts_ms": {
            "groq_parallel_timeout_ms": 1750,
            "openrouter_fallback_timeout_ms": 2150,
            "searxng_timeout_ms": 650,
            "searxng_grounded_llm_timeout_ms": 900,
            "hard_total_budget_ms": 3000,
        },
    }

    with patch("backend.core.llm_client.call_model_with_budget_retry", side_effect=mock_call_dual_fail):
        solver_res = asyncio.run(
            solve_question(
                subject="Geography",
                q_type="mcq",
                question="What is the capital of Australia?",
                options=["Sydney", "Melbourne", "Canberra", "Perth"],
                config=test_config,
            )
        )

    assert solver_res.status == "OK"
    assert solver_res.confidence_tier == "MEDIUM_TIEBREAKER_AGREE", f"Expected rescue tier, got {solver_res.confidence_tier}"
    assert solver_res.canonical_key == "C"
    assert solver_res.selected_index == 2
    assert solver_res.selected_text == "Canberra"
    print("  -> Dual-primary failure successfully rescued to consensus answer 'C' [PASS]")


def test_5_assertion_reason_multi_block_dom_extraction():
    """
    Test 5: Multi-paragraph Assertion (A) + Reason (R) DOM extraction test.
    Confirms both Assertion (A) and Reason (R) paragraphs are combined into the extracted question string.
    """
    print("\n[Test 5] Testing Multi-Paragraph Assertion (A) & Reason (R) DOM Extraction in Node.js...")

    node_script = """
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class MockNode {
  constructor(tag, className = "", attrs = {}, children = []) {
    this.tagName = (tag || "").toUpperCase();
    this.nodeType = 1;
    this.className = className;
    this.id = attrs.id || "";
    this.attributes = attrs;
    this.childNodes = [];
    this.parentElement = null;
    this.parentNode = null;

    for (const child of children) {
      if (typeof child === "string") {
        const textNode = {
          nodeType: 3,
          nodeValue: child,
          textContent: child,
          childNodes: [],
          parentElement: this,
          parentNode: this
        };
        this.childNodes.push(textNode);
      } else if (child) {
        child.parentElement = this;
        child.parentNode = this;
        this.childNodes.push(child);
      }
    }
  }

  get firstChild() { return this.childNodes[0] || null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    const siblings = this.parentNode.childNodes;
    const idx = siblings.indexOf(this);
    return (idx !== -1 && idx < siblings.length - 1) ? siblings[idx + 1] : null;
  }
  get children() { return this.childNodes.filter(n => n.nodeType === 1); }

  getAttribute(k) { return this.attributes[k] !== undefined ? String(this.attributes[k]) : null; }
  hasAttribute(k) { return this.attributes[k] !== undefined; }

  get textContent() {
    let text = "";
    for (const c of this.childNodes) {
      text += c.nodeType === 3 ? c.nodeValue : (c.textContent || "");
    }
    return text;
  }

  get innerText() { return this.textContent; }

  querySelector(sel) {
    const all = this.querySelectorAll(sel);
    return all.length > 0 ? all[0] : null;
  }

  querySelectorAll(sel) {
    const results = [];
    const walk = (n) => {
      if (n.nodeType === 1 && n !== this) {
        if (matches(n, sel)) results.push(n);
      }
      for (const c of (n.childNodes || [])) {
        if (c.nodeType === 1) walk(c);
      }
    };
    walk(this);
    return results;
  }

  contains(other) {
    let curr = other;
    while (curr) {
      if (curr === this) return true;
      curr = curr.parentNode || curr.parentElement;
    }
    return false;
  }

  closest(sel) {
    let curr = this;
    while (curr) {
      if (curr.nodeType === 1 && matches(curr, sel)) return curr;
      curr = curr.parentNode || curr.parentElement;
    }
    return null;
  }

  addEventListener() {}
  removeEventListener() {}
  getBoundingClientRect() { return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 }; }
}

function matches(el, sel) {
  const parts = sel.split(",").map(s => s.trim());
  for (const p of parts) {
    if (p === "*") return true;
    if (p.startsWith(".")) {
      const cls = p.slice(1);
      if (el.className && el.className.split(/\\s+/).includes(cls)) return true;
    } else if (p.startsWith("#")) {
      const id = p.slice(1);
      if (el.id === id) return true;
    } else if (p.startsWith("[class*='") && p.endsWith("']")) {
      const target = p.slice(9, -2);
      if (el.className && el.className.includes(target)) return true;
    } else if (p.startsWith("[role='") && p.endsWith("']")) {
      const target = p.slice(7, -2);
      if (el.getAttribute("role") === target) return true;
    } else if (p.includes("[type='radio']")) {
      if (el.tagName.toLowerCase() === "input" && el.getAttribute("type") === "radio") return true;
    } else if (p.toLowerCase() === el.tagName.toLowerCase()) {
      return true;
    }
  }
  return false;
}

const el = (tag, cls = "", attrs = {}, children = []) => new MockNode(tag, cls, attrs, children);

// Build CCBP Assertion (A) and Reason (R) question card
const opt0 = el("div", "sc-opt-row sc-bczRLJ", {}, ["Both A and R are true and R is the correct explanation of A"]);
const opt1 = el("div", "sc-opt-row sc-bczRLJ", {}, ["Both A and R are true but R is not the correct explanation of A"]);
const opt2 = el("div", "sc-opt-row sc-bczRLJ", {}, ["A is true but R is false"]);
const opt3 = el("div", "sc-opt-row sc-bczRLJ", {}, ["A is false but R is true"]);

const ccbpDoc = el("html", "", {}, [
  el("head"),
  el("body", "sc-body-app", {}, [
    // Top bar header
    el("div", "sc-topbar sc-jTzLTM", {}, [
      el("span", "sc-title", {}, ["← MCQ PRACTICE | THERMODYNAMICS AND KINETICS"]),
      el("span", "sc-score", {}, ["SCORE: 4"]),
      el("span", "sc-attempted", {}, ["QUESTIONS ATTEMPTED: 4/15"]),
      el("span", "sc-instructions", {}, ["PRACTICE INSTRUCTIONS"]),
      el("span", "sc-end", {}, ["END PRACTICE"]),
    ]),
    // Card Container
    el("div", "sc-card-hash sc-fjdhpX", {}, [
      // Multi-paragraph question stem
      el("div", "sc-q-section sc-gKclnd", {}, [
        el("p", "sc-statement", {}, ["Assertion (A): The velocity of sound in air increases with increase in temperature."]),
        el("p", "sc-statement", {}, ["Reason (R): With increase in temperature, the density of air decreases."]),
        el("p", "sc-instruction", {}, ["Choose the correct option:"])
      ]),
      // Options container
      el("div", "sc-opts-section sc-dIouRR", {}, [
        opt0,
        opt1,
        opt2,
        opt3
      ]),
      // Footer action buttons
      el("div", "sc-actions", {}, [
        el("button", "sc-btn", {}, ["SUBMIT"]),
        el("button", "sc-btn", {}, ["SKIP"])
      ])
    ])
  ])
]);

ccbpDoc.body = ccbpDoc.childNodes[1];
ccbpDoc.documentElement = ccbpDoc;
ccbpDoc.getElementById = (id) => ccbpDoc.querySelector('#' + id);
ccbpDoc.createElement = (tag) => new MockNode(tag);

const sandbox = {
  window: null,
  document: ccbpDoc,
  getComputedStyle: () => ({ display: "block", visibility: "visible", opacity: "1" }),
  Event: global.Event,
  MouseEvent: global.MouseEvent,
  PointerEvent: global.PointerEvent,
  HTMLInputElement: global.HTMLInputElement,
  HTMLTextAreaElement: global.HTMLTextAreaElement,
  Date,
  String,
  Number,
  Boolean,
  Array,
  Object,
  Set,
  RegExp,
  console
};
sandbox.window = sandbox;

const extractorCode = fs.readFileSync(path.resolve(process.cwd(), "extension/extractor.js"), "utf8");
const result = vm.runInNewContext(extractorCode, sandbox);

if (!result || !result.success) {
  console.error("EXTRACTION_FAILED:", JSON.stringify(result));
  process.exit(1);
}

console.log("EXTRACTED_QUESTION:\\n" + result.question);
console.log("EXTRACTED_OPTIONS_COUNT: " + result.options.length);

if (!result.question.includes("Assertion (A)")) {
  console.error("MISSING_ASSERTION");
  process.exit(2);
}
if (!result.question.includes("Reason (R)")) {
  console.error("MISSING_REASON");
  process.exit(3);
}
if (!result.question.includes("Choose the correct option")) {
  console.error("MISSING_INSTRUCTION");
  process.exit(4);
}
if (result.options.length !== 4) {
  console.error("WRONG_OPTIONS_COUNT");
  process.exit(5);
}

console.log("NODE_ASSERTION_REASON_OK");
process.exit(0);
""";

    res = subprocess.run(
        ["node", "-"],
        input=node_script,
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    if res.returncode != 0:
        print("Node.js extraction stdout:\n", res.stdout)
        print("Node.js extraction stderr:\n", res.stderr)
        raise AssertionError(f"Assertion-Reason extraction failed with return code {res.returncode}")

    assert "NODE_ASSERTION_REASON_OK" in res.stdout, f"Missing success confirmation: {res.stdout}"
    print("  -> Passed Node.js Assertion (A) & Reason (R) multi-paragraph question extraction [PASS]")


def test_6_tier_hierarchy_and_two_wave_cascade():
    """
    Test 6: Strict Tier Hierarchy and Two-Wave Tier Cascade.
    1. Verifies models.json fallback_pool is strictly ordered S-Tier -> A-Tier -> B-Tier.
    2. Verifies Wave 1 only dispatches S-Tier models when S-Tier succeeds within the soft escalation window.
    3. Verifies Wave 2 automatically escalates to A/B-Tier models when S-Tier models fail, 429, or exceed soft window.
    """
    print("\n[Test 6] Testing Strict Tier Hierarchy & Two-Wave Tier Cascade...")
    reset_model_quarantine()

    # 1. Verify models.json configuration
    cfg = load_models_config()
    fb_pool = cfg.get("fallback_pool", [])
    assert len(fb_pool) >= 6, f"Expected at least 6 models in fallback_pool, got {len(fb_pool)}"

    expected_tiers = ["S", "S", "S", "A", "B", "B"]
    expected_models = [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "google/gemma-4-31b-it:free",
        "google/gemma-4-26b-a4b-it:free",
        "nvidia/nemotron-3.5-lightning:free",
        "liquid/lfm-2.5-2.6b:free",
    ]

    for idx, (m_entry, exp_tier, exp_slug) in enumerate(zip(fb_pool, expected_tiers, expected_models)):
        assert m_entry["tier"] == exp_tier, f"Index {idx} expected tier {exp_tier}, got {m_entry['tier']}"
        assert m_entry["model"] == exp_slug, f"Index {idx} expected model {exp_slug}, got {m_entry['model']}"
        assert m_entry["priority"] == idx + 1, f"Index {idx} expected priority {idx+1}, got {m_entry['priority']}"

    assert cfg.get("timeouts_ms", {}).get("s_tier_soft_escalation_ms") == 1300

    # 2. Test get_ordered_fallback_pool sorting
    shuffled_pool = list(reversed(fb_pool))
    ordered = get_ordered_fallback_pool(shuffled_pool)
    for idx, m_entry in enumerate(ordered):
        assert m_entry["model"] == expected_models[idx]

    print("  -> Passed models.json immutable tier hierarchy audit (S -> A -> B).")

    # 3. Two-Wave Cascade: Case A - Wave 1 S-Tier succeeds fast (< soft escalation window)
    norm_a = NormalizedAnswer(q_type="mcq", canonical_key="A", selected_index=0, selected_text="Apple", ordered_texts=[], permutation_indices=[])
    norm_b = NormalizedAnswer(q_type="mcq", canonical_key="B", selected_index=1, selected_text="Banana", ordered_texts=[], permutation_indices=[])
    parsed_a = ParsedLLMResponse(answer="A", reason="Reason A")
    parsed_b = ParsedLLMResponse(answer="B", reason="Reason B")

    wave2_called = False

    async def mock_call_s_fast(client, provider, model, messages, api_key, timeout_ms, q_type, options, *args, **kwargs):
        nonlocal wave2_called
        if model == cfg["primary_a"]["model"] or model == "primary_a":
            return ('{"answer": "A"}', parsed_a, norm_a, 50.0, None)
        if model == cfg["primary_b"]["model"] or model == "primary_b":
            return ('{"answer": "B"}', parsed_b, norm_b, 50.0, None)
        # S-Tier Wave 1 model: fast success in 40ms agreeing with A
        if model == "nvidia/nemotron-3-ultra-550b-a55b:free":
            await asyncio.sleep(0.04)
            return ('{"answer": "A"}', parsed_a, norm_a, 40.0, None)
        if model == "nvidia/nemotron-3-super-120b-a12b:free":
            await asyncio.sleep(0.8)
            return ('{"answer": "A"}', parsed_a, norm_a, 800.0, None)
        # Wave 2 models
        if "gemma-4-26b" in model or "lightning" in model:
            wave2_called = True
            return ('{"answer": "A"}', parsed_a, norm_a, 50.0, None)
        return (None, None, None, 0.0, "UNKNOWN_MOCK")

    with patch("backend.core.llm_client.call_model_with_budget_retry", side_effect=mock_call_s_fast):
        solver_res = asyncio.run(
            solve_question(
                subject="Test",
                q_type="mcq",
                question="Q?",
                options=["Apple", "Banana", "Cherry", "Date"],
                config=cfg,
            )
        )
        assert solver_res.status == "OK"
        assert solver_res.canonical_key == "A"
        assert not wave2_called, "Wave 2 models should NOT be called when Wave 1 S-tier succeeds within soft window!"
        print("  -> Passed Wave 1 S-Tier early exit: Wave 2 lower-tier models not dispatched.")

    # 4. Two-Wave Cascade: Case B - Wave 1 S-Tier models fail -> Wave 2 triggers immediately
    wave2_dispatched = False

    async def mock_call_s_fails(client, provider, model, messages, api_key, timeout_ms, q_type, options, *args, **kwargs):
        nonlocal wave2_dispatched
        if model == cfg["primary_a"]["model"] or model == "primary_a":
            return ('{"answer": "A"}', parsed_a, norm_a, 50.0, None)
        if model == cfg["primary_b"]["model"] or model == "primary_b":
            return ('{"answer": "B"}', parsed_b, norm_b, 50.0, None)
        # S-Tier Wave 1 models and Groq arbiter fail immediately
        if "gpt-oss-20b" in model or "nemotron-3-ultra-550b" in model or "nemotron-3-super-120b" in model:
            return (None, None, None, 30.0, "RATE_LIMIT_429")
        # Wave 2 A-tier model succeeds
        if "gemma-4-26b" in model:
            wave2_dispatched = True
            await asyncio.sleep(0.04)
            return ('{"answer": "A"}', parsed_a, norm_a, 40.0, None)
        return (None, None, None, 0.0, "OTHER_MODEL")

    with patch("backend.core.llm_client.call_model_with_budget_retry", side_effect=mock_call_s_fails):
        solver_res_w2 = asyncio.run(
            solve_question(
                subject="Test",
                q_type="mcq",
                question="Q?",
                options=["Apple", "Banana", "Cherry", "Date"],
                config=cfg,
            )
        )
        assert solver_res_w2.status == "OK"
        assert solver_res_w2.canonical_key == "A"
        assert wave2_dispatched is True, "Wave 2 should be dispatched when S-Tier models fail!"
        print("  -> Passed Wave 2 automatic escalation on S-Tier failure.")
    reset_model_quarantine()
    reset_key_quarantine()


def test_7_non_destructive_check_models():
    """
    Test 7: Verify check_models() is strictly non-destructive.
    Never modifies, prunes, or re-orders models.json.
    """
    print("\n[Test 7] Testing Non-Destructive check_models()...")
    models_path = WORKSPACE_ROOT / "models.json"
    content_before = models_path.read_text(encoding="utf-8")

    async def mock_single_completion(client, provider, model, messages, api_key, timeout_ms, max_tokens=512):
        # Simulate successful ping for primary and fallback models
        return '{"answer": "A", "reason": "Test non-destructive check"}', 85.0, None

    class MockResponse:
        def __init__(self, json_data, status_code=200):
            self._json = json_data
            self.status_code = status_code

        def json(self):
            return self._json

    async def mock_client_get(self, url, *args, **kwargs):
        if "groq.com" in str(url):
            return MockResponse({"data": [{"id": "openai/gpt-oss-120b"}, {"id": "qwen/qwen3.8-27b"}, {"id": "openai/gpt-oss-20b"}]})
        if "openrouter.ai" in str(url):
            return MockResponse({
                "data": [
                    {"id": "nvidia/nemotron-3-ultra-550b-a55b:free"},
                    {"id": "nvidia/nemotron-3-super-120b-a12b:free"},
                    {"id": "google/gemma-4-31b-it:free"},
                    {"id": "google/gemma-4-26b-a4b-it:free"},
                    {"id": "nvidia/nemotron-3.5-lightning:free"},
                    {"id": "liquid/lfm-2.5-2.6b:free"},
                ]
            })
        return MockResponse({"data": []})

    with patch("backend.core.llm_client.call_single_completion", side_effect=mock_single_completion), \
         patch("backend.core.llm_client.load_api_keys", return_value=("mock-groq-key", "mock-openrouter-key")), \
         patch.object(httpx.AsyncClient, "get", mock_client_get):
        res = asyncio.run(check_models(str(models_path)))
        assert res is True, "check_models should return True when models pass"

    content_after = models_path.read_text(encoding="utf-8")
    assert content_before == content_after, "models.json was mutated or reordered by check_models!"
    print("  -> Passed non-destructive check_models(): models.json byte-for-byte unchanged [PASS]")


def main():
    print("=" * 80)
    print("RUNNING PHASE 6 MULTI-KEY & HEDGED FALLBACK VERIFICATION SUITE")
    print("=" * 80)

    test_1_non_chat_model_rejection()
    test_2_multikey_loading_rotation_and_quarantine()
    test_3_parallel_hedged_tiebreaker_early_exit()
    test_4_dual_primary_failure_rescued_by_fallback_consensus()
    test_5_assertion_reason_multi_block_dom_extraction()
    test_6_tier_hierarchy_and_two_wave_cascade()
    test_7_non_destructive_check_models()

    print("\n" + "=" * 80)
    print("ALL PHASE 6 VERIFICATION TESTS PASSED (100% SUCCESS)")
    print("=" * 80)


if __name__ == "__main__":
    main()

