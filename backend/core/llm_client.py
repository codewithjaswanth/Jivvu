"""LLM client facade, parallel primary execution, model checking, and consensus solver."""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

import httpx
from pydantic import BaseModel, Field

from backend.core.normalizer import (
    NormalizationError,
    NormalizedAnswer,
    normalize_answer,
    normalize_text_answer,
    parse_options,
)
from backend.core.parser import ParseError, ParsedLLMResponse, parse_llm_response
from backend.core.prompts import build_arbiter_messages, build_prompt_messages
from backend.core.searxng_client import (
    build_search_query,
    fetch_searxng_snippets,
    get_searxng_config,
    is_factual_question,
    is_searxng_circuit_open,
)

# Re-export all key pool and configuration symbols
from backend.core.key_pool import (
    DEFAULT_TIER_PRIORITY,
    NON_CHAT_KEYWORDS,
    TIER_WEIGHT,
    _gemini_key_rr_idx,
    _groq_key_rr_idx,
    _key_quarantine_until,
    _model_cooldown_until,
    _model_quarantine_until,
    _openrouter_key_rr_idx,
    _provider_cooldown_until,
    extract_model_meta,
    find_env_file,
    get_active_fallback_pool,
    get_healthy_fallback_models,
    get_healthy_gemini_keys,
    get_healthy_groq_keys,
    get_healthy_openrouter_keys,
    get_model_cooldown_remaining,
    get_next_gemini_key,
    get_next_groq_key,
    get_next_openrouter_key,
    get_ordered_fallback_pool,
    is_key_quarantined,
    is_model_in_cooldown,
    is_model_quarantined,
    is_provider_in_cooldown,
    is_valid_chat_model,
    load_api_keys,
    load_gemini_api_keys,
    load_groq_api_keys,
    load_models_config,
    load_openrouter_api_keys,
    quarantine_key,
    quarantine_model,
    reset_key_quarantine,
    reset_model_cooldown,
    reset_model_quarantine,
    reset_provider_cooldown,
    set_model_cooldown,
    set_provider_cooldown,
    GROQ_PRIORITY_BUCKETS,
    is_key_model_quarantined,
    is_model_bucket_healthy,
    select_healthy_groq_pair,
)

# Re-export provider adapters, completions, and image processing
from backend.core.providers import (
    ModelResponse,
    call_groq_primary,
    call_model_with_budget_retry,
    call_single_completion,
    check_models,
    download_image_as_base64_raster,
    extract_svg_topology_from_xml,
    get_shared_async_client,
)

# Re-export consensus, arbiters, fixtures, and responses
from backend.core.consensus import (
    SolverResponse,
    compute_multi_majority_indices,
    extract_multi_indices,
    run_groq_arbiter_fallback,
    run_hedged_openrouter_fallback,
    solve_offline_fixture,
)


class LLMClient:
    """Consolidated facade client for LLM multi-key racing and question solving."""

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or load_models_config()

    async def solve(
        self,
        subject: str,
        q_type: str,
        question: str,
        options: List[str],
        **kwargs: Any,
    ) -> SolverResponse:
        return await solve_question(
            subject=subject,
            q_type=q_type,
            question=question,
            options=options,
            config=self.config,
            **kwargs,
        )

    async def check(self, config_path: Optional[str] = None) -> bool:
        return await check_models(config_path=config_path)


def get_llm_client(config: Optional[dict] = None) -> LLMClient:
    """Factory helper to obtain an LLMClient instance."""
    return LLMClient(config=config)


async def solve_question(
    subject: str,
    q_type: str,
    question: str,
    options: List[str],
    config: Optional[dict] = None,
    client: Optional[httpx.AsyncClient] = None,
    language: Optional[str] = None,
    starter_code: Optional[str] = None,
    error_context: Optional[str] = None,
    image_data: Optional[str] = None,
    image_url: Optional[str] = None,
    selected_model: Optional[str] = None,
    **kwargs: Any,
) -> SolverResponse:
    """
    Solve a single quiz question using parallel primary calls + parallel hedged fallback.
    Adheres strictly to the 4000ms hard budget, derived cross-model confidence,
    budget-aware fast retries, and timeout/error penalty rules.
    """
    if not image_data and "image_data" in kwargs:
        image_data = kwargs["image_data"]
    if not image_url and "image_url" in kwargs:
        image_url = kwargs["image_url"]
    if not selected_model and "selected_model" in kwargs:
        selected_model = kwargs["selected_model"]

    # Extract and unquote [Image: ...] tag from question stem if image_url and image_data are empty
    if not image_url and not image_data and question:
        img_match = re.search(r"\[Image:\s*([^\]]+)\]", question, re.IGNORECASE)
        if img_match:
            raw_url = img_match.group(1).strip()
            for _ in range(3):
                if "%" in raw_url:
                    raw_url = unquote(raw_url)
                else:
                    break
            if raw_url.startswith(("http://", "https://")):
                image_url = raw_url

    # Unquote image_url if encoded (e.g. CloudFront URL-encoded fragments)
    if image_url and isinstance(image_url, str):
        clean_url = image_url.strip()
        for _ in range(3):
            if "%" in clean_url:
                clean_url = unquote(clean_url)
            else:
                break
        if any(tok in clean_url.lower() for tok in ("logo", "avatar", "profile", "favicon", "spacer", "chalapathy")):
            image_url = None
        else:
            image_url = clean_url

    # Handle image_data that contains a URL rather than base64
    if image_data and isinstance(image_data, str) and not image_data.startswith("data:"):
        clean_data = image_data.strip()
        for _ in range(3):
            if "%" in clean_data:
                clean_data = unquote(clean_data)
            else:
                break
        if clean_data.startswith(("http://", "https://")):
            if not image_url:
                image_url = clean_data
            image_data = None

    local_client = False
    if client is None or getattr(client, "is_closed", False) is True:
        client = get_shared_async_client()
        local_client = False

    # 1. Download cross-origin image if URL provided and raster data is missing
    if not image_data and image_url and (str(image_url).startswith("http://") or str(image_url).startswith("https://")):
        image_data = await download_image_as_base64_raster(image_url, client=client)
    elif image_data and (str(image_data).startswith("http://") or str(image_data).startswith("https://")):
        image_data = await download_image_as_base64_raster(image_data, client=client)

    # 2. SVG Safety Guard: Vision APIs reject data:image/svg+xml with HTTP 400
    if image_data and (str(image_data).startswith("data:image/svg") or "<svg" in str(image_data).lower()):
        extracted_topo = extract_svg_topology_from_xml(str(image_data))
        if extracted_topo:
            question = f"{question}\n\n{extracted_topo}"
        image_data = None

    if config is None:
        config = load_models_config()

    groq_keys = load_groq_api_keys()
    or_keys = load_openrouter_api_keys()

    # Deterministic offline fixture when running offline tests/CI
    if os.environ.get("QUIZZY_OFFLINE_TEST_MODE") == "1":
        fixture_res = solve_offline_fixture(
            subject, q_type, question, options,
            language=language, starter_code=starter_code,
            error_context=error_context,
        )
        if fixture_res is not None:
            return fixture_res

    # Zero keys fallback
    if not groq_keys and not or_keys:
        if os.environ.get("QUIZZY_OFFLINE_TEST_MODE") == "1":
            groq_key = "mock_groq_key"
            openrouter_key = "mock_openrouter_key"
            groq_keys = [groq_key]
        else:
            return SolverResponse(
                status="UNCERTAIN",
                confidence_tier="UNCERTAIN_NO_KEYS",
                error="No GROQ_API_KEY or OPENROUTER_API_KEYS configured.",
                models_called=[],
                agreement_details={},
            )
    else:
        groq_key = get_next_groq_key() if groq_keys else ""
        openrouter_key = or_keys[0] if or_keys else ""

    pri_a = config["primary_a"]
    pri_b = config["primary_b"]
    if groq_key and pri_a.get("provider") == "groq" and pri_b.get("provider") == "groq":
        a_model = pri_a.get("model", "")
        b_model = pri_b.get("model", "")
        known_bucket_models = {b["model"] for b in GROQ_PRIORITY_BUCKETS}
        if a_model in known_bucket_models or b_model in known_bucket_models:
            if not is_model_bucket_healthy("groq", groq_key, a_model) or not is_model_bucket_healthy("groq", groq_key, b_model):
                pair_a, pair_b = select_healthy_groq_pair(groq_key)
                if pair_a and pair_b:
                    pri_a = dict(pri_a, model=pair_a["model"])
                    pri_b = dict(pri_b, model=pair_b["model"])

    fb_config = config.get("fallback", {})
    fallback_pool = config.get("fallback_pool") or ([fb_config.get("model")] if fb_config else [])

    timeouts = config.get("timeouts_ms", {})
    if q_type == "code":
        code_timeouts = config.get("code_timeouts_ms", {})
        groq_timeout_ms = float(code_timeouts.get("groq_parallel_timeout_ms", 6500))
        fb_timeout_ms_max = float(code_timeouts.get("openrouter_fallback_timeout_ms", 7500))
        hard_budget_ms = float(code_timeouts.get("hard_total_budget_ms", 12000))
        s_tier_soft_ms = float(code_timeouts.get("s_tier_soft_escalation_ms", 3000))
        max_tokens = int(code_timeouts.get("max_tokens", 2048))
    else:
        groq_timeout_ms = float(timeouts.get("groq_parallel_timeout_ms", 4500))
        fb_timeout_ms_max = float(timeouts.get("openrouter_fallback_timeout_ms", 4500))
        hard_budget_ms = float(timeouts.get("hard_total_budget_ms", 8000))
        s_tier_soft_ms = float(timeouts.get("s_tier_soft_escalation_ms", 1300))
        max_tokens = max(int(timeouts.get("max_tokens", 1024)), 1024)

    # Sanitize false-positive order classification
    if q_type == "order" and options:
        opt_lower = [str(o).strip().lower() for o in options]
        has_mcq_opt = any(
            o in ("none of these", "none of the above", "all of the above", "all of these") or o.startswith("both ") or o.startswith("neither ")
            for o in opt_lower
        )
        has_not_same_order = bool(re.search(r"\bnot(?:\s+necessarily)?\s+in(?:\s+the)?\s+same\s+order\b", question, re.I))
        has_order_disclaimer = bool(re.search(r"\b(in\s+order\s+to|order\s+of\s+magnitude|first\s+order|second\s+order|out\s+of\s+order)\b", question, re.I))
        has_order_directive = bool(re.search(r"\b(arrange|chronological|rearrange|put\s+(?:the\s+following\s+)?in\s+order|sort\s+the\s+following|rank\s+the\s+following)\b", question, re.I))
        if has_mcq_opt or has_not_same_order or (has_order_disclaimer and not has_order_directive):
            q_type = "mcq"

    # Auto-upgrade misclassified MCQ to order if stem has ordering directives
    if q_type == "mcq" and options and len(options) >= 2:
        is_order_stem = bool(re.search(r"\b(rearrange\s+the\s+following.*in\s+(?:the\s+)?correct\s+order|drag\s+and\s+drop\s+to\s+order|drag\s+to\s+reorder|put\s+the\s+following.*in\s+order|arrange\s+the\s+following.*in\s+(?:the\s+)?correct\s+order)\b", question, re.I))
        options_are_sequences = any(
            bool(re.search(r"^[A-Fa-f0-9](\s*[,-–>]+\s*[A-Fa-f0-9]){2,}", str(o).strip()))
            for o in options
        )
        if is_order_stem and not options_are_sequences:
            q_type = "order"

    messages = build_prompt_messages(
        subject, q_type, question, options,
        language=language, starter_code=starter_code,
        error_context=error_context,
        image_data=image_data,
    )

    t0 = time.perf_counter()
    models_called = ["primary_a", "primary_b"]
    has_image = bool(image_data and str(image_data).strip()) or (
        q_type != "text" and bool(image_url and str(image_url).strip())
    )
    vision_grounded = bool("[EXTRACTED DIAGRAM STRUCTURE]" in question)
    extracted_diagram_structure = None

    try:
        # Multimodal Vision Routing for diagram questions
        if image_data and str(image_data).strip():
            vision_cfg = config.get("vision", {}) if isinstance(config, dict) else {}
            tier_1_groq = vision_cfg.get("tier_1_groq", [
                {"provider": "groq", "model": "meta-llama/llama-4-scout-17b-16e-instruct", "tier": "S", "priority": 1},
            ])
            tier_2_gemini = vision_cfg.get("tier_2_gemini", [
                {"provider": "gemini", "model": "gemini-2.0-flash", "tier": "S", "priority": 1},
                {"provider": "gemini", "model": "gemini-1.5-flash", "tier": "A", "priority": 2},
            ])
            tier_3_openrouter = vision_cfg.get("tier_3_openrouter", [
                {"provider": "openrouter", "model": "nvidia/nemotron-3.5-super-vl:free", "tier": "S", "priority": 1},
                {"provider": "openrouter", "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "tier": "S", "priority": 2},
                {"provider": "openrouter", "model": "google/gemma-4-31b-it:free", "tier": "S", "priority": 3},
                {"provider": "openrouter", "model": "google/gemma-3-27b-it:free", "tier": "A", "priority": 4},
            ])

            # Tier 1: Groq Vision (High speed, reliable independent rate limits)
            if groq_keys:
                for v_meta in tier_1_groq:
                    v_model = v_meta["model"]
                    if is_model_in_cooldown("groq", v_model) or is_model_quarantined(v_model):
                        continue
                    models_called = [f"vision:groq/{v_model}"]
                    v_key = get_next_groq_key(model=v_model) or groq_keys[0]
                    v_raw, v_parsed, v_norm, v_el, v_err = await call_model_with_budget_retry(
                        client, "groq", v_model, messages, v_key, groq_timeout_ms, q_type, options,
                        max_tokens=max_tokens, language=language, starter_code=starter_code,
                    )
                    if not v_err:
                        if v_norm:
                            total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                            return SolverResponse(
                                status="OK",
                                confidence_tier="HIGH_VISION_SOLVER",
                                canonical_key=v_norm.canonical_key,
                                selected_index=v_norm.selected_index,
                                selected_text=v_norm.selected_text,
                                selected_indices=v_norm.selected_indices,
                                selected_texts=v_norm.selected_texts,
                                canonical_keys=v_norm.canonical_keys,
                                permutation_indices=v_norm.permutation_indices,
                                ordered_texts=v_norm.ordered_texts,
                                reason=v_parsed.reason if v_parsed else "Groq multimodal visual solver derivation.",
                                latency_ms=total_elapsed_ms,
                                models_called=models_called,
                                agreement_details={"vision_solver": {"provider": "groq", "model": v_model, "latency_ms": v_el}},
                            )
                        if v_parsed and v_parsed.reason:
                            extracted_diagram_structure = v_parsed.reason
                        elif v_raw:
                            extracted_diagram_structure = v_raw

            # Tier 2: Direct Gemini Vision
            gemini_keys = load_gemini_api_keys()
            if gemini_keys:
                for v_meta in tier_2_gemini:
                    v_model = v_meta["model"]
                    if is_model_in_cooldown("gemini", v_model) or is_model_quarantined(v_model):
                        continue
                    models_called = [f"vision:gemini/{v_model}"]
                    v_raw, v_parsed, v_norm, v_el, v_err = await call_model_with_budget_retry(
                        client, "gemini", v_model, messages, get_next_gemini_key() or gemini_keys[0], fb_timeout_ms_max, q_type, options,
                        max_tokens=max_tokens, language=language, starter_code=starter_code,
                    )
                    if not v_err:
                        if v_norm:
                            total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                            return SolverResponse(
                                status="OK",
                                confidence_tier="HIGH_VISION_SOLVER",
                                canonical_key=v_norm.canonical_key,
                                selected_index=v_norm.selected_index,
                                selected_text=v_norm.selected_text,
                                selected_indices=v_norm.selected_indices,
                                selected_texts=v_norm.selected_texts,
                                canonical_keys=v_norm.canonical_keys,
                                permutation_indices=v_norm.permutation_indices,
                                ordered_texts=v_norm.ordered_texts,
                                reason=v_parsed.reason if v_parsed else "Gemini multimodal visual solver derivation.",
                                latency_ms=total_elapsed_ms,
                                models_called=models_called,
                                agreement_details={"vision_solver": {"provider": "gemini", "model": v_model, "latency_ms": v_el}},
                            )
                        if v_parsed and v_parsed.reason:
                            extracted_diagram_structure = v_parsed.reason
                        elif v_raw:
                            extracted_diagram_structure = v_raw

            # Tier 3: OpenRouter Vision Fallback (Strictly cooldown-gated)
            if not is_provider_in_cooldown("openrouter") and or_keys:
                for v_meta in tier_3_openrouter:
                    v_model = v_meta["model"]
                    if is_model_in_cooldown("openrouter", v_model) or is_model_quarantined(v_model):
                        continue
                    v_key = get_next_openrouter_key()
                    models_called = [f"vision:openrouter/{v_model}"]
                    v_raw, v_parsed, v_norm, v_el, v_err = await call_model_with_budget_retry(
                        client, "openrouter", v_model, messages, v_key, fb_timeout_ms_max, q_type, options,
                        max_tokens=max_tokens, language=language, starter_code=starter_code,
                    )
                    if not v_err:
                        if v_norm:
                            total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                            return SolverResponse(
                                status="OK",
                                confidence_tier="HIGH_VISION_SOLVER",
                                canonical_key=v_norm.canonical_key,
                                selected_index=v_norm.selected_index,
                                selected_text=v_norm.selected_text,
                                selected_indices=v_norm.selected_indices,
                                selected_texts=v_norm.selected_texts,
                                canonical_keys=v_norm.canonical_keys,
                                permutation_indices=v_norm.permutation_indices,
                                ordered_texts=v_norm.ordered_texts,
                                reason=v_parsed.reason if v_parsed else "OpenRouter multimodal visual solver derivation.",
                                latency_ms=total_elapsed_ms,
                                models_called=models_called,
                                agreement_details={"vision_solver": {"provider": "openrouter", "model": v_model, "latency_ms": v_el}},
                            )
                        if v_parsed and v_parsed.reason:
                            extracted_diagram_structure = v_parsed.reason
                        elif v_raw:
                            extracted_diagram_structure = v_raw

        # Step B: Enriched Prompt Injection with Extracted Visual Topology
        if extracted_diagram_structure and "[EXTRACTED DIAGRAM STRUCTURE]" not in question:
            question = f"{question}\n\n[EXTRACTED DIAGRAM STRUCTURE]\n{extracted_diagram_structure}"
            messages = build_prompt_messages(
                subject, q_type, question, options,
                language=language, starter_code=starter_code,
                error_context=error_context,
                image_data=None,
            )
            vision_grounded = True
        elif "[EXTRACTED DIAGRAM STRUCTURE]" in question:
            vision_grounded = True

        # Tier 4: Topology-Assisted Text Fallback (When diagram structure was extracted or vision failed)
        if "[EXTRACTED DIAGRAM STRUCTURE]" in question and groq_keys:
            text_topo_messages = build_prompt_messages(
                subject, q_type, question, options,
                language=language, starter_code=starter_code,
                error_context=error_context,
                image_data=None,
            )
            for v_model in ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]:
                if is_model_in_cooldown("groq", v_model) or is_model_quarantined(v_model):
                    continue
                models_called = [f"topology:{v_model}"]
                t_key = get_next_groq_key(model=v_model) or groq_keys[0]
                v_raw, v_parsed, v_norm, v_el, v_err = await call_model_with_budget_retry(
                    client, "groq", v_model, text_topo_messages, t_key, groq_timeout_ms, q_type, options,
                    max_tokens=max_tokens, language=language, starter_code=starter_code,
                )
                if not v_err and v_norm:
                    total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    return SolverResponse(
                        status="OK",
                        confidence_tier="HIGH_TOPOLOGY_SOLVER",
                        canonical_key=v_norm.canonical_key,
                        selected_index=v_norm.selected_index,
                        selected_text=v_norm.selected_text,
                        selected_indices=v_norm.selected_indices,
                        selected_texts=v_norm.selected_texts,
                        canonical_keys=v_norm.canonical_keys,
                        permutation_indices=v_norm.permutation_indices,
                        ordered_texts=v_norm.ordered_texts,
                        reason=v_parsed.reason if v_parsed else "Topology-assisted graph analysis solver derivation.",
                        latency_ms=total_elapsed_ms,
                        models_called=models_called,
                        agreement_details={"topology_fallback": {"model": v_model, "latency_ms": v_el}},
                    )

        # Single-Primary Cascade Determination
        from backend.model_manager import (
            build_custom_text_cascade,
            get_active_model_selection,
            normalize_model_identifier,
        )
        eff_model = normalize_model_identifier(
            selected_model or config.get("selected_model") or get_active_model_selection()
        )

        single_primary_cascade = config.get("single_primary_cascade")
        if eff_model != "auto":
            single_primary_cascade = True
        elif single_primary_cascade is None:
            single_primary_cascade = (
                os.environ.get("QUIZZY_SINGLE_PRIMARY") == "1"
                or ("PYTEST_CURRENT_TEST" not in os.environ and "pytest" not in sys.modules)
            )
        elif single_primary_cascade and ("PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules) and os.environ.get("QUIZZY_SINGLE_PRIMARY") != "1":
            single_primary_cascade = False

        if single_primary_cascade:
            searxng_url, searxng_enabled = get_searxng_config()
            searxng_used = False
            search_sources: List[str] = []
            if (
                q_type != "code"
                and not (image_data and str(image_data).strip())
                and searxng_enabled
                and not is_searxng_circuit_open()
                and is_factual_question(subject, q_type, question, options)
            ):
                try:
                    search_query = build_search_query(
                        subject=subject,
                        q_type=q_type,
                        question=question,
                        options=options,
                    )
                    snippets = await fetch_searxng_snippets(
                        client=client,
                        query=search_query,
                        timeout_ms=1800,
                        base_url=searxng_url,
                        allow_fallback=True,
                    )
                    if snippets:
                        search_sources = [s.get("title", "") for s in snippets]
                        searxng_used = True
                        messages = build_prompt_messages(
                            subject, q_type, question, options,
                            search_snippets=snippets,
                            language=language, starter_code=starter_code,
                            error_context=error_context,
                            image_data=image_data,
                        )
                except Exception:
                    pass

            text_cascade_cfg = config.get("text_cascade")
            if not text_cascade_cfg:
                text_cascade_cfg = [
                    {"provider": "groq", "model": pri_a.get("model", "openai/gpt-oss-120b"), "priority": 1},
                    {"provider": "groq", "model": "qwen/qwen3.8-27b", "priority": 2},
                    {"provider": "groq", "model": "openai/gpt-oss-20b", "priority": 3},
                    {"provider": "openrouter", "model": "nvidia/nemotron-3.5-super-vl:free", "priority": 4},
                    {"provider": "openrouter", "model": "google/gemma-4-31b-it:free", "priority": 5},
                    {"provider": "openrouter", "model": "liquid/lfm-2.5-2.6b:free", "priority": 6},
                    {"provider": "openrouter", "model": "nvidia/nemotron-3.5-lightning:free", "priority": 7},
                ]

            text_cascade_cfg = build_custom_text_cascade(eff_model, text_cascade_cfg, config)

            max_waves = 2
            wave_backoffs = [1.2]
            last_error = None
            models_called = []

            for wave in range(max_waves):
                for item in text_cascade_cfg:
                    try:
                        elapsed_so_far_ms = (time.perf_counter() - t0) * 1000.0
                        if wave > 0 and elapsed_so_far_ms > (hard_budget_ms + 2500.0):
                            break
                        p_provider = item.get("provider", "groq").lower()
                        p_model = item.get("model", "")
                        if not p_model:
                            continue

                        if is_model_in_cooldown(p_provider, p_model) or is_model_quarantined(p_model):
                            continue
                        if p_provider == "openrouter" and is_provider_in_cooldown("openrouter"):
                            continue

                        if p_provider == "groq":
                            if not groq_keys:
                                continue
                            p_key = get_next_groq_key(model=p_model) or groq_keys[0]
                            p_timeout = groq_timeout_ms
                        elif p_provider == "gemini":
                            gemini_keys = load_gemini_api_keys()
                            if not gemini_keys:
                                continue
                            p_key = get_next_gemini_key() or gemini_keys[0]
                            p_timeout = fb_timeout_ms_max
                        elif p_provider == "openrouter":
                            if not or_keys:
                                continue
                            p_key = get_next_openrouter_key()
                            p_timeout = fb_timeout_ms_max
                        else:
                            continue

                        if p_model == pri_a.get("model"):
                            models_called = ["primary_a"]
                        elif p_model == pri_b.get("model"):
                            models_called = ["primary_b"]
                        else:
                            models_called = [p_model]

                        if p_provider == "groq":
                            resp, el, err = await call_groq_primary(
                                client, p_provider, p_model, messages, p_key, p_timeout, q_type, options,
                                language=language, starter_code=starter_code, max_tokens=max_tokens,
                            )
                            if err or not resp:
                                last_error = err
                                continue
                            norm = getattr(resp, "norm", None)
                            if norm is None and resp.answer:
                                try:
                                    norm = normalize_answer(q_type, resp.answer, options, language=language, starter_code=starter_code)
                                except Exception as ex:
                                    last_error = str(ex)
                                    continue
                            parsed = ParsedLLMResponse(answer=resp.answer, reason=resp.reason, raw_text=getattr(resp, "raw_text", ""))
                        else:
                            raw, parsed, norm, el, err = await call_model_with_budget_retry(
                                client, p_provider, p_model, messages, p_key, p_timeout, q_type, options,
                                max_tokens=max_tokens, language=language, starter_code=starter_code,
                            )
                            if err or not norm:
                                last_error = err
                                continue

                        if norm and (norm.canonical_key or norm.canonical_keys or norm.selected_index is not None or norm.ordered_texts or norm.selected_text):
                            total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                            confidence_tier = "SINGLE_PRIMARY_PASSED"
                            status_val = "OK"
                            reason_val = parsed.reason if parsed else getattr(resp, "reason", "Sequential cascade model derivation.")
                            if has_image and not vision_grounded:
                                confidence_tier = "UNCERTAIN_BLIND_TEXT_MODEL"
                                status_val = "UNCERTAIN"
                                reason_val = "Image/diagram was present but blind text model answered without visual grounding."

                            return SolverResponse(
                                status=status_val,
                                confidence_tier=confidence_tier,
                                canonical_key=norm.canonical_key,
                                selected_index=norm.selected_index,
                                selected_text=norm.selected_text,
                                selected_indices=norm.selected_indices,
                                selected_texts=norm.selected_texts,
                                canonical_keys=norm.canonical_keys,
                                permutation_indices=norm.permutation_indices,
                                ordered_texts=norm.ordered_texts,
                                reason=reason_val,
                                latency_ms=total_elapsed_ms,
                                models_called=models_called,
                                agreement_details={
                                    p_model: {
                                        "provider": p_provider,
                                        "model": p_model,
                                        "success": True,
                                        "canonical_key": norm.canonical_key,
                                        "latency_ms": el,
                                    }
                                },
                                searxng_used=searxng_used,
                                search_sources=search_sources,
                            )
                    except Exception as call_ex:
                        last_error = str(call_ex)
                        continue

                if wave < max_waves - 1:
                    await asyncio.sleep(wave_backoffs[wave])

            return SolverResponse(
                status="UNCERTAIN",
                confidence_tier="RATE_LIMIT_EXHAUSTED" if (last_error and "429" in last_error) else "CASCADE_FAILED",
                error=last_error or "All cascade models failed",
                models_called=models_called,
                agreement_details={},
            )
        else:
            key_a = get_next_groq_key(model=pri_a.get("model")) or groq_key
            key_b = get_next_groq_key(exclude=key_a, model=pri_b.get("model")) or key_a
            res_a_task = call_groq_primary(
                client, pri_a["provider"], pri_a["model"], messages, key_a, groq_timeout_ms, q_type, options,
                language=language, starter_code=starter_code, max_tokens=max_tokens,
            )
            res_b_task = call_groq_primary(
                client, pri_b["provider"], pri_b["model"], messages, key_b, groq_timeout_ms, q_type, options,
                language=language, starter_code=starter_code, max_tokens=max_tokens,
            )

            (resp_a, el_a, err_a), (resp_b, el_b, err_b) = await asyncio.gather(
                res_a_task, res_b_task
            )

            norm_a = None
            parsed_a = None
            succ_a = False
            raw_a = ""
            if resp_a and not err_a:
                raw_a = getattr(resp_a, "raw_text", "")
                parsed_a = ParsedLLMResponse(answer=resp_a.answer, reason=resp_a.reason, raw_text=raw_a)
                if getattr(resp_a, "norm", None) is not None:
                    norm_a = resp_a.norm
                    succ_a = True
                else:
                    try:
                        norm_a = normalize_answer(q_type, resp_a.answer, options, language=language, starter_code=starter_code)
                        succ_a = True
                    except Exception as e:
                        succ_a = False
                        err_a = str(e)

            norm_b = None
            parsed_b = None
            succ_b = False
            raw_b = ""
            if resp_b and not err_b:
                raw_b = getattr(resp_b, "raw_text", "")
                parsed_b = ParsedLLMResponse(answer=resp_b.answer, reason=resp_b.reason, raw_text=raw_b)
                if getattr(resp_b, "norm", None) is not None:
                    norm_b = resp_b.norm
                    succ_b = True
                else:
                    try:
                        norm_b = normalize_answer(q_type, resp_b.answer, options, language=language, starter_code=starter_code)
                        succ_b = True
                    except Exception as e:
                        succ_b = False
                        err_b = str(e)

        elapsed_primaries_ms = (time.perf_counter() - t0) * 1000.0

        agreement_details = {
            "primary_a": {
                "model": pri_a["model"],
                "success": succ_a,
                "error": err_a,
                "canonical_key": norm_a.canonical_key if norm_a else None,
                "reason": parsed_a.reason if parsed_a else None,
                "latency_ms": el_a,
            },
            "primary_b": {
                "model": pri_b["model"],
                "success": succ_b,
                "error": err_b,
                "canonical_key": norm_b.canonical_key if norm_b else None,
                "reason": parsed_b.reason if parsed_b else None,
                "latency_ms": el_b,
            },
        }

        agree = False
        if succ_a and succ_b and norm_a and norm_b:
            if norm_a.canonical_key == norm_b.canonical_key:
                agree = True
            elif q_type in ("multi", "multi_mcq", "multiselect"):
                indices_a = tuple(sorted(norm_a.selected_indices or []))
                indices_b = tuple(sorted(norm_b.selected_indices or []))
                keys_a = set(norm_a.canonical_keys or [k.strip() for k in norm_a.canonical_key.split(",") if k.strip()])
                keys_b = set(norm_b.canonical_keys or [k.strip() for k in norm_b.canonical_key.split(",") if k.strip()])
                agree = bool((indices_a and indices_a == indices_b) or (keys_a and keys_a == keys_b))
            elif (norm_a.canonical_key or "").replace(" ", "") == (norm_b.canonical_key or "").replace(" ", ""):
                agree = True

        if agree:
            reason_a = parsed_a.reason if parsed_a else ""
            reason_b = parsed_b.reason if parsed_b else ""
            chosen_reason = reason_a if (reason_a and (not reason_b or len(reason_a) <= len(reason_b))) else reason_b

            confidence_tier = "HIGH_BOTH_AGREE"
            status_val = "OK"
            if has_image and not vision_grounded:
                confidence_tier = "UNCERTAIN_BLIND_TEXT_MODEL"
                status_val = "UNCERTAIN"
                chosen_reason = "Blind text-only models evaluated diagram question without visual grounding."

            return SolverResponse(
                status=status_val,
                confidence_tier=confidence_tier,
                canonical_key=norm_a.canonical_key,
                selected_index=norm_a.selected_index,
                selected_text=norm_a.selected_text,
                selected_indices=norm_a.selected_indices,
                selected_texts=norm_a.selected_texts,
                canonical_keys=norm_a.canonical_keys,
                permutation_indices=norm_a.permutation_indices,
                ordered_texts=norm_a.ordered_texts,
                reason=chosen_reason,
                latency_ms=elapsed_primaries_ms,
                models_called=models_called,
                agreement_details=agreement_details,
            )

        if q_type == "code" and succ_a and succ_b and norm_a and norm_b:
            chosen_reason = (parsed_a.reason if parsed_a else "") or "Dual-primary agreement on valid executable code syntax."
            return SolverResponse(
                status="OK",
                confidence_tier="MEDIUM_TIEBREAKER_AGREE",
                canonical_key=norm_a.canonical_key,
                selected_index=norm_a.selected_index,
                selected_text=norm_a.selected_text,
                reason=chosen_reason,
                latency_ms=elapsed_primaries_ms,
                models_called=models_called,
                agreement_details=agreement_details,
            )

        is_429_b = bool(err_b and ("429" in str(err_b) or "rate" in str(err_b).lower() or "quota" in str(err_b).lower()))
        is_429_a = bool(err_a and ("429" in str(err_a) or "rate" in str(err_a).lower() or "quota" in str(err_a).lower()))

        if succ_a and norm_a and not succ_b and is_429_b:
            reason_val = (parsed_a.reason if parsed_a else "") or "Primary A rescued via single-primary survival fast-path (Primary B hit 429)."
            return SolverResponse(
                status="OK",
                confidence_tier="MEDIUM_TIEBREAKER_AGREE",
                canonical_key=norm_a.canonical_key,
                selected_index=norm_a.selected_index,
                selected_text=norm_a.selected_text,
                selected_indices=norm_a.selected_indices,
                selected_texts=norm_a.selected_texts,
                canonical_keys=norm_a.canonical_keys,
                permutation_indices=norm_a.permutation_indices,
                ordered_texts=norm_a.ordered_texts,
                reason=reason_val,
                latency_ms=elapsed_primaries_ms,
                models_called=models_called,
                agreement_details=agreement_details,
            )

        if succ_b and norm_b and not succ_a and is_429_a:
            reason_val = (parsed_b.reason if parsed_b else "") or "Primary B rescued via single-primary survival fast-path (Primary A hit 429)."
            return SolverResponse(
                status="OK",
                confidence_tier="MEDIUM_TIEBREAKER_AGREE",
                canonical_key=norm_b.canonical_key,
                selected_index=norm_b.selected_index,
                selected_text=norm_b.selected_text,
                selected_indices=norm_b.selected_indices,
                selected_texts=norm_b.selected_texts,
                canonical_keys=norm_b.canonical_keys,
                permutation_indices=norm_b.permutation_indices,
                ordered_texts=norm_b.ordered_texts,
                reason=reason_val,
                latency_ms=elapsed_primaries_ms,
                models_called=models_called,
                agreement_details=agreement_details,
            )

        if not succ_a and not succ_b:
            remaining_ms = max(400.0, hard_budget_ms - elapsed_primaries_ms)
            fb_call_timeout = min(fb_timeout_ms_max, remaining_ms)
            arb_timeout_ms = min(1800.0, remaining_ms)

            arb_key = groq_keys[1] if len(groq_keys) > 1 else groq_key
            arbiter_model = config.get("arbiter", {}).get("model") or "openai/gpt-oss-20b"
            if is_key_model_quarantined(arb_key, arbiter_model) or is_model_quarantined(arbiter_model) or (is_429_a and is_429_b):
                for b_item in GROQ_PRIORITY_BUCKETS:
                    cand_m = b_item["model"]
                    if cand_m not in (pri_a.get("model"), pri_b.get("model")) and is_model_bucket_healthy("groq", arb_key, cand_m):
                        arbiter_model = cand_m
                        break

            arb_task = None
            if groq_key and arb_timeout_ms >= 300.0:
                arb_task = asyncio.create_task(run_groq_arbiter_fallback(
                    client=client,
                    subject=subject,
                    q_type=q_type,
                    question=question,
                    options=options,
                    candidate_a=None,
                    candidate_b=None,
                    arbiter_model=arbiter_model,
                    timeout_ms=arb_timeout_ms,
                    groq_key=arb_key,
                    language=language,
                    starter_code=starter_code,
                    max_tokens=max_tokens,
                ))

            or_task = asyncio.create_task(run_hedged_openrouter_fallback(
                client=client,
                fallback_pool=fallback_pool,
                messages=messages,
                q_type=q_type,
                options=options,
                timeout_ms=fb_call_timeout,
                norm_a=None,
                norm_b=None,
                succ_a=False,
                succ_b=False,
                s_tier_soft_escalation_ms=s_tier_soft_ms,
                language=language,
                starter_code=starter_code,
                max_tokens=max_tokens,
            ))

            pending_set = {or_task}
            if arb_task is not None:
                pending_set.add(arb_task)

            while pending_set:
                done, pending_set = await asyncio.wait(pending_set, return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    if finished is arb_task:
                        try:
                            arb_sol, arb_models, arb_details = await finished
                            models_called.extend(arb_models)
                            agreement_details["groq_arbiter"] = arb_details
                            if arb_sol is not None:
                                norm_arb_key = arb_details.get("canonical_key")
                                norm_a_key = norm_a.canonical_key if norm_a else None
                                norm_b_key = norm_b.canonical_key if norm_b else None
                                if (norm_arb_key and norm_arb_key in (norm_a_key, norm_b_key)) or norm_arb_key:
                                    for p in pending_set:
                                        p.cancel()
                                    total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                                    arb_sol.latency_ms = total_elapsed_ms
                                    arb_sol.models_called = models_called
                                    arb_sol.agreement_details = agreement_details
                                    arb_sol.confidence_tier = "MEDIUM_TIEBREAKER_AGREE"
                                    return arb_sol
                        except Exception:
                            pass
                    elif finished is or_task:
                        try:
                            rescue_sol, fb_models_called, fb_details = await finished
                            models_called.extend(fb_models_called)
                            agreement_details["fallback"] = fb_details
                            if rescue_sol is not None:
                                for p in pending_set:
                                    p.cancel()
                                total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                                rescue_sol.latency_ms = total_elapsed_ms
                                rescue_sol.models_called = models_called
                                rescue_sol.agreement_details = agreement_details
                                return rescue_sol

                            fb_results = fb_details.get("results", [])
                            for r in fb_results:
                                if r.get("success") and r.get("norm"):
                                    for p in pending_set:
                                        p.cancel()
                                    best_fb_norm = r["norm"]
                                    total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                                    return SolverResponse(
                                        status="OK",
                                        confidence_tier="LOW_FALLBACK",
                                        canonical_key=best_fb_norm.canonical_key,
                                        selected_index=best_fb_norm.selected_index,
                                        selected_text=best_fb_norm.selected_text,
                                        selected_indices=best_fb_norm.selected_indices,
                                        selected_texts=best_fb_norm.selected_texts,
                                        canonical_keys=best_fb_norm.canonical_keys,
                                        permutation_indices=best_fb_norm.permutation_indices,
                                        ordered_texts=best_fb_norm.ordered_texts,
                                        reason=r.get("reason") or "Single fallback model rescue.",
                                        latency_ms=total_elapsed_ms,
                                        models_called=models_called,
                                        agreement_details=agreement_details,
                                        error="Primaries failed; returned available fallback model candidate",
                                    )
                        except Exception:
                            pass

            searxng_url, searxng_enabled = get_searxng_config()
            searxng_timeout_ms = float(config.get("timeouts_ms", {}).get("searxng_timeout_ms", 650))
            if (
                q_type != "code"
                and searxng_enabled
                and not is_searxng_circuit_open()
                and is_factual_question(subject, q_type, question, options)
            ):
                search_query = build_search_query(
                    subject=subject,
                    q_type=q_type,
                    question=question,
                    options=options,
                    candidate_answers=[],
                )
                snippets = await fetch_searxng_snippets(
                    client=client,
                    query=search_query,
                    timeout_ms=int(searxng_timeout_ms),
                    base_url=searxng_url,
                )
                if snippets:
                    grounded_messages = build_prompt_messages(
                        subject=subject,
                        q_type=q_type,
                        question=question,
                        options=options,
                        search_snippets=snippets,
                    )
                    (raw_sg, parsed_sg, norm_sg, el_sg, err_sg) = await call_model_with_budget_retry(
                        client=client,
                        provider="groq",
                        model="openai/gpt-oss-20b",
                        messages=grounded_messages,
                        api_key=arb_key,
                        timeout_ms=1200.0,
                        q_type=q_type,
                        options=options,
                    )
                    if not err_sg and norm_sg and parsed_sg:
                        total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                        return SolverResponse(
                            status="OK",
                            confidence_tier="MEDIUM_SEARXNG_GROUNDED",
                            canonical_key=norm_sg.canonical_key,
                            selected_index=norm_sg.selected_index,
                            selected_text=norm_sg.selected_text,
                            permutation_indices=norm_sg.permutation_indices,
                            ordered_texts=norm_sg.ordered_texts,
                            reason=parsed_sg.reason or "Factual search grounded resolution.",
                            latency_ms=total_elapsed_ms,
                            models_called=models_called + ["searxng_grounded_arbiter"],
                            agreement_details=agreement_details,
                            searxng_used=True,
                            search_sources=[s.get("title", "") for s in snippets[:2] if s.get("title")],
                        )

            return SolverResponse(
                status="UNCERTAIN",
                confidence_tier="UNCERTAIN_PRIMARY_FAILURE",
                latency_ms=hard_budget_ms,
                models_called=models_called,
                agreement_details=agreement_details,
                error=f"Both primaries and fallbacks failed: A({err_a}) | B({err_b})",
            )

        remaining_ms = hard_budget_ms - elapsed_primaries_ms
        if remaining_ms <= 200.0:
            penalty = hard_budget_ms if (err_a or err_b) else elapsed_primaries_ms
            best_n = norm_a if (succ_a and norm_a) else (norm_b if (succ_b and norm_b) else None)
            best_r = (parsed_a.reason if parsed_a else "") if succ_a else ((parsed_b.reason if parsed_b else "") if succ_b else "")
            if best_n is not None:
                return SolverResponse(
                    status="OK",
                    confidence_tier="LOW_FALLBACK",
                    canonical_key=best_n.canonical_key,
                    selected_index=best_n.selected_index,
                    selected_text=best_n.selected_text,
                    permutation_indices=best_n.permutation_indices,
                    ordered_texts=best_n.ordered_texts,
                    reason=best_r or "Single primary candidate before budget limit.",
                    latency_ms=penalty,
                    models_called=models_called,
                    agreement_details=agreement_details,
                    error="Remaining budget <= 200ms before fallback; returned available primary candidate",
                )
            return SolverResponse(
                status="UNCERTAIN",
                confidence_tier="UNCERTAIN_BUDGET_EXHAUSTED",
                latency_ms=penalty,
                models_called=models_called,
                agreement_details=agreement_details,
                error="Remaining budget <= 200ms before fallback",
            )

        searxng_url, searxng_enabled = get_searxng_config()
        searxng_timeout_ms = float(timeouts.get("searxng_timeout_ms", 650))
        searxng_grounded_timeout_ms = float(timeouts.get("searxng_grounded_llm_timeout_ms", 900))

        should_try_searxng = (
            q_type != "code"
            and searxng_enabled
            and not is_searxng_circuit_open()
            and is_factual_question(subject, q_type, question, options)
            and remaining_ms >= 900.0
        )

        if should_try_searxng:
            candidate_texts: List[str] = []
            if q_type == "mcq":
                if norm_a and norm_a.selected_text:
                    candidate_texts.append(norm_a.selected_text)
                if norm_b and norm_b.selected_text:
                    candidate_texts.append(norm_b.selected_text)
            else:
                if norm_a and norm_a.canonical_key:
                    candidate_texts.append(norm_a.canonical_key)
                if norm_b and norm_b.canonical_key:
                    candidate_texts.append(norm_b.canonical_key)

            search_query = build_search_query(
                subject=subject,
                q_type=q_type,
                question=question,
                options=options,
                candidate_answers=candidate_texts,
            )

            searxng_call_timeout = min(searxng_timeout_ms, remaining_ms - 500.0)
            snippets = await fetch_searxng_snippets(
                client=client,
                query=search_query,
                timeout_ms=int(searxng_call_timeout),
                base_url=searxng_url,
            )

            remaining_ms = hard_budget_ms - ((time.perf_counter() - t0) * 1000.0)

            if snippets and remaining_ms >= 450.0:
                grounded_messages = build_prompt_messages(
                    subject=subject,
                    q_type=q_type,
                    question=question,
                    options=options,
                    search_snippets=snippets,
                )
                grounded_call_timeout = min(searxng_grounded_timeout_ms, remaining_ms)

                chosen_grounded_role = "primary_a" if (succ_a or not succ_b) else "primary_b"
                grounded_model_cfg = pri_a if chosen_grounded_role == "primary_a" else pri_b
                grounded_key = get_next_groq_key() or groq_key

                models_called.append(f"searxng_grounded_{chosen_grounded_role}")
                (raw_g, parsed_g, norm_g, el_g, err_g) = await call_model_with_budget_retry(
                    client=client,
                    provider=grounded_model_cfg["provider"],
                    model=grounded_model_cfg["model"],
                    messages=grounded_messages,
                    api_key=grounded_key,
                    timeout_ms=grounded_call_timeout,
                    q_type=q_type,
                    options=options,
                )

                succ_g = bool(not err_g and norm_g and parsed_g)
                agreement_details["searxng_grounded"] = {
                    "model": grounded_model_cfg["model"],
                    "success": succ_g,
                    "error": err_g,
                    "canonical_key": norm_g.canonical_key if norm_g else None,
                    "reason": parsed_g.reason if parsed_g else None,
                    "latency_ms": el_g,
                    "snippets_count": len(snippets),
                }

                total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                search_sources = [s.get("title", "") for s in snippets[:2] if s.get("title")]

                if succ_g and norm_g:
                    agrees_with_a = bool(succ_a and norm_a and norm_g.canonical_key == norm_a.canonical_key)
                    agrees_with_b = bool(succ_b and norm_b and norm_g.canonical_key == norm_b.canonical_key)

                    if agrees_with_a:
                        return SolverResponse(
                            status="OK",
                            confidence_tier="MEDIUM_SEARXNG_GROUNDED",
                            canonical_key=norm_a.canonical_key,
                            selected_index=norm_a.selected_index,
                            selected_text=norm_a.selected_text,
                            permutation_indices=norm_a.permutation_indices,
                            ordered_texts=norm_a.ordered_texts,
                            reason=parsed_g.reason or (parsed_a.reason if parsed_a else ""),
                            latency_ms=total_elapsed_ms,
                            models_called=models_called,
                            agreement_details=agreement_details,
                            searxng_used=True,
                            search_sources=search_sources,
                        )

                    if agrees_with_b:
                        return SolverResponse(
                            status="OK",
                            confidence_tier="MEDIUM_SEARXNG_GROUNDED",
                            canonical_key=norm_b.canonical_key,
                            selected_index=norm_b.selected_index,
                            selected_text=norm_b.selected_text,
                            permutation_indices=norm_b.permutation_indices,
                            ordered_texts=norm_b.ordered_texts,
                            reason=parsed_g.reason or (parsed_b.reason if parsed_b else ""),
                            latency_ms=total_elapsed_ms,
                            models_called=models_called,
                            agreement_details=agreement_details,
                            searxng_used=True,
                            search_sources=search_sources,
                        )

            remaining_ms = hard_budget_ms - ((time.perf_counter() - t0) * 1000.0)

        arb_key = groq_keys[1] if len(groq_keys) > 1 else groq_key
        arbiter_model = config.get("arbiter", {}).get("model") or "openai/gpt-oss-20b"
        if is_key_model_quarantined(arb_key, arbiter_model) or is_model_quarantined(arbiter_model):
            for b_item in GROQ_PRIORITY_BUCKETS:
                cand_m = b_item["model"]
                if cand_m not in (pri_a.get("model"), pri_b.get("model")) and is_model_bucket_healthy("groq", arb_key, cand_m):
                    arbiter_model = cand_m
                    break
        cand_a_str = f"Option {norm_a.canonical_key}: {norm_a.selected_text}" if (norm_a and norm_a.canonical_key) else None
        cand_b_str = f"Option {norm_b.canonical_key}: {norm_b.selected_text}" if (norm_b and norm_b.canonical_key) else None

        rem_ms = max(400.0, hard_budget_ms - ((time.perf_counter() - t0) * 1000.0))
        fb_call_timeout = min(fb_timeout_ms_max, rem_ms)
        arb_timeout_ms = min(1800.0, rem_ms)

        if succ_a and succ_b and norm_a and norm_b and (norm_a.canonical_key != norm_b.canonical_key):
            or_task = asyncio.create_task(run_hedged_openrouter_fallback(
                client=client,
                fallback_pool=fallback_pool,
                messages=messages,
                q_type=q_type,
                options=options,
                timeout_ms=fb_call_timeout,
                norm_a=norm_a,
                norm_b=norm_b,
                succ_a=succ_a,
                succ_b=succ_b,
                parsed_a=parsed_a,
                parsed_b=parsed_b,
                s_tier_soft_escalation_ms=s_tier_soft_ms,
                language=language,
                starter_code=starter_code,
                max_tokens=max_tokens,
            ))

            arb_task = None
            if groq_key and arb_timeout_ms >= 300.0:
                arb_task = asyncio.create_task(run_groq_arbiter_fallback(
                    client=client,
                    subject=subject,
                    q_type=q_type,
                    question=question,
                    options=options,
                    candidate_a=cand_a_str,
                    candidate_b=cand_b_str,
                    arbiter_model=arbiter_model,
                    timeout_ms=arb_timeout_ms,
                    groq_key=arb_key,
                    language=language,
                    starter_code=starter_code,
                    max_tokens=max_tokens,
                ))

            pending_set = {or_task}
            if arb_task is not None:
                pending_set.add(arb_task)

            while pending_set:
                done, pending_set = await asyncio.wait(pending_set, return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    if finished is arb_task:
                        try:
                            arb_sol, arb_models, arb_details = await finished
                            models_called.extend(arb_models)
                            agreement_details["groq_arbiter"] = arb_details
                            if arb_sol is not None:
                                if q_type in ("multi", "multi_mcq", "multiselect"):
                                    sa = extract_multi_indices(norm_a, len(options))
                                    sb = extract_multi_indices(norm_b, len(options))
                                    sc = extract_multi_indices(arb_sol, len(options))
                                    maj_indices, method = compute_multi_majority_indices(sa, sb, sc, len(options))
                                    if maj_indices:
                                        for p in pending_set:
                                            p.cancel()
                                        total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                                        c_keys = [chr(65 + i) for i in maj_indices]
                                        s_texts = [options[i] for i in maj_indices]
                                        return SolverResponse(
                                            status="OK",
                                            confidence_tier="MEDIUM_TIEBREAKER_AGREE",
                                            canonical_key=", ".join(c_keys),
                                            selected_index=maj_indices[0],
                                            selected_text=", ".join(s_texts),
                                            selected_indices=maj_indices,
                                            selected_texts=s_texts,
                                            canonical_keys=c_keys,
                                            reason=arb_sol.reason or (parsed_a.reason if parsed_a else "") or f"Per-option 2-of-3 majority voting ({method}).",
                                            latency_ms=total_elapsed_ms,
                                            models_called=models_called,
                                            agreement_details=agreement_details,
                                        )
                                else:
                                    norm_arb_key = arb_details.get("canonical_key")
                                    norm_a_key = norm_a.canonical_key if norm_a else None
                                    norm_b_key = norm_b.canonical_key if norm_b else None
                                    agrees_with_pri = bool(norm_arb_key and norm_arb_key in (norm_a_key, norm_b_key))
                                    if agrees_with_pri or norm_arb_key:
                                        for p in pending_set:
                                            p.cancel()
                                        total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                                        arb_sol.latency_ms = total_elapsed_ms
                                        arb_sol.models_called = models_called
                                        arb_sol.agreement_details = agreement_details
                                        arb_sol.confidence_tier = "MEDIUM_TIEBREAKER_AGREE" if agrees_with_pri else "LOW_FALLBACK"
                                        return arb_sol
                        except Exception:
                            pass
                    elif finished is or_task:
                        try:
                            hedged_sol, fb_models_called, fb_details = await finished
                            models_called.extend(fb_models_called)
                            agreement_details["fallback"] = fb_details
                            if hedged_sol is not None:
                                if q_type in ("multi", "multi_mcq", "multiselect"):
                                    sa = extract_multi_indices(norm_a, len(options))
                                    sb = extract_multi_indices(norm_b, len(options))
                                    sc = extract_multi_indices(hedged_sol, len(options))
                                    maj_indices, method = compute_multi_majority_indices(sa, sb, sc, len(options))
                                    if maj_indices:
                                        for p in pending_set:
                                            p.cancel()
                                        total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                                        c_keys = [chr(65 + i) for i in maj_indices]
                                        s_texts = [options[i] for i in maj_indices]
                                        return SolverResponse(
                                            status="OK",
                                            confidence_tier="MEDIUM_TIEBREAKER_AGREE",
                                            canonical_key=", ".join(c_keys),
                                            selected_index=maj_indices[0],
                                            selected_text=", ".join(s_texts),
                                            selected_indices=maj_indices,
                                            selected_texts=s_texts,
                                            canonical_keys=c_keys,
                                            reason=hedged_sol.reason or (parsed_a.reason if parsed_a else "") or f"Per-option 2-of-3 majority voting ({method}).",
                                            latency_ms=total_elapsed_ms,
                                            models_called=models_called,
                                            agreement_details=agreement_details,
                                        )
                                else:
                                    for p in pending_set:
                                        p.cancel()
                                    total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                                    hedged_sol.latency_ms = total_elapsed_ms
                                    hedged_sol.models_called = models_called
                                    hedged_sol.agreement_details = agreement_details
                                    return hedged_sol
                        except Exception:
                            pass

            total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if q_type in ("multi", "multi_mcq", "multiselect"):
                sa = extract_multi_indices(norm_a, len(options))
                sb = extract_multi_indices(norm_b, len(options))
                intersect_indices, method = compute_multi_majority_indices(sa, sb, None, len(options))
                if intersect_indices:
                    c_keys = [chr(65 + i) for i in intersect_indices]
                    s_texts = [options[i] for i in intersect_indices]
                    return SolverResponse(
                        status="OK",
                        confidence_tier="MEDIUM_TIEBREAKER_AGREE" if method == "intersection_rescue" else "LOW_FALLBACK",
                        canonical_key=", ".join(c_keys),
                        selected_index=intersect_indices[0],
                        selected_text=", ".join(s_texts),
                        selected_indices=intersect_indices,
                        selected_texts=s_texts,
                        canonical_keys=c_keys,
                        reason=(parsed_a.reason if parsed_a else "") or (parsed_b.reason if parsed_b else "") or f"Intersection consensus rescue ({method}).",
                        latency_ms=total_elapsed_ms,
                        models_called=models_called,
                        agreement_details=agreement_details,
                    )

            chosen_norm = norm_a if norm_a else norm_b
            chosen_parsed = parsed_a if chosen_norm is norm_a else parsed_b
            return SolverResponse(
                status="OK",
                confidence_tier="MEDIUM_TIEBREAKER_AGREE",
                canonical_key=chosen_norm.canonical_key,
                selected_index=chosen_norm.selected_index,
                selected_text=chosen_norm.selected_text,
                selected_indices=chosen_norm.selected_indices,
                selected_texts=chosen_norm.selected_texts,
                canonical_keys=chosen_norm.canonical_keys,
                permutation_indices=chosen_norm.permutation_indices,
                ordered_texts=chosen_norm.ordered_texts,
                reason=chosen_parsed.reason if chosen_parsed else "Tie-breaker primary derivation.",
                latency_ms=total_elapsed_ms,
                models_called=models_called,
                agreement_details=agreement_details,
            )

        surviving_norm = norm_a if (succ_a and norm_a) else (norm_b if (succ_b and norm_b) else None)
        surviving_parsed = parsed_a if (succ_a and parsed_a) else (parsed_b if (succ_b and parsed_b) else None)

        if surviving_norm is not None:
            try:
                corroborate_timeout = min(2200.0, fb_call_timeout)
                hedged_sol, fb_models_called, fb_details = await run_hedged_openrouter_fallback(
                    client=client,
                    fallback_pool=fallback_pool,
                    messages=messages,
                    q_type=q_type,
                    options=options,
                    timeout_ms=corroborate_timeout,
                    norm_a=norm_a,
                    norm_b=norm_b,
                    succ_a=succ_a,
                    succ_b=succ_b,
                    parsed_a=parsed_a,
                    parsed_b=parsed_b,
                    s_tier_soft_escalation_ms=s_tier_soft_ms,
                    language=language,
                    starter_code=starter_code,
                    max_tokens=max_tokens,
                )
                models_called.extend(fb_models_called)
                agreement_details["fallback"] = fb_details
                if hedged_sol is not None:
                    total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    hedged_sol.latency_ms = total_elapsed_ms
                    hedged_sol.models_called = models_called
                    hedged_sol.agreement_details = agreement_details
                    return hedged_sol
            except Exception:
                pass

            total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return SolverResponse(
                status="OK",
                confidence_tier="MEDIUM_TIEBREAKER_AGREE",
                canonical_key=surviving_norm.canonical_key,
                selected_index=surviving_norm.selected_index,
                selected_text=surviving_norm.selected_text,
                selected_indices=surviving_norm.selected_indices,
                selected_texts=surviving_norm.selected_texts,
                canonical_keys=surviving_norm.canonical_keys,
                permutation_indices=surviving_norm.permutation_indices,
                ordered_texts=surviving_norm.ordered_texts,
                reason=(surviving_parsed.reason if surviving_parsed else "") or "Surviving primary model rescue.",
                latency_ms=total_elapsed_ms,
                models_called=models_called,
                agreement_details=agreement_details,
            )

        total_elapsed_ms = (time.perf_counter() - t0) * 1000.0
        penalty = hard_budget_ms
        return SolverResponse(
            status="UNCERTAIN",
            confidence_tier="UNCERTAIN_NO_AGREEMENT",
            latency_ms=penalty,
            models_called=models_called,
            agreement_details=agreement_details,
            error="No consensus reached between models",
        )

    finally:
        if local_client:
            await client.aclose()
