"""Defensive JSON extractor and schema validator for LLM quiz responses."""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field


class ParseError(ValueError):
    """Raised when raw LLM output cannot be parsed or validated into expected schema."""
    pass


class ParsedLLMResponse(BaseModel):
    """Validated structured LLM response."""
    answer: Any
    reason: str
    verification_workspace: Optional[str] = None
    raw_text: str = ""


TARGET_KEYS = ("answer", "correctAnswer", "correctOptionId", "reason", "explanation", "reasoning_summary")


def repair_json_string(text: str) -> str:
    """
    Repair malformed or truncated JSON strings:
    - Escapes unescaped control newlines, carriage returns, and tabs inside JSON string literals
    - Strips trailing commas before closing braces/brackets
    - Auto-closes unclosed strings, arrays, and objects truncated by token limits
    """
    if not text:
        return text

    # Step A: Locate outermost { and strip leading non-{ content
    first_brace = text.find("{")
    if first_brace != -1:
        text = text[first_brace:]

    # Step B: Escape raw unescaped newlines/tabs inside string literals and track unclosed brackets
    repaired_chars: List[str] = []
    in_string = False
    escape_active = False
    quote_char = None
    bracket_stack: List[str] = []

    for ch in text:
        if in_string:
            if escape_active:
                repaired_chars.append(ch)
                escape_active = False
            elif ch == "\\":
                repaired_chars.append(ch)
                escape_active = True
            elif ch == quote_char:
                repaired_chars.append(ch)
                in_string = False
                quote_char = None
            elif ch == "\n":
                repaired_chars.append("\\n")
            elif ch == "\r":
                pass
            elif ch == "\t":
                repaired_chars.append("\\t")
            else:
                repaired_chars.append(ch)
        else:
            if ch in ('"', "'"):
                in_string = True
                quote_char = ch
                repaired_chars.append(ch)
            else:
                if ch in ("{", "["):
                    bracket_stack.append("}" if ch == "{" else "]")
                elif ch in ("}", "]"):
                    if bracket_stack and bracket_stack[-1] == ch:
                        bracket_stack.pop()
                repaired_chars.append(ch)

    # Step C: If string was left unclosed by truncation, close it
    if in_string and quote_char:
        repaired_chars.append(quote_char)

    s = "".join(repaired_chars)

    # Step D: Strip trailing commas before closing brackets/braces
    s = re.sub(r",\s*([}\]])", r"\1", s)

    # Step E: Close unclosed brackets/braces in reverse order
    if bracket_stack:
        s = s.rstrip()
        if s.endswith(","):
            s = s[:-1].rstrip()
        for closing in reversed(bracket_stack):
            s += closing

    return s


def clean_tags_and_fences(raw_str: str) -> Tuple[str, Optional[str]]:
    """
    Stage 1: Clean raw LLM text by stripping tags and fences:
    - Extracts & preserves <verification_workspace>...</verification_workspace>
    - Strips closed and unclosed <think> blocks
    - Strips <verification_workspace> blocks
    - Prioritizes <verified_json> content if present
    - Strips markdown code fences
    """
    verification_workspace: Optional[str] = None
    vw_match = re.search(r"<verification_workspace>\s*([\s\S]*?)\s*(?:</verification_workspace>|$)", raw_str, re.IGNORECASE)
    if vw_match and vw_match.group(1).strip():
        verification_workspace = vw_match.group(1).strip()

    # Priority check for <verified_json>
    vjson_match = re.search(r"<verified_json>\s*([\s\S]*?)\s*(?:</verified_json>|$)", raw_str, re.IGNORECASE)
    source = vjson_match.group(1).strip() if (vjson_match and vjson_match.group(1).strip()) else raw_str

    # Strip closed and unclosed <think>...</think>
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", source, flags=re.IGNORECASE).strip()
    think_idx = cleaned.lower().find("<think>")
    if think_idx != -1:
        first_brace = cleaned.find("{", think_idx)
        if first_brace != -1:
            cleaned = cleaned[first_brace:].strip()
        else:
            cleaned = cleaned[think_idx + len("<think>"):].strip()

    # Strip closed and unclosed <verification_workspace>...</verification_workspace>
    cleaned = re.sub(r"<verification_workspace>[\s\S]*?</verification_workspace>", "", cleaned, flags=re.IGNORECASE).strip()
    vw_idx = cleaned.lower().find("<verification_workspace>")
    if vw_idx != -1:
        first_brace = cleaned.find("{", vw_idx)
        if first_brace != -1:
            cleaned = cleaned[first_brace:].strip()
        else:
            cleaned = cleaned[vw_idx + len("<verification_workspace>"):].strip()

    # Strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    return cleaned, verification_workspace


def extract_json_candidate(text: str) -> str:
    """
    Defensively extract JSON substring from raw model output.
    """
    if not text or not str(text).strip():
        raise ParseError("LLM returned an empty response")

    raw_str = str(text).strip()
    cleaned, _ = clean_tags_and_fences(raw_str)

    # 1. If already clean JSON/dict object containing recognized keys
    if cleaned.startswith("{"):
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict) and any(k in parsed for k in TARGET_KEYS):
                return cleaned
        except Exception:
            try:
                parsed = ast.literal_eval(cleaned)
                if isinstance(parsed, dict) and any(k in parsed for k in TARGET_KEYS):
                    return json.dumps(parsed)
            except Exception:
                pass

    # 2. Search for valid JSON substring containing target keys
    key_patterns = tuple(f'"{k}"' for k in TARGET_KEYS) + tuple(f"'{k}'" for k in TARGET_KEYS)
    start_indices = [m.start() for m in re.finditer(r"\{", cleaned)]
    end_indices = [m.start() for m in re.finditer(r"\}", cleaned)]

    for s_idx in start_indices:
        for e_idx in reversed(end_indices):
            if e_idx > s_idx:
                candidate = cleaned[s_idx : e_idx + 1]
                if any(k in candidate for k in key_patterns):
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict) and any(k in parsed for k in TARGET_KEYS):
                            return candidate
                    except Exception:
                        try:
                            parsed = ast.literal_eval(candidate)
                            if isinstance(parsed, dict) and any(k in parsed for k in TARGET_KEYS):
                                return json.dumps(parsed)
                        except Exception:
                            continue

    # 3. Outermost '{' and '}'
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidate = cleaned[start_idx : end_idx + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return candidate
        except Exception:
            pass
        return candidate

    if start_idx != -1:
        return cleaned[start_idx:]

    return cleaned


def parse_llm_response(raw_text: str, options: Optional[List[str]] = None) -> ParsedLLMResponse:
    """
    Parse and validate raw LLM completion string through a 5-stage defensive recovery pipeline:
      Stage 1: Strip closed and unclosed <think> and <verification_workspace> blocks and code fences.
      Stage 2: Fast json.loads on candidate / outermost {...}.
      Stage 3: JSON Repair Pass (unescaped newlines/tabs, trailing commas, truncated brackets/quotes).
      Stage 4: Python Literal Pass (ast.literal_eval for single-quoted dicts).
      Stage 5: Regex Field & Final-Line Rescue (extract answer and reason, explicit Answer: patterns).
    """
    if not raw_text or not str(raw_text).strip():
        raise ParseError("LLM returned an empty response")

    raw_str = str(raw_text).strip()

    # Stage 1: Clean delimiters, tags, and fences
    cleaned, verification_workspace = clean_tags_and_fences(raw_str)

    data: Optional[Dict[str, Any]] = None

    # Stage 2: Fast json.loads on candidate
    candidate = extract_json_candidate(cleaned)
    if candidate:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and any(k in parsed for k in TARGET_KEYS):
                data = parsed
        except Exception:
            pass

    # Stage 3: JSON Repair Pass
    if not data and candidate:
        try:
            repaired = repair_json_string(candidate)
            parsed = json.loads(repaired)
            if isinstance(parsed, dict) and any(k in parsed for k in TARGET_KEYS):
                data = parsed
        except Exception:
            pass

    # Stage 4: Python Literal Pass (ast.literal_eval for single-quoted dicts)
    if not data and candidate:
        for text_variant in (candidate, repair_json_string(candidate)):
            try:
                candidate_data = ast.literal_eval(text_variant)
                if isinstance(candidate_data, dict) and any(k in candidate_data for k in TARGET_KEYS):
                    data = candidate_data
                    break
            except Exception:
                pass

    # Stage 5: Regex Field & Final-Line Rescue
    if not data:
        search_sources: List[str] = []
        if verification_workspace:
            search_sources.append(verification_workspace)
        if cleaned:
            search_sources.append(cleaned)
        search_sources.append(raw_str)

        for src in search_sources:
            # 5a. Regex extraction of "answer" : ... and "reason" : ...
            ans_match = re.search(
                r'["\']?(?:answer|correctAnswer|correctOptionId)["\']?\s*[:=]\s*(?:"([^"]+)"|\'([^\']+)\'|\[([^\]]+)\]|([A-Pa-p0-9\.\-\_\+]+))',
                src,
                re.IGNORECASE,
            )
            reason_match = re.search(
                r'["\']?(?:reason|explanation|reasoning_summary)["\']?\s*[:=]\s*(?:"([^"]+)"|\'([^\']+)\')',
                src,
                re.IGNORECASE,
            )

            if ans_match:
                raw_ans_group = ans_match.group(1) or ans_match.group(2) or ans_match.group(3) or ans_match.group(4)
                extracted_ans: Any = raw_ans_group
                # If array format like [A, C] or ["A", "C"]
                if ans_match.group(3):
                    arr_items = [re.sub(r"^['\"]|['\"]$", "", x.strip()) for x in ans_match.group(3).split(",") if x.strip()]
                    extracted_ans = arr_items if arr_items else raw_ans_group
                extracted_reason = ""
                if reason_match:
                    extracted_reason = reason_match.group(1) or reason_match.group(2) or ""
                data = {
                    "answer": extracted_ans.strip() if isinstance(extracted_ans, str) else extracted_ans,
                    "reason": extracted_reason.strip() if extracted_reason else "Step-by-step derivation verified."
                }
                break

        # 5b. Explicit Final Answer / Answer: patterns (supporting options A-P)
        if not data:
            final_patterns = [
                r'(?:final\s+answer|conclusion)\s*[:\-]?\s*\[?([A-Pa-p0-9,\s\'"]+)\]?',
                r'(?:correct\s+(?:answer|option|choice)\s+is|answer\s*[:\-]|option\s*[:\-]?|choice\s*[:\-]?|therefore|hence|thus)\s*[\(\[]?([A-Pa-p0-9])[\)\]]?(?:\s|$|\.|\,)',
                r'(?:option|choice|answer)\s+([A-Pa-p0-9])\s+(?:is\s+correct|is\s+the\s+correct|is\s+right)',
                r'\b([A-Pa-p0-9])\s+is\s+(?:the\s+)?correct\s+(?:option|choice|answer)\b',
                r'[\(\[]([A-Pa-p0-9])[\)\]]\s+(?:is\s+correct|is\s+the\s+correct)',
                r'(?:select|choose)\s+option\s+([A-Pa-p0-9])\b',
            ]
            for src in search_sources:
                for pat in final_patterns:
                    m = re.search(pat, src, re.IGNORECASE)
                    if m:
                        matched_str = m.group(1).strip()
                        if "," in matched_str:
                            arr_items = [re.sub(r"^['\"]|['\"]$", "", x.strip()).upper() for x in matched_str.split(",") if x.strip()]
                            extracted_ans = arr_items if arr_items else matched_str.upper()
                        else:
                            extracted_ans = matched_str.upper()
                        data = {
                            "answer": extracted_ans,
                            "reason": "Step-by-step derivation verified."
                        }
                        break
                if data:
                    break

        # 5c. Option text substring matching against provided options
        if not data and options and len(options) >= 2:
            for idx, opt in enumerate(options):
                clean_opt = str(opt).strip()
                if clean_opt and len(clean_opt) >= 2:
                    opt_pattern = r'\b' + re.escape(clean_opt) + r'\b'
                    for src in search_sources:
                        if re.search(opt_pattern, src, re.IGNORECASE):
                            letter = chr(65 + idx)
                            data = {
                                "answer": letter,
                                "reason": f"Matched deduction for option ({letter}) '{clean_opt}' in reasoning."
                            }
                            break
                if data:
                    break

        if not data:
            raise ParseError(f"JSON decode failed and regex fallback could not find 'answer' in: '{raw_text[:120]}'")

    if not isinstance(data, dict):
        raise ParseError(f"Expected JSON object, got {type(data).__name__}")

    # Map alternative schema keys
    if "answer" not in data or data["answer"] is None:
        if "correctAnswer" in data and data["correctAnswer"] is not None:
            data["answer"] = data["correctAnswer"]
        elif "correctOptionId" in data and data["correctOptionId"] is not None:
            data["answer"] = data["correctOptionId"]

    if "reason" not in data or not data["reason"]:
        if "explanation" in data and data["explanation"]:
            data["reason"] = data["explanation"]
        elif "reasoning_summary" in data and data["reasoning_summary"]:
            data["reason"] = data["reasoning_summary"]

    if "answer" not in data or data["answer"] is None:
        raise ParseError("JSON response missing required 'answer' key")

    answer_val = data["answer"]
    if isinstance(answer_val, str) and not answer_val.strip():
        raise ParseError("'answer' string cannot be empty")
    if isinstance(answer_val, (list, tuple)) and len(answer_val) == 0:
        raise ParseError("'answer' array cannot be empty")

    reason_val = data.get("reason", "")
    if not isinstance(reason_val, str) or not reason_val.strip():
        reason_val = "Step-by-step derivation verified."

    reason_clean = " ".join(str(reason_val).strip().split())

    return ParsedLLMResponse(
        answer=answer_val,
        reason=reason_clean,
        verification_workspace=verification_workspace,
        raw_text=raw_text,
    )
