"""API Key lifecycle, rate-limit cooldowns, quarantine, and model pool catalog."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from dotenv import load_dotenv

import hashlib

# Non-chat model exclusion keywords (embedding, reranker, content-safety, guard, whisper, tts)
NON_CHAT_KEYWORDS = ("embed", "rerank", "safety", "guard", "whisper", "tts")

_key_quarantine_until: Dict[str, float] = {}
_key_model_quarantine_until: Dict[Tuple[str, str], float] = {}
_rate_limit_headers_cache: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
_last_model_used_time: Dict[Tuple[str, str, str], float] = {}
_model_quarantine_until: Dict[str, float] = {}
_provider_cooldown_until: Dict[str, float] = {}
_model_cooldown_until: Dict[Tuple[str, str], float] = {}
_openrouter_key_rr_idx: int = 0
_groq_key_rr_idx: int = 0
_gemini_key_rr_idx: int = 0
_gemini_key_cooldown_until: Dict[str, float] = {}
_gemini_key_inflight: Dict[str, int] = {}
_gemini_key_stats: Dict[str, Dict[str, Any]] = {}

GROQ_PRIORITY_BUCKETS: List[Dict[str, Any]] = [
    {"provider": "groq", "model": "openai/gpt-oss-120b", "family": "gpt-oss", "tier": "S", "priority": 1},
    {"provider": "groq", "model": "llama-3.3-70b-versatile", "family": "llama", "tier": "S", "priority": 2},
    {"provider": "groq", "model": "openai/gpt-oss-20b", "family": "gpt-oss", "tier": "A", "priority": 3},
    {"provider": "groq", "model": "qwen/qwen3.8-27b", "family": "qwen", "tier": "S", "priority": 4},
    {"provider": "groq", "model": "meta-llama/llama-4-scout-17b-16e-instruct", "family": "llama", "tier": "A", "priority": 5},
]

TIER_WEIGHT: Dict[str, int] = {"S": 1, "A": 2, "B": 3}

DEFAULT_TIER_PRIORITY: Dict[str, Tuple[str, int]] = {
    "nvidia/nemotron-3-ultra-550b-a55b:free": ("S", 1),
    "nvidia/nemotron-3-super-120b-a12b:free": ("S", 2),
    "google/gemma-4-31b-it:free": ("S", 3),
    "google/gemma-4-26b-a4b-it:free": ("A", 4),
    "nvidia/nemotron-3.5-lightning:free": ("B", 5),
    "liquid/lfm-2.5-2.6b:free": ("B", 6),
}


def find_env_file() -> Path:
    """Find backend/.env or root .env file."""
    candidates = [
        Path.cwd() / "backend" / ".env",
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def set_model_cooldown(provider: str, model_id: str, duration_sec: float = 12.0) -> None:
    """Set rate-limit cooldown for a specific (provider, model_id) without affecting other models."""
    if not provider or not model_id:
        return
    _model_cooldown_until[(provider.lower(), model_id.lower())] = time.time() + duration_sec


def is_model_in_cooldown(provider: str, model_id: str) -> bool:
    """Check if a specific (provider, model_id) is currently on rate-limit cooldown."""
    if not provider or not model_id:
        return False
    until = _model_cooldown_until.get((provider.lower(), model_id.lower()), 0.0)
    return time.time() < until


def get_model_cooldown_remaining(provider: str, model_id: str) -> float:
    """Return remaining cooldown seconds for a specific (provider, model_id), or 0.0 if not in cooldown."""
    if not provider or not model_id:
        return 0.0
    until = _model_cooldown_until.get((provider.lower(), model_id.lower()), 0.0)
    return max(0.0, until - time.time())


def reset_model_cooldown(provider: Optional[str] = None, model: Optional[str] = None) -> None:
    """Reset per-model cooldown map or a specific model entry (primarily for unit tests)."""
    global _model_cooldown_until
    if provider and model:
        _model_cooldown_until.pop((provider.lower(), model.lower()), None)
    else:
        _model_cooldown_until.clear()


def set_provider_cooldown(provider: str, duration_sec: float = 30.0) -> None:
    """Set cooldown for an entire provider (e.g. OpenRouter 429 rate limit storm protection)."""
    if not provider:
        return
    _provider_cooldown_until[provider.lower()] = time.time() + duration_sec


def is_provider_in_cooldown(provider: str) -> bool:
    """Check if an entire provider is on cooldown."""
    if not provider:
        return False
    until = _provider_cooldown_until.get(provider.lower(), 0.0)
    return time.time() < until


def reset_provider_cooldown() -> None:
    """Reset provider cooldown map (primarily for unit tests)."""
    global _provider_cooldown_until, _model_cooldown_until
    _provider_cooldown_until.clear()
    _model_cooldown_until.clear()


def is_valid_chat_model(slug: str) -> bool:
    """Strictly reject any slug containing embed, rerank, safety, guard, whisper, or tts."""
    if not slug:
        return False
    slug_lower = slug.lower()
    return not any(kw in slug_lower for kw in NON_CHAT_KEYWORDS)


def quarantine_key(key: str, status_code: int) -> None:
    """
    Quarantine specific API key on failures:
    - HTTP 401, 402, 403: quarantine for 300 seconds.
    - HTTP 429: quarantine for 60 seconds for OpenRouter/general multi-key rotation,
      but NEVER quarantine Groq keys (gsk_...) because Groq rate limits are per-model.
    """
    if not key:
        return
    now = time.time()
    if status_code in (401, 402, 403):
        _key_quarantine_until[key] = now + 300.0
    elif status_code == 429:
        if not key.startswith("gsk_"):
            _key_quarantine_until[key] = now + 60.0


def is_key_quarantined(key: str) -> bool:
    if not key:
        return False
    until = _key_quarantine_until.get(key, 0.0)
    return time.time() < until


def reset_key_quarantine() -> None:
    """Reset key quarantine map (primarily for unit tests)."""
    global _key_quarantine_until, _openrouter_key_rr_idx, _model_cooldown_until
    _key_quarantine_until.clear()
    _openrouter_key_rr_idx = 0
    _model_cooldown_until.clear()
    reset_provider_cooldown()
    reset_key_model_quarantine()
    reset_gemini_key_cooldown()


def _key_hash(key: str) -> str:
    if not key:
        return "empty"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def set_key_model_quarantine(key: str, model_id: str, duration_sec: float = 12.0) -> None:
    """Quarantine a specific (key, model_id) pair without quarantining other models on that key."""
    if not key or not model_id:
        return
    k_hash = _key_hash(key)
    _key_model_quarantine_until[(k_hash, model_id.lower())] = time.time() + duration_sec
    if key.startswith("gsk_"):
        all_groq_keys = get_healthy_groq_keys()
        if all_groq_keys and len(all_groq_keys) > 1:
            all_exhausted = all(is_key_model_quarantined(k, model_id) for k in all_groq_keys)
            if all_exhausted:
                set_model_cooldown("groq", model_id, duration_sec)
        else:
            set_model_cooldown("groq", model_id, duration_sec)
    else:
        set_model_cooldown("openrouter", model_id, duration_sec)


def is_key_model_quarantined(key: str, model_id: str) -> bool:
    """Check if a specific (key, model_id) pair is currently quarantined."""
    if not key or not model_id:
        return False
    k_hash = _key_hash(key)
    until = _key_model_quarantine_until.get((k_hash, model_id.lower()), 0.0)
    return time.time() < until


def reset_key_model_quarantine() -> None:
    """Reset key-model quarantine and rate-limit tracking caches."""
    global _key_model_quarantine_until, _rate_limit_headers_cache, _last_model_used_time
    _key_model_quarantine_until.clear()
    _rate_limit_headers_cache.clear()
    _last_model_used_time.clear()


def record_rate_limit_headers(provider: str, key: str, model_id: str, headers: Any) -> None:
    """Record sliding-window token and request quota headers from HTTP responses."""
    if not provider or not model_id or not headers:
        return
    k_hash = _key_hash(key)
    now = time.time()

    rem_tokens = None
    rem_requests = None
    reset_tokens = None
    retry_after = None

    items = headers.items() if hasattr(headers, "items") else []
    for h_name, h_val in items:
        h_lower = str(h_name).lower()
        if h_lower == "x-ratelimit-remaining-tokens":
            try:
                rem_tokens = int(h_val)
            except (ValueError, TypeError):
                pass
        elif h_lower == "x-ratelimit-remaining-requests":
            try:
                rem_requests = int(h_val)
            except (ValueError, TypeError):
                pass
        elif h_lower == "x-ratelimit-reset-tokens":
            try:
                reset_tokens = float(h_val)
            except (ValueError, TypeError):
                reset_tokens = str(h_val)
        elif h_lower == "retry-after":
            try:
                retry_after = float(h_val)
            except (ValueError, TypeError):
                pass

    _rate_limit_headers_cache[(provider.lower(), k_hash, model_id.lower())] = {
        "timestamp": now,
        "remaining_tokens": rem_tokens,
        "remaining_requests": rem_requests,
        "reset_tokens": reset_tokens,
        "retry_after": retry_after,
    }


def record_model_usage(provider: str, key: str, model_id: str) -> None:
    """Record the timestamp when a model was called on a specific API key."""
    if not provider or not model_id:
        return
    k_hash = _key_hash(key)
    _last_model_used_time[(provider.lower(), k_hash, model_id.lower())] = time.time()


def is_model_bucket_healthy(provider: str, key: str, model_id: str) -> bool:
    """
    Check if a model bucket is healthy and eligible for proactive selection:
    - Not in quarantine or rate-limit cooldown
    - x-ratelimit-remaining-tokens >= 450
    - x-ratelimit-remaining-requests > 1
    - qwen/qwen3.8-27b not used in last 18.0s on this key
    """
    if not provider or not model_id:
        return False
    p_lower = provider.lower()
    m_lower = model_id.lower()
    k_hash = _key_hash(key)

    if is_model_in_cooldown(p_lower, m_lower):
        return False
    if is_key_model_quarantined(key, m_lower):
        return False
    if is_model_quarantined(m_lower):
        return False

    now = time.time()

    # qwen/qwen3.8-27b 18s spacing on Groq to prevent 1000 OTPM ceiling breaches
    if "qwen3.8" in m_lower and p_lower == "groq":
        last_used = _last_model_used_time.get((p_lower, k_hash, m_lower), 0.0)
        if (now - last_used) < 18.0:
            return False

    cached = _rate_limit_headers_cache.get((p_lower, k_hash, m_lower))
    if cached:
        cache_age = now - cached.get("timestamp", 0.0)
        if cache_age < 30.0:
            rem_t = cached.get("remaining_tokens")
            rem_r = cached.get("remaining_requests")
            if rem_t is not None and rem_t < 450:
                return False
            if rem_r is not None and rem_r <= 1:
                return False

    return True


def select_healthy_groq_pair(
    groq_key: str,
    exclude_models: Optional[Sequence[str]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Proactively select a healthy pair of Groq models (Primary A & B) from the prioritized multi-bucket pool:
      1. openai/gpt-oss-120b
      2. llama-3.3-70b-versatile
      3. openai/gpt-oss-20b
      4. qwen/qwen3.8-27b
      5. meta-llama/llama-4-scout-17b-16e-instruct
    """
    excluded = set(m.lower() for m in (exclude_models or []))
    candidates = [b for b in GROQ_PRIORITY_BUCKETS if b["model"].lower() not in excluded]

    healthy_candidates = [c for c in candidates if is_model_bucket_healthy("groq", groq_key, c["model"])]
    if not healthy_candidates:
        healthy_candidates = [
            c for c in candidates
            if not is_model_in_cooldown("groq", c["model"]) and not is_key_model_quarantined(groq_key, c["model"])
        ]
        if not healthy_candidates:
            healthy_candidates = list(candidates)

    primary_a = healthy_candidates[0]
    primary_b = None
    for cand in healthy_candidates[1:]:
        if cand.get("family") != primary_a.get("family"):
            primary_b = cand
            break
    if primary_b is None and len(healthy_candidates) > 1:
        primary_b = healthy_candidates[1]
    if primary_b is None:
        for cand in candidates:
            if cand["model"] != primary_a["model"]:
                primary_b = cand
                break
    if primary_b is None:
        primary_b = primary_a

    return primary_a, primary_b


def quarantine_model(model_slug: str, duration_sec: float = 60.0) -> None:
    """Deprioritize an OpenRouter model slug for 60 seconds on error or timeout."""
    if not model_slug:
        return
    _model_quarantine_until[model_slug] = time.time() + duration_sec


def is_model_quarantined(model_slug: str) -> bool:
    if not model_slug:
        return False
    until = _model_quarantine_until.get(model_slug, 0.0)
    return time.time() < until


def reset_model_quarantine() -> None:
    """Reset model quarantine map (primarily for unit tests)."""
    global _model_quarantine_until
    _model_quarantine_until.clear()


def _sanitize_key_str(raw: str) -> str:
    """Strip whitespace, quotes, and accidental list prefixes like '1. ', '2: ' from API keys."""
    if not raw:
        return ""
    cleaned = raw.strip().strip("'\"")
    cleaned = re.sub(r"^\d+[\.\:\-]\s*", "", cleaned)
    return cleaned.strip()


def load_groq_api_keys() -> List[str]:
    """Load all unique non-empty Groq keys from GROQ_API_KEYS, GROQ_API_KEY, and numbered vars."""
    if "GROQ_API_KEYS" not in os.environ and "GROQ_API_KEY" not in os.environ:
        env_path = find_env_file()
        if env_path.is_file():
            load_dotenv(dotenv_path=env_path)
        else:
            load_dotenv()

    keys: List[str] = []
    multi_raw = os.getenv("GROQ_API_KEYS", "")
    for k in multi_raw.split(","):
        k_clean = _sanitize_key_str(k)
        if k_clean and k_clean not in keys:
            keys.append(k_clean)

    single_raw = os.getenv("GROQ_API_KEY", "")
    for k in single_raw.split(","):
        k_clean = _sanitize_key_str(k)
        if k_clean and k_clean not in keys:
            keys.append(k_clean)

    for env_k, env_v in os.environ.items():
        if env_k.startswith("GROQ_API_KEY_"):
            v_clean = _sanitize_key_str(env_v)
            if v_clean and v_clean not in keys:
                keys.append(v_clean)

    return keys


def get_healthy_groq_keys() -> List[str]:
    """Retrieve all non-quarantined Groq API keys."""
    all_keys = load_groq_api_keys()
    if not all_keys:
        return []
    healthy = [k for k in all_keys if not is_key_quarantined(k)]
    return healthy if healthy else all_keys


def get_next_groq_key(exclude: Optional[str] = None, model: Optional[str] = None) -> str:
    """Return next round-robin healthy Groq API key, excluding a quarantined/exhausted key or model if requested."""
    global _groq_key_rr_idx
    healthy = get_healthy_groq_keys()
    if not healthy:
        return ""
    candidates = [k for k in healthy if k != exclude] if (exclude and len(healthy) > 1) else list(healthy)
    if model:
        m_lower = model.lower()
        model_healthy = [
            k for k in candidates
            if not is_key_model_quarantined(k, m_lower) and is_model_bucket_healthy("groq", k, m_lower)
        ]
        if not model_healthy:
            model_healthy = [k for k in candidates if not is_key_model_quarantined(k, m_lower)]
        if model_healthy:
            candidates = model_healthy

    idx = _groq_key_rr_idx % len(candidates)
    _groq_key_rr_idx += 1
    return candidates[idx]


def load_openrouter_api_keys() -> List[str]:
    """Load all unique non-empty OpenRouter keys from OPENROUTER_API_KEYS, OPENROUTER_API_KEY, and numbered vars."""
    if "OPENROUTER_API_KEYS" not in os.environ and "OPENROUTER_API_KEY" not in os.environ:
        env_path = find_env_file()
        if env_path.is_file():
            load_dotenv(dotenv_path=env_path)
        else:
            load_dotenv()

    keys: List[str] = []
    multi_raw = os.getenv("OPENROUTER_API_KEYS", "")
    for k in multi_raw.split(","):
        k_clean = k.strip()
        if k_clean and k_clean not in keys:
            keys.append(k_clean)

    single_raw = os.getenv("OPENROUTER_API_KEY", "")
    for k in single_raw.split(","):
        k_clean = k.strip()
        if k_clean and k_clean not in keys:
            keys.append(k_clean)

    for env_k, env_v in os.environ.items():
        if env_k.startswith("OPENROUTER_API_KEY_"):
            v_clean = env_v.strip()
            if v_clean and v_clean not in keys:
                keys.append(v_clean)

    return keys


def set_gemini_key_cooldown(key: str, duration_sec: float = 8.0, reason: str = "") -> None:
    """Set cooldown for a specific Gemini API key."""
    if not key:
        return
    _gemini_key_cooldown_until[key] = time.time() + duration_sec


def record_gemini_key_error(key: str, status_code: int, retry_after: Optional[float] = None) -> float:
    """
    Record error on a Gemini API key and apply granular cooldowns:
    - HTTP 429: Rate limit cooldown of 8.0s (or Retry-After header).
    - HTTP 503: Transient model load spike micro-cooldown of 1.5s.
    - HTTP 401, 402, 403: Quarantine key for 300.0s.
    - Other 5xx: 2.0s cooldown.
    Returns the cooldown duration in seconds.
    """
    if not key:
        return 0.0
    if status_code == 429:
        duration = float(retry_after) if (retry_after is not None and retry_after > 0) else 8.0
        set_gemini_key_cooldown(key, duration, reason="429_rate_limit")
    elif status_code == 503:
        duration = 1.5
        set_gemini_key_cooldown(key, duration, reason="503_transient_overload")
    elif status_code in (401, 402, 403):
        duration = 300.0
        quarantine_key(key, status_code)
        set_gemini_key_cooldown(key, duration, reason=f"auth_error_{status_code}")
    else:
        duration = 2.0
        set_gemini_key_cooldown(key, duration, reason=f"http_{status_code}")
    return duration


def is_gemini_key_available(key: str) -> bool:
    """Check if a Gemini key is healthy, unquarantined, and not in cooldown."""
    if not key:
        return False
    if is_key_quarantined(key):
        return False
    until = _gemini_key_cooldown_until.get(key, 0.0)
    return time.time() >= until


def increment_gemini_inflight(key: str) -> None:
    if key:
        _gemini_key_inflight[key] = _gemini_key_inflight.get(key, 0) + 1


def decrement_gemini_inflight(key: str) -> None:
    if key:
        curr = _gemini_key_inflight.get(key, 0)
        _gemini_key_inflight[key] = max(0, curr - 1)


def get_gemini_inflight(key: str) -> int:
    return _gemini_key_inflight.get(key, 0)


def reset_gemini_key_cooldown(key: Optional[str] = None) -> None:
    global _gemini_key_cooldown_until, _gemini_key_inflight, _gemini_key_stats, _gemini_key_rr_idx
    if not key or key == "all":
        _gemini_key_cooldown_until.clear()
        _gemini_key_inflight.clear()
        _gemini_key_stats.clear()
        _gemini_key_rr_idx = 0
    else:
        _gemini_key_cooldown_until.pop(key, None)
        _gemini_key_inflight.pop(key, None)
        for k in list(_gemini_key_cooldown_until.keys()):
            ident = get_key_identity("gemini", k)
            if ident.get("key_id") == key:
                _gemini_key_cooldown_until.pop(k, None)
                _gemini_key_inflight.pop(k, None)


def load_gemini_api_keys() -> List[str]:
    """Load all unique non-empty Gemini keys from GEMINI_API_KEYS, GEMINI_API_KEY, and numbered vars."""
    if "GEMINI_API_KEYS" not in os.environ and "GEMINI_API_KEY" not in os.environ:
        env_path = find_env_file()
        if env_path.is_file():
            load_dotenv(dotenv_path=env_path)
        else:
            load_dotenv()

    keys: List[str] = []
    # Primary key first
    single_raw = os.getenv("GEMINI_API_KEY", "")
    for k in single_raw.split(","):
        k_clean = _sanitize_key_str(k)
        if k_clean and k_clean not in keys:
            keys.append(k_clean)

    # Comma-separated pool
    multi_raw = os.getenv("GEMINI_API_KEYS", "")
    for k in multi_raw.split(","):
        k_clean = _sanitize_key_str(k)
        if k_clean and k_clean not in keys:
            keys.append(k_clean)

    # Numbered env vars GEMINI_API_KEY_1..N sorted
    numbered_keys: List[Tuple[int, str]] = []
    for env_k, env_v in os.environ.items():
        if env_k.startswith("GEMINI_API_KEY_"):
            suffix = env_k[len("GEMINI_API_KEY_"):]
            try:
                num = int(suffix)
            except ValueError:
                num = 999
            v_clean = _sanitize_key_str(env_v)
            if v_clean:
                numbered_keys.append((num, v_clean))
    numbered_keys.sort(key=lambda x: x[0])
    for _, k_val in numbered_keys:
        if k_val not in keys:
            keys.append(k_val)

    return keys


# Primary alias per specification
get_gemini_keys = load_gemini_api_keys


def get_healthy_gemini_keys() -> List[str]:
    """Retrieve all non-quarantined and non-cooldown Gemini API keys."""
    all_keys = load_gemini_api_keys()
    if not all_keys:
        return []
    healthy = [k for k in all_keys if is_gemini_key_available(k)]
    return healthy if healthy else all_keys


def get_next_gemini_key(exclude: Optional[str] = None, prioritize_least_inflight: bool = False) -> str:
    """
    Return next round-robin healthy Gemini API key, excluding a quarantined/cooldown/exhausted key if requested.
    If prioritize_least_inflight is True, chooses the available key with the lowest in-flight request count.
    """
    global _gemini_key_rr_idx
    healthy = get_healthy_gemini_keys()
    if not healthy:
        return ""
    candidates = [k for k in healthy if k != exclude] if (exclude and len(healthy) > 1) else healthy
    if not candidates:
        all_keys = load_gemini_api_keys()
        candidates = [k for k in all_keys if k != exclude] if (exclude and len(all_keys) > 1) else all_keys

    if prioritize_least_inflight and len(candidates) > 1:
        candidates = sorted(candidates, key=lambda k: get_gemini_inflight(k))
        return candidates[0]

    idx = _gemini_key_rr_idx % len(candidates)
    _gemini_key_rr_idx += 1
    return candidates[idx]


def get_healthy_openrouter_keys() -> List[str]:
    all_keys = load_openrouter_api_keys()
    if not all_keys:
        return []
    healthy = [k for k in all_keys if not is_key_quarantined(k)]
    return healthy if healthy else all_keys


def get_next_openrouter_key(exclude: Optional[str] = None) -> str:
    global _openrouter_key_rr_idx
    healthy = get_healthy_openrouter_keys()
    if not healthy:
        return ""
    candidates = [k for k in healthy if k != exclude] if (exclude and len(healthy) > 1) else healthy
    idx = _openrouter_key_rr_idx % len(candidates)
    _openrouter_key_rr_idx += 1
    return candidates[idx]


def extract_model_meta(item: Any) -> Dict[str, Any]:
    """Convert a model dict or string slug into normalized dict metadata."""
    if isinstance(item, dict):
        model = item.get("model", "")
        tier = item.get("tier")
        priority = item.get("priority")
        if not tier or priority is None:
            def_tier, def_prio = DEFAULT_TIER_PRIORITY.get(model, ("B", 99))
            tier = tier or def_tier
            priority = priority if priority is not None else def_prio
        return {
            "provider": item.get("provider", "openrouter"),
            "family": item.get("family", ""),
            "model": model,
            "tier": str(tier).upper(),
            "priority": int(priority),
        }
    elif isinstance(item, str):
        def_tier, def_prio = DEFAULT_TIER_PRIORITY.get(item, ("B", 99))
        return {
            "provider": "openrouter",
            "family": "",
            "model": item,
            "tier": def_tier,
            "priority": def_prio,
        }
    return {"provider": "openrouter", "family": "", "model": str(item), "tier": "B", "priority": 99}


def get_ordered_fallback_pool(config: Optional[Union[dict, list]] = None) -> List[Dict[str, Any]]:
    """
    Return candidate fallback models strictly ordered by capability tier and priority:
    (TIER_WEIGHT[tier], priority). Accepts config dict or candidate model list.
    """
    if config is None:
        try:
            config = load_models_config()
        except Exception:
            config = {}

    if isinstance(config, list):
        raw_pool = config
    else:
        raw_pool = config.get("fallback_pool")
        if not raw_pool:
            fb_m = config.get("fallback", {})
            if isinstance(fb_m, dict) and fb_m.get("model"):
                raw_pool = [fb_m]
            elif isinstance(fb_m, str):
                raw_pool = [fb_m]
            else:
                raw_pool = []

    parsed = [extract_model_meta(item) for item in raw_pool if item]
    valid = [item for item in parsed if is_valid_chat_model(item["model"])]
    valid.sort(key=lambda x: (TIER_WEIGHT.get(x.get("tier", "B").upper(), 99), x.get("priority", 99)))
    return valid


def get_active_fallback_pool(config: Optional[dict] = None) -> List[str]:
    """Return model slug strings in strict capability tier order."""
    ordered = get_ordered_fallback_pool(config)
    return [m["model"] for m in ordered]


def get_healthy_fallback_models(pool: Sequence[Any]) -> List[str]:
    """
    Return healthy (non-quarantined) models in strict tier order as a list of model slug strings.
    Accepts either list of model dicts or list of string slugs.
    """
    meta_list = [extract_model_meta(m) for m in pool if m]
    valid = [m for m in meta_list if is_valid_chat_model(m["model"])]
    valid.sort(key=lambda x: (TIER_WEIGHT.get(x.get("tier", "B").upper(), 99), x.get("priority", 99)))
    healthy = [m["model"] for m in valid if not is_model_quarantined(m["model"])]
    return healthy if healthy else [m["model"] for m in valid]


def load_api_keys() -> Tuple[str, str]:
    """Load primary GROQ_API_KEY and OPENROUTER_API_KEY (backward compatible)."""
    groq_keys = load_groq_api_keys()
    or_keys = load_openrouter_api_keys()
    groq_key = groq_keys[0] if groq_keys else ""
    openrouter_key = or_keys[0] if or_keys else ""
    return groq_key, openrouter_key


def load_models_config(config_path: Optional[str] = None) -> dict:
    """Load and parse models.json."""
    path = Path(config_path) if config_path else Path.cwd() / "models.json"
    if not path.is_file():
        alt = Path(__file__).resolve().parent.parent.parent / "models.json"
        if alt.is_file():
            path = alt
        else:
            raise FileNotFoundError(f"models.json not found at {path}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ==============================================================================
# Deterministic Key Identity, Masking & Rate Limit Parsing
# ==============================================================================
def get_key_identity(provider: str, raw_key: str, index: int = 0) -> Dict[str, Any]:
    """
    Generate deterministic masked identity and SHA256 key_id for an API key.
    Never exposes raw key strings in logs, SQLite, or dashboard payloads.
    """
    prov = str(provider or "").strip().lower()
    k = str(raw_key or "").strip()
    if not k:
        return {
            "key_id": "empty",
            "provider": prov,
            "key_label": f"{prov.upper()} Key #{index + 1}",
            "masked_key": "EMPTY",
        }
    actual_idx = index
    if actual_idx == 0 and k:
        try:
            if prov == "gemini":
                keys = load_gemini_api_keys()
                if k in keys:
                    actual_idx = keys.index(k)
            elif prov == "groq":
                keys = load_groq_api_keys()
                if k in keys:
                    actual_idx = keys.index(k)
            elif prov == "openrouter":
                keys = load_openrouter_api_keys()
                if k in keys:
                    actual_idx = keys.index(k)
        except Exception:
            pass

    key_id = hashlib.sha256(f"{prov}:{k}".encode("utf-8")).hexdigest()[:10]
    if len(k) <= 10:
        masked = k[:3] + "..." + k[-2:]
    else:
        masked = k[:6] + "..." + k[-4:]
    return {
        "key_id": key_id,
        "provider": prov,
        "key_label": f"{prov.upper()} Key #{actual_idx + 1}",
        "masked_key": masked,
    }


def get_all_configured_keys() -> List[Dict[str, Any]]:
    """
    Return ordered list of all configured API key identities across Gemini, Groq, and OpenRouter.
    Ensures every key in .env is immediately discoverable even if total requests == 0.
    """
    all_entries: List[Dict[str, Any]] = []
    seen_ids = set()

    # 1. Gemini
    for idx, k in enumerate(load_gemini_api_keys()):
        ident = get_key_identity("gemini", k, idx)
        if ident["key_id"] not in seen_ids:
            seen_ids.add(ident["key_id"])
            all_entries.append(ident)

    # 2. Groq
    for idx, k in enumerate(load_groq_api_keys()):
        ident = get_key_identity("groq", k, idx)
        if ident["key_id"] not in seen_ids:
            seen_ids.add(ident["key_id"])
            all_entries.append(ident)

    # 3. OpenRouter
    for idx, k in enumerate(load_openrouter_api_keys()):
        ident = get_key_identity("openrouter", k, idx)
        if ident["key_id"] not in seen_ids:
            seen_ids.add(ident["key_id"])
            all_entries.append(ident)

    return all_entries


def parse_reset_duration_seconds(raw_val: Any) -> float:
    """
    Parse rate-limit reset duration strings ('16h24m10.048s', '2m59.5s', '450ms', '12s', '15.2', or epoch timestamp)
    into a normalized duration in seconds.
    """
    if raw_val is None:
        return 0.0
    if isinstance(raw_val, (int, float)):
        val = float(raw_val)
        if val > 1_000_000_000:
            return max(0.0, val - time.time())
        return max(0.0, val)

    s = str(raw_val).strip()
    if not s:
        return 0.0

    try:
        val = float(s)
        if val > 1_000_000_000:
            return max(0.0, val - time.time())
        return max(0.0, val)
    except ValueError:
        pass

    total_sec = 0.0
    s_work = s

    # 1. Days
    d_match = re.search(r"(\d+(?:\.\d+)?)\s*d(?:ay?s?)?(?![a-zA-Z])", s_work, re.IGNORECASE)
    if d_match:
        total_sec += float(d_match.group(1)) * 86400.0
        s_work = s_work[:d_match.start()] + " " + s_work[d_match.end():]

    # 2. Hours
    h_match = re.search(r"(\d+(?:\.\d+)?)\s*h(?:(?:ou)?r?s?)?(?![a-zA-Z])", s_work, re.IGNORECASE)
    if h_match:
        total_sec += float(h_match.group(1)) * 3600.0
        s_work = s_work[:h_match.start()] + " " + s_work[h_match.end():]

    # 3. Milliseconds (check first before 's')
    ms_match = re.search(r"(\d+(?:\.\d+)?)\s*ms(?![a-zA-Z])", s_work, re.IGNORECASE)
    if ms_match:
        total_sec += float(ms_match.group(1)) / 1000.0
        s_work = s_work[:ms_match.start()] + " " + s_work[ms_match.end():]

    # 4. Minutes
    m_match = re.search(r"(\d+(?:\.\d+)?)\s*m(?:in(?:ute)?s?)?(?![a-zA-Z])", s_work, re.IGNORECASE)
    if m_match:
        total_sec += float(m_match.group(1)) * 60.0
        s_work = s_work[:m_match.start()] + " " + s_work[m_match.end():]

    # 5. Seconds
    s_match = re.search(r"(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?(?![a-zA-Z])", s_work, re.IGNORECASE)
    if s_match:
        total_sec += float(s_match.group(1))

    return max(0.0, total_sec)


def get_key_cooldown_remaining(key: str, provider: Optional[str] = None) -> float:
    """Return in-memory remaining cooldown seconds for an API key."""
    if not key:
        return 0.0
    now = time.time()
    until = 0.0
    prov = (provider or "").lower()
    if prov == "gemini" or key in _gemini_key_cooldown_until:
        until = max(until, _gemini_key_cooldown_until.get(key, 0.0))
    if key in _key_quarantine_until:
        until = max(until, _key_quarantine_until.get(key, 0.0))
    return max(0.0, until - now)


