"""QuizZy Model Selection and Dynamic Cascade Manager.

Provides centralized model selection, normalization, catalog retrieval,
and custom cascade re-ordering across Groq and OpenRouter providers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from backend.telemetry_db import get_system_setting, set_system_setting

logger = logging.getLogger("quizzy.model_manager")

SETTING_KEY_MODEL = "active_model_selection"
DEFAULT_MODEL = "auto"

# In-memory fast cache to minimize SQLite reads
_cached_active_model: Optional[str] = None

# Known canonical aliases
PRIMARY_A_ID = "primary_a"
PRIMARY_B_ID = "primary_b"
GROQ_ONLY_ID = "groq_only"
OPENROUTER_ONLY_ID = "openrouter_only"
AUTO_ID = "auto"


def normalize_model_identifier(raw: Optional[str]) -> str:
    """
    Deterministically normalize any model string, alias, or display label.
    Maps variants like 'Primary A', 'primary_a', 'Groq-only', 'Auto' to canonical IDs.
    Gracefully falls back to 'auto' for invalid or empty inputs without errors.
    """
    if raw is None:
        return AUTO_ID
    clean = str(raw).strip()
    if not clean:
        return AUTO_ID

    low = clean.lower().replace("-", "_").replace(" ", "_")

    # Automated
    if low in ("auto", "default", "intelligent_cascade", "all"):
        return AUTO_ID

    # Primary Profiles
    if low in ("primary_a", "primarya", "pri_a", "gpt_oss_120b", "openai/gpt_oss_120b") or "primary_a" in low:
        return PRIMARY_A_ID
    if low in ("primary_b", "primaryb", "pri_b", "qwen", "qwen3.8_27b", "qwen/qwen3.8_27b") or "primary_b" in low:
        return PRIMARY_B_ID

    # Provider Modes
    if low in ("groq_only", "groq", "all_groq", "groq_cascade"):
        return GROQ_ONLY_ID
    if low in ("openrouter_only", "openrouter", "all_openrouter", "openrouter_cascade"):
        return OPENROUTER_ONLY_ID

    # Specific Model Formats:
    # 1. groq:model_name or groq/model_name
    if clean.startswith("groq:"):
        return f"groq:{clean[5:].strip()}"
    if clean.startswith("openrouter:"):
        return f"openrouter:{clean[11:].strip()}"

    # 2. Known models from Groq
    if any(k in clean.lower() for k in ("gpt-oss-120b", "qwen3.8", "gpt-oss-20b")):
        return f"groq:{clean}"

    # 3. Known models from OpenRouter
    if any(k in clean.lower() for k in ("nemotron", "gemma", "lfm", "free")):
        return f"openrouter:{clean}"

    # Return clean raw string if unrecognized specific model
    return clean


def get_active_model_selection(db_path: Optional[Union[str, Path]] = None) -> str:
    """
    Retrieve currently selected model setting.
    Checks environment override first, then in-memory cache, then SQLite persistent settings.
    Defaults to 'auto' with zero error propagation.
    """
    global _cached_active_model

    # 1. Environment variable override (useful for testing or dedicated worker containers)
    env_override = os.getenv("QUIZZY_ACTIVE_MODEL")
    if env_override and env_override.strip():
        return normalize_model_identifier(env_override)

    # 2. If in-memory cache populated and no explicit custom db_path requested
    if _cached_active_model is not None and db_path is None:
        return _cached_active_model

    # 3. Read from SQLite system_settings table
    try:
        val = get_system_setting(SETTING_KEY_MODEL, default=DEFAULT_MODEL, db_path=db_path)
        norm = normalize_model_identifier(val)
        if db_path is None:
            _cached_active_model = norm
        return norm
    except Exception as exc:
        logger.warning("Error reading active model selection: %s", exc)
        return DEFAULT_MODEL


def set_active_model_selection(
    model_identifier: str,
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    Persist new active model selection.
    Normalizes the identifier, updates in-memory cache, and writes to SQLite WAL table.
    Returns structured result with metadata and label.
    """
    global _cached_active_model

    norm = normalize_model_identifier(model_identifier)
    try:
        set_system_setting(SETTING_KEY_MODEL, norm, db_path=db_path)
    except Exception as exc:
        logger.warning("Failed to persist model setting to database: %s", exc)

    if db_path is None:
        _cached_active_model = norm

    label = get_model_display_label(norm)
    return {
        "status": "ok",
        "selected_model": norm,
        "selected_label": label,
    }


def reset_active_model_selection(db_path: Optional[Union[str, Path]] = None) -> None:
    """Reset active model selection back to default 'auto' (mainly for unit tests)."""
    global _cached_active_model
    _cached_active_model = DEFAULT_MODEL
    try:
        set_system_setting(SETTING_KEY_MODEL, DEFAULT_MODEL, db_path=db_path)
    except Exception:
        pass


def get_model_display_label(model_id: str) -> str:
    """Return user-friendly descriptive label for a given canonical model identifier."""
    if model_id == AUTO_ID:
        return "⚡ Auto (Intelligent Cascade)"
    if model_id == PRIMARY_A_ID:
        return "Primary A (openai/gpt-oss-120b - Groq)"
    if model_id == PRIMARY_B_ID:
        return "Primary B (qwen/qwen3.8-27b - Groq)"
    if model_id == GROQ_ONLY_ID:
        return "Groq Only (All Groq Cascade)"
    if model_id == OPENROUTER_ONLY_ID:
        return "OpenRouter Only (All OpenRouter Cascade)"

    if model_id.startswith("groq:"):
        return f"Groq • {model_id[5:]}"
    if model_id.startswith("openrouter:"):
        return f"OpenRouter • {model_id[11:]}"

    return model_id


def get_available_models_catalog(config: Optional[dict] = None) -> Dict[str, Any]:
    """
    Generate grouped catalog of all available model options for UI select elements.
    Includes Auto, Primary Profiles, Provider Modes, and Specific Models from models.json.
    """
    pri_a_model = "openai/gpt-oss-120b"
    pri_b_model = "qwen/qwen3.8-27b"
    groq_models = [pri_a_model, pri_b_model, "openai/gpt-oss-20b"]
    or_models = [
        "nvidia/nemotron-3.5-super-vl:free",
        "nvidia/nemotron-3.5-lightning:free",
        "google/gemma-4-31b-it:free",
        "liquid/lfm-2.5-2.6b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "google/gemma-4-26b-a4b-it:free",
    ]

    if config and isinstance(config, dict):
        if "primary_a" in config and isinstance(config["primary_a"], dict):
            pri_a_model = config["primary_a"].get("model", pri_a_model)
        if "primary_b" in config and isinstance(config["primary_b"], dict):
            pri_b_model = config["primary_b"].get("model", pri_b_model)

        # Extract Groq and OpenRouter models from cascade and fallback pools
        cascade = config.get("text_cascade") or []
        for item in cascade:
            prov = str(item.get("provider", "")).lower()
            m = item.get("model")
            if m:
                if prov == "groq" and m not in groq_models:
                    groq_models.append(m)
                elif prov == "openrouter" and m not in or_models:
                    or_models.append(m)

        for pool_item in (config.get("fallback_pool") or []):
            if isinstance(pool_item, dict):
                m = pool_item.get("model")
                prov = str(pool_item.get("provider", "")).lower()
                if m:
                    if prov == "groq" and m not in groq_models:
                        groq_models.append(m)
                    elif prov == "openrouter" and m not in or_models:
                        or_models.append(m)

    catalog = {
        "groups": [
            {
                "label": "Automated",
                "options": [
                    {
                        "id": AUTO_ID,
                        "label": "⚡ Auto (Intelligent Cascade)",
                        "description": "Smart multi-tier fallback cascade handling all subjects without errors",
                    }
                ],
            },
            {
                "label": "Primary Profiles",
                "options": [
                    {
                        "id": PRIMARY_A_ID,
                        "label": f"Primary A ({pri_a_model})",
                        "provider": "groq",
                        "model": pri_a_model,
                        "description": "High-accuracy S-Tier Groq flagship solver",
                    },
                    {
                        "id": PRIMARY_B_ID,
                        "label": f"Primary B ({pri_b_model})",
                        "provider": "groq",
                        "model": pri_b_model,
                        "description": "High-reasoning S-Tier Groq alternative solver",
                    },
                ],
            },
            {
                "label": "Provider Modes",
                "options": [
                    {
                        "id": GROQ_ONLY_ID,
                        "label": "Groq Only (All Groq Cascade)",
                        "description": "Strictly restricts solving to ultra-fast Groq models",
                    },
                    {
                        "id": OPENROUTER_ONLY_ID,
                        "label": "OpenRouter Only (All OpenRouter Cascade)",
                        "description": "Strictly restricts solving to OpenRouter multi-key pool",
                    },
                ],
            },
            {
                "label": "Specific Groq Models",
                "options": [
                    {
                        "id": f"groq:{m}",
                        "label": f"Groq: {m}",
                        "provider": "groq",
                        "model": m,
                    }
                    for m in groq_models
                ],
            },
            {
                "label": "Specific OpenRouter Models",
                "options": [
                    {
                        "id": f"openrouter:{m}",
                        "label": f"OpenRouter: {m}",
                        "provider": "openrouter",
                        "model": m,
                    }
                    for m in or_models
                ],
            },
        ],
    }
    return catalog


def build_custom_text_cascade(
    selected_model: Optional[str],
    base_cascade: List[Dict[str, Any]],
    config: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """
    Construct tailored text cascade list based on active model selection.
    Ensures targeted models are attempted first while preserving fail-safe
    cooldown/fallback capabilities so requests never fail unhandled.
    """
    norm = normalize_model_identifier(selected_model)
    if not base_cascade:
        base_cascade = []

    # 1. AUTO: Return full default cascade unchanged
    if norm == AUTO_ID:
        return list(base_cascade)

    # 2. PRIMARY A: Prioritize Primary A model at position 0
    if norm == PRIMARY_A_ID:
        pri_a_model = "openai/gpt-oss-120b"
        if config and "primary_a" in config:
            pri_a_model = config["primary_a"].get("model", pri_a_model)

        target = None
        others = []
        for item in base_cascade:
            if item.get("provider", "").lower() == "groq" and item.get("model") == pri_a_model:
                target = dict(item)
            else:
                others.append(dict(item))

        if not target:
            target = {"provider": "groq", "model": pri_a_model, "tier": "S", "priority": 1}

        target["priority"] = 1
        for idx, it in enumerate(others, start=2):
            it["priority"] = idx
        return [target] + others

    # 3. PRIMARY B: Prioritize Primary B model at position 0
    if norm == PRIMARY_B_ID:
        pri_b_model = "qwen/qwen3.8-27b"
        if config and "primary_b" in config:
            pri_b_model = config["primary_b"].get("model", pri_b_model)

        target = None
        others = []
        for item in base_cascade:
            if item.get("provider", "").lower() == "groq" and item.get("model") == pri_b_model:
                target = dict(item)
            else:
                others.append(dict(item))

        if not target:
            target = {"provider": "groq", "model": pri_b_model, "tier": "S", "priority": 1}

        target["priority"] = 1
        for idx, it in enumerate(others, start=2):
            it["priority"] = idx
        return [target] + others

    # 4. GROQ ONLY: Exclude any non-Groq models completely
    if norm == GROQ_ONLY_ID:
        groq_items = [dict(it) for it in base_cascade if it.get("provider", "").lower() == "groq"]
        if not groq_items:
            # Fallback to standard Groq trio if base cascade had no groq models
            groq_items = [
                {"provider": "groq", "model": "openai/gpt-oss-120b", "priority": 1},
                {"provider": "groq", "model": "qwen/qwen3.8-27b", "priority": 2},
                {"provider": "groq", "model": "openai/gpt-oss-20b", "priority": 3},
            ]
        for idx, it in enumerate(groq_items, start=1):
            it["priority"] = idx
        return groq_items

    # 5. OPENROUTER ONLY: Exclude any non-OpenRouter models completely
    if norm == OPENROUTER_ONLY_ID:
        or_items = [dict(it) for it in base_cascade if it.get("provider", "").lower() == "openrouter"]
        if config and config.get("fallback_pool"):
            existing_models = {it.get("model") for it in or_items}
            for pool_it in config["fallback_pool"]:
                if isinstance(pool_it, dict) and pool_it.get("model") not in existing_models:
                    or_items.append(dict(pool_it))
                    existing_models.add(pool_it.get("model"))
        if not or_items:
            or_items = [
                {"provider": "openrouter", "model": "nvidia/nemotron-3.5-super-vl:free", "priority": 1},
                {"provider": "openrouter", "model": "google/gemma-4-31b-it:free", "priority": 2},
                {"provider": "openrouter", "model": "liquid/lfm-2.5-2.6b:free", "priority": 3},
            ]
        for idx, it in enumerate(or_items, start=1):
            it["priority"] = idx
        return or_items

    # 6. SPECIFIC MODEL (e.g. groq:openai/gpt-oss-20b or openrouter:google/gemma-4-31b-it:free)
    target_provider = "groq"
    target_model = norm
    if norm.startswith("groq:"):
        target_provider = "groq"
        target_model = norm[5:]
    elif norm.startswith("openrouter:"):
        target_provider = "openrouter"
        target_model = norm[11:]
    elif "/" in norm:
        # Determine provider by model path or lookup in config
        if any(k in norm for k in ("gpt-oss", "qwen")):
            target_provider = "groq"
        else:
            target_provider = "openrouter"

    target_item = {"provider": target_provider, "model": target_model, "priority": 1}

    # Add fallback models after the specific model to preserve error resilience
    backup_items = []
    for it in base_cascade:
        if it.get("model") != target_model:
            backup_items.append(dict(it))

    result = [target_item] + backup_items
    for idx, it in enumerate(result, start=1):
        it["priority"] = idx
    return result

