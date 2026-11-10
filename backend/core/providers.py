"""Provider client adapters, completions with retry, vision/SVG preprocessors, and catalog verification."""

from __future__ import annotations

import asyncio
import base64
import math
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote
import xml.etree.ElementTree as ET

import httpx
from pydantic import BaseModel

from backend.core.key_pool import (
    extract_model_meta,
    get_healthy_groq_keys,
    get_model_cooldown_remaining,
    get_next_groq_key,
    get_ordered_fallback_pool,
    is_key_model_quarantined,
    is_valid_chat_model,
    load_api_keys,
    load_models_config,
    quarantine_key,
    quarantine_model,
    record_model_usage,
    record_rate_limit_headers,
    reset_model_cooldown,
    set_key_model_quarantine,
    set_model_cooldown,
    set_provider_cooldown,
)
from backend.core.normalizer import (
    NormalizationError,
    NormalizedAnswer,
    normalize_answer,
)
from backend.core.parser import ParseError, ParsedLLMResponse, parse_llm_response


def _dispatch(name: str, fallback_fn: Any) -> Any:
    """Dynamically resolve function from backend.core.llm_client to honour test monkey patches."""
    mod = sys.modules.get("backend.core.llm_client")
    if mod and hasattr(mod, name):
        return getattr(mod, name)
    return fallback_fn


class ModelResponse(BaseModel):
    """Structured LLM response for primary and judge completions."""
    answer: Any
    reason: str = ""
    raw_text: str = ""
    norm: Optional[Any] = None


_shared_client_pool: Dict[Any, httpx.AsyncClient] = {}


def get_shared_async_client() -> httpx.AsyncClient:
    """
    Returns a reusable per-event-loop httpx.AsyncClient with persistent keep-alive pool.
    Re-uses TCP/TLS connections to avoid 150-300ms handshake overhead on every call.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        client = _shared_client_pool.get(loop)
        if client is not None and not getattr(client, "is_closed", False):
            return client
        client = httpx.AsyncClient(
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
            timeout=httpx.Timeout(connect=2.0, read=3.5, write=2.0, pool=2.0),
        )
        _shared_client_pool[loop] = client
        return client

    return httpx.AsyncClient(
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
        timeout=httpx.Timeout(connect=2.0, read=3.5, write=2.0, pool=2.0),
    )


async def call_single_completion(
    client: httpx.AsyncClient,
    provider: str,
    model: str,
    messages: List[dict],
    api_key: str,
    timeout_ms: float,
    max_tokens: int = 1536,
) -> Tuple[Optional[str], float, Optional[str]]:
    """
    Call a single LLM provider chat completions endpoint with timeout.
    Uses max_tokens (default 1536, 256 for pings) and falls back to reasoning fields if content is empty.
    Returns: (raw_content, elapsed_ms, error_string)
    """
    if not api_key or not str(api_key).strip():
        return None, 0.0, "MISSING_API_KEY"

    timeout_sec = min(6.5, max(0.1, timeout_ms / 1000.0))
    t0 = time.perf_counter()

    effective_model = model
    if provider == "groq" and model in (
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "meta-llama/llama-4-maverick-17b-128e-instruct",
        "llama-3.2-11b-vision-preview",
    ):
        effective_model = "qwen/qwen3.8-27b"

    is_reasoning_model = any(k in effective_model.lower() for k in ("gpt-oss", "qwen", "nemotron"))
    has_image_content = any(isinstance(m.get("content"), list) for m in messages)
    if "qwen3.8" in effective_model.lower() and provider == "groq":
        # Groq enforces a strict 1000 OTPM limit on qwen/qwen3.8-27b; cap max_tokens at 320 for non-coding text, 480 for vision
        if has_image_content:
            effective_max_tokens = min(max_tokens, 480)
        elif max_tokens > 1536:
            effective_max_tokens = 768
        else:
            effective_max_tokens = min(max_tokens, 320)
    elif is_reasoning_model:
        effective_max_tokens = max(max_tokens, 2048) if ("qwen" in effective_model.lower() or "nemotron" in effective_model.lower() or max_tokens > 1024) else max(max_tokens, 1024)
    else:
        effective_max_tokens = max_tokens

    is_multimodal = (
        (provider == "gemini")
        or (provider == "openrouter" and any(k in effective_model.lower() for k in ("-vl", "vision", "vl:", "multimodal", "gemma-3", "gemma-4", "nano-omni", "inkling")))
        or (provider == "groq" and any(k in effective_model.lower() for k in ("vision", "scout", "maverick", "qwen")))
    )

    if has_image_content and is_multimodal:
        timeout_sec = min(10.0, max(8.0, timeout_ms / 1000.0))

    effective_messages = []
    for msg in messages:
        c = msg.get("content")
        if isinstance(c, list) and not is_multimodal:
            text_parts = [
                str(part.get("text", "")) for part in c
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            effective_messages.append({
                "role": msg.get("role", "user"),
                "content": "\n".join(text_parts) if text_parts else str(c)
            })
        else:
            effective_messages.append(msg)

    if provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": effective_model,
            "messages": effective_messages,
            "temperature": 0.0,
            "max_tokens": effective_max_tokens,
        }
    elif provider == "openrouter":
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/quizzy-eval",
            "X-Title": "QuizZy Evaluator",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": effective_messages,
            "temperature": 0.0,
            "max_tokens": effective_max_tokens,
        }
    elif provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": effective_messages,
            "temperature": 0.0,
            "max_tokens": effective_max_tokens,
        }
    else:
        return None, 0.0, f"Unsupported provider: {provider}"

    _q_mod = _dispatch("quarantine_model", quarantine_model)
    _q_key = _dispatch("quarantine_key", quarantine_key)
    _set_m_cd = _dispatch("set_model_cooldown", set_model_cooldown)
    _set_p_cd = _dispatch("set_provider_cooldown", set_provider_cooldown)
    _rec_usage = _dispatch("record_model_usage", record_model_usage)
    _rec_headers = _dispatch("record_rate_limit_headers", record_rate_limit_headers)
    _set_km_q = _dispatch("set_key_model_quarantine", set_key_model_quarantine)

    try:
        _rec_usage(provider, api_key, effective_model)
        response = await asyncio.wait_for(
            client.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout_sec,
            ),
            timeout=max(0.5, timeout_sec + 1.0),
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        _rec_headers(provider, api_key, effective_model, getattr(response, "headers", {}))

        if response.status_code == 200:
            data = response.json()
            choices = data.get("choices", [])
            if choices and "message" in choices[0]:
                msg = choices[0]["message"]
                content = msg.get("content") or ""
                if not str(content).strip():
                    content = msg.get("reasoning") or msg.get("reasoning_content") or ""
                if str(content).strip():
                    return str(content), elapsed_ms, None
                if provider == "openrouter":
                    _q_mod(model, 60.0)
                return None, elapsed_ms, "EMPTY_CHOICES_CONTENT"
            if provider == "openrouter":
                _q_mod(model, 60.0)
            return None, elapsed_ms, "EMPTY_CHOICES"
        elif response.status_code == 429:
            retry_after_hdr = response.headers.get("retry-after")
            try:
                retry_after_sec = float(retry_after_hdr) if retry_after_hdr else 12.0
            except ValueError:
                retry_after_sec = 12.0

            if provider == "groq":
                is_daily_limit = retry_after_sec > 30.0 or any(
                    k in response.text.lower() for k in ("tokens per day", "(tpd)", "requests per day", "(rpd)")
                )
                duration = min(retry_after_sec, 600.0) if is_daily_limit else max(12.0, retry_after_sec)
                _set_km_q(api_key, model, duration)
                groq_keys = _healthy_gk_fn()
                if not groq_keys or all(is_key_model_quarantined(k, model) for k in groq_keys):
                    _set_m_cd("groq", model, duration)

                # Fast < 5ms fallback to alternate model bucket in Groq
                fallback_map = {
                    "openai/gpt-oss-120b": "llama-3.3-70b-versatile",
                    "llama-3.3-70b-versatile": "openai/gpt-oss-20b",
                    "openai/gpt-oss-20b": "qwen/qwen3.8-27b",
                    "qwen/qwen3.8-27b": "openai/gpt-oss-120b",
                }
                alt_model = fallback_map.get(model)
                rem_timeout = max(0.1, timeout_sec - (time.perf_counter() - t0))
                if alt_model and rem_timeout > 0.2:
                    alt_payload = dict(payload)
                    alt_payload["model"] = alt_model
                    if "qwen" in alt_model.lower():
                        alt_payload["max_tokens"] = 320 if max_tokens <= 1024 else 480
                    elif "gpt-oss" in alt_model.lower():
                        alt_payload["max_tokens"] = max(effective_max_tokens, 1024)
                    try:
                        alt_resp = await asyncio.wait_for(
                            client.post(url, headers=headers, json=alt_payload, timeout=rem_timeout),
                            timeout=max(0.4, rem_timeout + 0.5),
                        )
                        if alt_resp.status_code == 200:
                            alt_data = alt_resp.json()
                            alt_choices = alt_data.get("choices", [])
                            if alt_choices and "message" in alt_choices[0]:
                                alt_msg = alt_choices[0]["message"]
                                alt_content = alt_msg.get("content") or alt_msg.get("reasoning") or alt_msg.get("reasoning_content") or ""
                                if str(alt_content).strip():
                                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                                    return str(alt_content), elapsed_ms, None
                    except Exception:
                        pass
            elif provider == "openrouter":
                duration = max(12.0, retry_after_sec)
                _set_km_q(api_key, model, duration)
                _set_m_cd("openrouter", model, duration)
                _q_mod(model, 30.0)
                is_provider_wide = any(k in response.text.lower() for k in ("account", "credits", "user rate limit", "insufficient"))
                if is_provider_wide:
                    _set_p_cd("openrouter", 8.0)
            elif provider == "gemini":
                _set_m_cd("gemini", model, min(retry_after_sec, 15.0))
            return None, elapsed_ms, "RATE_LIMIT_429"
        elif response.status_code in (401, 402, 403):
            _q_key(api_key, response.status_code)
            return None, elapsed_ms, f"HTTP_{response.status_code}: {response.text[:150]}"
        elif provider == "groq" and response.status_code in (400, 404, 429):
            if response.status_code == 404:
                _q_mod(model, 3600.0)
            fallback_map = {
                "qwen/qwen3-32b": "qwen/qwen3.8-27b",
                "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
                "meta-llama/llama-4-maverick-17b-128e-instruct": "qwen/qwen3.8-27b",
                "meta-llama/llama-4-scout-17b-16e-instruct": "qwen/qwen3.8-27b",
                "llama-3.2-11b-vision-preview": "qwen/qwen3.8-27b",
                "openai/gpt-oss-120b": "qwen/qwen3.8-27b",
                "qwen/qwen3.8-27b": "openai/gpt-oss-20b",
                "openai/gpt-oss-20b": "qwen/qwen3.8-27b",
            }
            alt_model = fallback_map.get(model)
            if alt_model and alt_model != model:
                rem_timeout = max(0.1, timeout_sec - (time.perf_counter() - t0))
                if rem_timeout > 0.15:
                    alt_payload = dict(payload)
                    alt_payload["model"] = alt_model
                    if "qwen" in alt_model.lower():
                        alt_payload["max_tokens"] = 480 if max_tokens <= 1536 else 768
                    elif "gpt-oss" in alt_model.lower():
                        alt_payload["max_tokens"] = max(effective_max_tokens, 1024)
                    alt_is_multimodal = any(k in alt_model.lower() for k in ("vision", "scout", "maverick", "qwen"))
                    if not alt_is_multimodal:
                        sanitized_msgs = []
                        for m in alt_payload.get("messages", []):
                            mc = m.get("content")
                            if isinstance(mc, list):
                                t_parts = [str(p.get("text", "")) for p in mc if isinstance(p, dict) and p.get("type") == "text"]
                                sanitized_msgs.append({"role": m.get("role", "user"), "content": "\n".join(t_parts) if t_parts else str(mc)})
                            else:
                                sanitized_msgs.append(m)
                        alt_payload["messages"] = sanitized_msgs
                    try:
                        alt_resp = await asyncio.wait_for(
                            client.post(url, headers=headers, json=alt_payload, timeout=rem_timeout),
                            timeout=max(0.4, rem_timeout + 0.8),
                        )
                        if alt_resp.status_code == 200:
                            alt_data = alt_resp.json()
                            alt_choices = alt_data.get("choices", [])
                            if alt_choices and "message" in alt_choices[0]:
                                alt_msg = alt_choices[0]["message"]
                                alt_content = alt_msg.get("content") or alt_msg.get("reasoning") or alt_msg.get("reasoning_content") or ""
                                if str(alt_content).strip():
                                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                                    return str(alt_content), elapsed_ms, None
                    except Exception:
                        pass
            return None, elapsed_ms, f"HTTP_{response.status_code}: {response.text[:150]}"
        elif provider == "openrouter":
            if response.status_code in (404, 429, 500, 502, 503, 504):
                _q_mod(model, 60.0)
            if response.status_code in (400, 404, 429, 500, 502, 503):
                or_fallback_map = {
                    "nvidia/nemotron-3-super-120b-a12b:free": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    "google/gemma-4-31b-it:free": "minimax/minimax-m3.1-flash:free",
                    "google/gemma-4-26b-a4b-it:free": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                    "liquid/lfm-2.5-2.6b:free": "minimax/minimax-m3.1-flash:free",
                }
                alt_model = or_fallback_map.get(model)
                if alt_model and alt_model != model:
                    rem_timeout = max(0.2, timeout_sec - (time.perf_counter() - t0))
                    if rem_timeout > 0.4:
                        alt_payload = dict(payload)
                        alt_payload["model"] = alt_model
                        try:
                            alt_resp = await client.post(url, headers=headers, json=alt_payload, timeout=rem_timeout)
                            if alt_resp.status_code == 200:
                                alt_data = alt_resp.json()
                                alt_choices = alt_data.get("choices", [])
                                if alt_choices and "message" in alt_choices[0]:
                                    alt_msg = alt_choices[0]["message"]
                                    alt_content = alt_msg.get("content") or alt_msg.get("reasoning") or alt_msg.get("reasoning_content") or ""
                                    if str(alt_content).strip():
                                        elapsed_ms = (time.perf_counter() - t0) * 1000.0
                                        return str(alt_content), elapsed_ms, None
                        except Exception:
                            pass
            return None, elapsed_ms, f"HTTP_{response.status_code}: {response.text[:150]}"
        else:
            return None, elapsed_ms, f"HTTP_{response.status_code}: {response.text[:150]}"

    except (httpx.TimeoutException, asyncio.TimeoutError):
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if provider == "openrouter":
            _q_mod(model, 60.0)
        return None, elapsed_ms, "TIMEOUT"
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if provider == "openrouter":
            _q_mod(model, 60.0)
        return None, elapsed_ms, f"NETWORK_ERROR_{type(exc).__name__}: {str(exc)}"


async def call_model_with_budget_retry(
    client: httpx.AsyncClient,
    provider: str,
    model: str,
    messages: List[dict],
    api_key: str,
    timeout_ms: float,
    q_type: str,
    options: List[str],
    max_tokens: int = 1536,
    language: Optional[str] = None,
    starter_code: Optional[str] = None,
) -> Tuple[Optional[str], Optional[ParsedLLMResponse], Optional[NormalizedAnswer], float, Optional[str]]:
    """
    Call model with budget-aware single fast retry on transient 5xx, network error,
    or ParseError (never on 429 or timeout) ONLY if elapsed time in current stage < 700 ms (100ms backoff).
    Returns: (raw_text, parsed_response, normalized_answer, elapsed_ms, error_string)
    """
    t_start = time.perf_counter()
    _call_fn = _dispatch("call_single_completion", call_single_completion)
    raw, el, err = await _call_fn(client, provider, model, messages, api_key, timeout_ms, max_tokens=max_tokens)

    parsed: Optional[ParsedLLMResponse] = None
    norm: Optional[NormalizedAnswer] = None

    if err:
        if "429" not in err and "TIMEOUT" not in err:
            elapsed_so_far = (time.perf_counter() - t_start) * 1000.0
            if elapsed_so_far < 700.0:
                await asyncio.sleep(0.1)
                rem_timeout = timeout_ms - ((time.perf_counter() - t_start) * 1000.0)
                if rem_timeout > 200.0:
                    raw, el2, err = await _call_fn(client, provider, model, messages, api_key, rem_timeout, max_tokens=max_tokens)

    if not err and raw:
        try:
            parsed = parse_llm_response(raw, options=options)
            norm = normalize_answer(q_type, parsed.answer, options, language=language, starter_code=starter_code)
        except (ParseError, NormalizationError) as pe:
            elapsed_so_far = (time.perf_counter() - t_start) * 1000.0
            if elapsed_so_far < 700.0:
                await asyncio.sleep(0.1)
                rem_timeout = timeout_ms - ((time.perf_counter() - t_start) * 1000.0)
                if rem_timeout > 200.0:
                    raw2, el2, err2 = await _call_fn(client, provider, model, messages, api_key, rem_timeout, max_tokens=max_tokens)
                    if not err2 and raw2:
                        try:
                            parsed = parse_llm_response(raw2, options=options)
                            norm = normalize_answer(q_type, parsed.answer, options, language=language, starter_code=starter_code)
                            raw = raw2
                            err = None
                        except (ParseError, NormalizationError) as pe2:
                            err = f"PARSE_OR_NORM_ERROR: {pe2}"
                    else:
                        err = err2 or "RETRY_EMPTY"
            else:
                err = f"PARSE_OR_NORM_ERROR: {pe}"

    total_elapsed = (time.perf_counter() - t_start) * 1000.0
    return raw, parsed, norm, total_elapsed, err


async def call_groq_primary(
    client: httpx.AsyncClient,
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    api_key: str,
    timeout_ms: float,
    q_type: str,
    options: List[str],
    language: Optional[str] = None,
    starter_code: Optional[str] = None,
    max_tokens: int = 1536,
) -> Tuple[Optional[ModelResponse], float, Optional[str]]:
    """
    Call primary Groq model. Returns (ModelResponse, elapsed_ms, error).
    Prioritizes primary_a (openai/gpt-oss-120b) by exhausting all healthy keys
    and token-bucket refilling before admitting defeat.
    """
    _budget_fn = _dispatch("call_model_with_budget_retry", call_model_with_budget_retry)
    _next_gk_fn = _dispatch("get_next_groq_key", get_next_groq_key)
    _healthy_gk_fn = _dispatch("get_healthy_groq_keys", get_healthy_groq_keys)
    _cd_rem_fn = _dispatch("get_model_cooldown_remaining", get_model_cooldown_remaining)
    _reset_cd_fn = _dispatch("reset_model_cooldown", reset_model_cooldown)

    if not api_key:
        api_key = _next_gk_fn()
    (raw, parsed, norm, el, err) = await _budget_fn(
        client, provider, model, messages, api_key, timeout_ms, q_type, options,
        max_tokens=max_tokens, language=language, starter_code=starter_code,
    )
    if err == "RATE_LIMIT_429":
        healthy_keys = _healthy_gk_fn()
        tried_keys = {api_key}
        for alt_key in healthy_keys:
            if alt_key in tried_keys:
                continue
            if is_key_model_quarantined(alt_key, model):
                continue
            tried_keys.add(alt_key)
            rem_timeout = max(200.0, timeout_ms - el)
            if rem_timeout < 300.0:
                break
            (raw, parsed, norm, el2, err) = await _budget_fn(
                client, provider, model, messages, alt_key, rem_timeout, q_type, options,
                max_tokens=max_tokens, language=language, starter_code=starter_code,
            )
            el += el2
            if not err and parsed:
                break

    if err == "RATE_LIMIT_429" and "120b" in model and _cd_rem_fn("groq", model) <= 5.0:
        rem_timeout = timeout_ms - el
        if rem_timeout >= 1200.0:
            await asyncio.sleep(0.8)
            refill_key = _next_gk_fn() or api_key
            rem_timeout_after_sleep = timeout_ms - el - 800.0
            (raw, parsed, norm, el3, err) = await _budget_fn(
                client, provider, model, messages, refill_key, rem_timeout_after_sleep, q_type, options,
                max_tokens=max_tokens, language=language, starter_code=starter_code,
            )
            el += (800.0 + el3)

    if err or not parsed:
        return None, el, err

    _reset_cd_fn("groq", model)
    return ModelResponse(answer=parsed.answer, reason=parsed.reason, raw_text=raw or "", norm=norm), el, None


async def download_image_as_base64_raster(image_url: str, client: Optional[httpx.AsyncClient] = None) -> Optional[str]:
    """Download image from an HTTP/HTTPS URL and convert to base64 raster data URL."""
    if not image_url or not str(image_url).strip():
        return None
    clean_url = str(image_url).strip()
    for _ in range(3):
        if "%" in clean_url:
            clean_url = unquote(clean_url)
        else:
            break
    if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
        return None
    close_client = False
    if client is None or getattr(client, "is_closed", False) is True:
        client = httpx.AsyncClient()
        close_client = True
    try:
        resp = await client.get(clean_url, timeout=5.0)
        if resp.status_code == 200:
            content_type = resp.headers.get("content-type", "").lower()
            if "svg" in content_type:
                b64_str = base64.b64encode(resp.content).decode("utf-8")
                return f"data:image/svg+xml;base64,{b64_str}"
            if "jpeg" in content_type or "jpg" in content_type:
                mime = "image/jpeg"
            elif "webp" in content_type:
                mime = "image/webp"
            elif "gif" in content_type:
                mime = "image/gif"
            else:
                mime = "image/png"
            b64_str = base64.b64encode(resp.content).decode("utf-8")
            return f"data:{mime};base64,{b64_str}"
    except Exception:
        pass
    finally:
        if close_client:
            await client.aclose()
    return None


def extract_svg_topology_from_xml(svg_xml: str) -> Optional[str]:
    """Parse SVG XML, extract nodes, edges, root, leaves, and calculate tree diameter."""
    if not svg_xml or not str(svg_xml).strip():
        return None

    clean_xml = str(svg_xml).strip()
    if clean_xml.startswith("data:image/svg"):
        if "," in clean_xml:
            prefix, data = clean_xml.split(",", 1)
            if "base64" in prefix.lower():
                try:
                    clean_xml = base64.b64decode(data).decode("utf-8", errors="replace")
                except Exception:
                    clean_xml = unquote(data)
            else:
                clean_xml = unquote(data)

    try:
        root_el = ET.fromstring(clean_xml)
    except Exception:
        m = re.search(r"<svg[\s\S]*?</svg>", clean_xml, re.IGNORECASE)
        if m:
            try:
                root_el = ET.fromstring(m.group(0))
            except Exception:
                return None
        else:
            return None

    shapes = []
    raw_labels = []
    lines = []

    for elem in root_el.iter():
        tag = elem.tag.split("}")[-1].lower()
        if tag == "circle":
            try:
                cx = float(elem.attrib.get("cx", 0))
                cy = float(elem.attrib.get("cy", 0))
                r = float(elem.attrib.get("r", 15))
                shapes.append({"type": "circle", "cx": cx, "cy": cy, "r": r})
            except (ValueError, TypeError):
                pass
        elif tag == "rect":
            try:
                w = float(elem.attrib.get("width", 0))
                h = float(elem.attrib.get("height", 0))
                x = float(elem.attrib.get("x", 0))
                y = float(elem.attrib.get("y", 0))
                if 10 <= w <= 300 and 10 <= h <= 300:
                    shapes.append({"type": "rect", "cx": x + w / 2, "cy": y + h / 2, "r": max(w, h) / 2})
            except (ValueError, TypeError):
                pass
        elif tag in ("text", "tspan"):
            txt = "".join(elem.itertext()).strip()
            if txt:
                try:
                    x = float(elem.attrib.get("x", 0))
                    y = float(elem.attrib.get("y", 0))
                    raw_labels.append({"text": txt, "x": x, "y": y})
                except (ValueError, TypeError):
                    pass
        elif tag == "line":
            try:
                x1 = float(elem.attrib.get("x1", 0))
                y1 = float(elem.attrib.get("y1", 0))
                x2 = float(elem.attrib.get("x2", 0))
                y2 = float(elem.attrib.get("y2", 0))
                lines.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
            except (ValueError, TypeError):
                pass
        elif tag == "path":
            d = elem.attrib.get("d", "")
            nums = re.findall(r"[-+]?[0-9]*\.?[0-9]+", d)
            if len(nums) >= 4:
                try:
                    x1 = float(nums[0])
                    y1 = float(nums[1])
                    x2 = float(nums[-2])
                    y2 = float(nums[-1])
                    if math.hypot(x2 - x1, y2 - y1) >= 15:
                        lines.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
                except (ValueError, TypeError):
                    pass

    if not raw_labels:
        return None

    filtered_labels = []
    for l in raw_labels:
        if not any(ex["text"] == l["text"] and math.hypot(ex["x"] - l["x"], ex["y"] - l["y"]) < 10 for ex in filtered_labels):
            filtered_labels.append(l)

    nodes = []
    for i, l in enumerate(filtered_labels):
        best_shape = None
        min_d = 45.0
        for s in shapes:
            d = math.hypot(s["cx"] - l["x"], s["cy"] - l["y"])
            if d < min_d:
                min_d = d
                best_shape = s
        nx = best_shape["cx"] if best_shape else l["x"]
        ny = best_shape["cy"] if best_shape else l["y"]
        nodes.append({"id": f"node_{i}", "label": l["text"], "x": round(nx), "y": round(ny)})

    nodes.sort(key=lambda n: (round(n["y"] / 15) * 15, n["x"]))

    edges = []
    edge_keys = set()
    adj: Dict[str, List[str]] = {n["label"]: [] for n in nodes}
    children_map: Dict[str, List[str]] = {n["label"]: [] for n in nodes}
    parent_map: Dict[str, str] = {}

    for l in lines:
        best_a = None
        min_da = 70.0
        best_b = None
        min_db = 70.0
        for n in nodes:
            da = math.hypot(n["x"] - l["x1"], n["y"] - l["y1"])
            if da < min_da:
                min_da = da
                best_a = n
            db = math.hypot(n["x"] - l["x2"], n["y"] - l["y2"])
            if db < min_db:
                min_db = db
                best_b = n

        if best_a and best_b and best_a["label"] != best_b["label"]:
            parent = best_a if best_a["y"] <= best_b["y"] else best_b
            child = best_b if best_a["y"] <= best_b["y"] else best_a
            ek = f"{parent['label']}->{child['label']}"
            if ek not in edge_keys:
                edge_keys.add(ek)
                is_left = child["x"] < parent["x"]
                direction = "left child" if is_left else "right child"
                edges.append({"parent": parent["label"], "child": child["label"], "dir": direction})
                adj[parent["label"]].append(child["label"])
                adj[child["label"]].append(parent["label"])
                children_map[parent["label"]].append(child["label"])
                parent_map[child["label"]] = parent["label"]

    all_labels = [n["label"] for n in nodes]
    root_candidates = [lbl for lbl in all_labels if lbl not in parent_map and len(children_map.get(lbl, [])) > 0]
    root = root_candidates[0] if root_candidates else (all_labels[0] if all_labels else "")
    leaves = [lbl for lbl in all_labels if len(children_map.get(lbl, [])) == 0]

    def find_path(start: str, target: str) -> Optional[List[str]]:
        if start == target:
            return [start]
        queue = [[start]]
        visited = {start}
        while queue:
            path = queue.pop(0)
            curr = path[-1]
            for neighbor in adj.get(curr, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    new_path = path + [neighbor]
                    if neighbor == target:
                        return new_path
                    queue.append(new_path)
        return None

    longest_path: List[str] = []
    for i in range(len(leaves)):
        for j in range(i + 1, len(leaves)):
            p = find_path(leaves[i], leaves[j])
            if p and len(p) > len(longest_path):
                longest_path = p

    if not longest_path:
        for i in range(len(all_labels)):
            for j in range(i + 1, len(all_labels)):
                p = find_path(all_labels[i], all_labels[j])
                if p and len(p) > len(longest_path):
                    longest_path = p

    diameter_edges = max(0, len(longest_path) - 1)
    diameter_nodes = len(longest_path)

    lines_out = [
        "[EXTRACTED DIAGRAM STRUCTURE]",
        f"SVG Tree/Graph Diagram with {len(nodes)} nodes:",
        "Nodes:",
    ]
    for n in nodes:
        lines_out.append(f"- Node \"{n['label']}\" at pos ({n['x']}, {n['y']})")
    if edges:
        lines_out.append("Edges:")
        for e in edges:
            lines_out.append(f"- Edge: Node \"{e['parent']}\" -> Node \"{e['child']}\" ({e['dir']})")
    lines_out.append("Graph Topology Analysis:")
    lines_out.append(f"- Root Node: \"{root}\"")
    lines_out.append(f"- Leaf Nodes: {', '.join(f'\"{l}\"' for l in leaves)}")
    if longest_path:
        lines_out.append(f"- Longest Path (Diameter): {' -> '.join(longest_path)} ({diameter_edges} edges, {diameter_nodes} nodes)")
        lines_out.append(f"- Tree Diameter (Number of Edges): {diameter_edges}")
        lines_out.append(f"- Tree Diameter (Number of Nodes on Longest Path): {diameter_nodes}")

    return "\n".join(lines_out)


async def check_models(config_path: Optional[str] = None) -> bool:
    """
    Verify model slugs and connectivity (--check-models):
    Tests primary_a, primary_b, and every model in fallback_pool.
    Filters non-chat models (embed/rerank/safety), benchmarks latency,
    and displays a non-destructive audit table. Never mutates models.json.
    """
    _load_cfg_fn = _dispatch("load_models_config", load_models_config)
    _load_keys_fn = _dispatch("load_api_keys", load_api_keys)
    _call_fn = _dispatch("call_single_completion", call_single_completion)

    config = _load_cfg_fn(config_path)
    groq_key, openrouter_key = _load_keys_fn()

    print("=" * 115)
    print("STARTING LIVE MODEL CATALOG & CONNECTIVITY VERIFICATION (NON-DESTRUCTIVE)")
    print("=" * 115)

    if not groq_key:
        print("[CHECK-MODELS] ERROR: GROQ_API_KEY is not set in backend/.env")
    if not openrouter_key:
        print("[CHECK-MODELS] ERROR: OPENROUTER_API_KEY is not set in backend/.env")

    if not groq_key or not openrouter_key:
        return False

    pri_a = config.get("primary_a", {})
    pri_b = config.get("primary_b", {})
    model_a = pri_a.get("model", "")
    model_b = pri_b.get("model", "")
    tier_a = pri_a.get("tier", "S")
    tier_b = pri_b.get("tier", "S")
    prio_a = pri_a.get("priority", 1)
    prio_b = pri_b.get("priority", 2)

    ordered_pool = get_ordered_fallback_pool(config)

    fam_a = pri_a.get("family", "").strip().lower()
    fam_b = pri_b.get("family", "").strip().lower()

    results: Dict[str, Dict[str, Any]] = {
        "primary_a": {"model": model_a, "provider": "groq", "tier": tier_a, "priority": prio_a, "pass": False, "reason": "", "latency_ms": 0.0},
        "primary_b": {"model": model_b, "provider": "groq", "tier": tier_b, "priority": prio_b, "pass": False, "reason": "", "latency_ms": 0.0},
    }

    arb = config.get("arbiter", {})
    model_arb = arb.get("model", "")
    tier_arb = arb.get("tier", "A")
    prio_arb = arb.get("priority", 3)
    if model_arb:
        results["arbiter"] = {"model": model_arb, "provider": arb.get("provider", "groq"), "tier": tier_arb, "priority": prio_arb, "pass": False, "reason": "", "latency_ms": 0.0}

    for item in ordered_pool:
        m_slug = item["model"]
        results[m_slug] = {
            "model": m_slug,
            "provider": item.get("provider", "openrouter"),
            "tier": item.get("tier", "B"),
            "priority": item.get("priority", 99),
            "pass": False,
            "reason": "",
            "latency_ms": 0.0,
        }

    ping_messages = [
        {"role": "user", "content": "Respond with the word PONG and nothing else."}
    ]

    async with httpx.AsyncClient() as client:
        # Check primary_a
        raw, el, err = await _call_fn(client, "groq", model_a, ping_messages, groq_key, timeout_ms=5000, max_tokens=256)
        results["primary_a"]["latency_ms"] = el
        if not err and raw and len(raw.strip()) > 0:
            results["primary_a"]["pass"] = True
            results["primary_a"]["reason"] = f"OK ({el:.0f}ms)"
        else:
            results["primary_a"]["pass"] = False
            results["primary_a"]["reason"] = err or "EMPTY_RESPONSE"

        # Check primary_b
        raw, el, err = await _call_fn(client, "groq", model_b, ping_messages, groq_key, timeout_ms=5000, max_tokens=256)
        results["primary_b"]["latency_ms"] = el
        if not err and raw and len(raw.strip()) > 0:
            results["primary_b"]["pass"] = True
            results["primary_b"]["reason"] = f"OK ({el:.0f}ms)"
        else:
            results["primary_b"]["pass"] = False
            results["primary_b"]["reason"] = err or "EMPTY_RESPONSE"

        # Check arbiter
        if model_arb:
            raw, el, err = await _call_fn(client, arb.get("provider", "groq"), model_arb, ping_messages, groq_key, timeout_ms=5000, max_tokens=256)
            results["arbiter"]["latency_ms"] = el
            if not err and raw and len(raw.strip()) > 0:
                results["arbiter"]["pass"] = True
                results["arbiter"]["reason"] = f"OK ({el:.0f}ms)"
            else:
                results["arbiter"]["pass"] = False
                results["arbiter"]["reason"] = err or "EMPTY_RESPONSE"

        # Query OpenRouter models catalog
        try:
            catalog_resp = await client.get("https://openrouter.ai/api/v1/models", timeout=6.0)
            catalog_models = set()
            if catalog_resp.status_code == 200:
                catalog_data = catalog_resp.json().get("data", [])
                catalog_models = {m.get("id") for m in catalog_data if m.get("id")}
        except Exception:
            catalog_models = set()

        for item in ordered_pool:
            m_slug = item["model"]
            if not is_valid_chat_model(m_slug):
                results[m_slug]["pass"] = False
                results[m_slug]["reason"] = "REJECTED_NON_CHAT"
                continue

            if catalog_models and m_slug not in catalog_models:
                results[m_slug]["pass"] = False
                results[m_slug]["reason"] = "NOT_IN_OPENROUTER_CATALOG"
                continue

            raw, el, err = await _call_fn(client, "openrouter", m_slug, ping_messages, openrouter_key, timeout_ms=5000, max_tokens=256)
            results[m_slug]["latency_ms"] = el
            if not err and raw and len(raw.strip()) > 0:
                results[m_slug]["pass"] = True
                results[m_slug]["reason"] = f"OK ({el:.0f}ms)"
            else:
                results[m_slug]["pass"] = False
                results[m_slug]["reason"] = err or "EMPTY_RESPONSE"

    print(f"{'SLOT/MODEL':<45} | {'TIER':<4} | {'PRIO':<4} | {'STATUS':<6} | {'LATENCY':<8} | {'DETAILS'}")
    print("-" * 115)
    for slot, r in results.items():
        st = "PASS" if r["pass"] else "FAIL"
        lat = f"{r['latency_ms']:.0f}ms" if r["latency_ms"] > 0 else "-"
        m_display = f"{slot} ({r['model']})" if slot in ("primary_a", "primary_b", "arbiter") else r["model"]
        print(f"{m_display:<45} | {r.get('tier', 'B'):<4} | {r.get('priority', 99):<4} | {st:<6} | {lat:<8} | {r['reason']}")
    print("=" * 115)

    all_passed = all(r["pass"] for r in results.values())
    return all_passed

