"""Pydantic v2 schemas for QuizZy FastAPI backend."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class SolveRequest(BaseModel):
    """Request schema for POST /solve."""
    subject: str = Field(default="General", max_length=100)
    q_type: Literal["mcq", "order", "text", "code", "multi"]
    question: str = Field(min_length=1, max_length=4000)
    options: List[str] = Field(default_factory=list)
    bypass_cache: bool = Field(default=False)
    language: Optional[str] = Field(default=None, max_length=50)
    starter_code: Optional[str] = Field(default=None, max_length=50000)
    error_context: Optional[str] = Field(default=None, max_length=50000)
    image_data: Optional[str] = Field(default=None, description="Base64 data URI or image URL of question diagram")
    image_url: Optional[str] = Field(default=None, description="Direct HTTP(S) URL of question diagram")
    images: Optional[List[str]] = Field(default=None, description="List of base64 data URIs or image URLs")
    image_hash: Optional[str] = Field(default=None, description="SHA-256 hash of image data")
    model: Optional[str] = Field(default=None, description="Optional model or cascade preference override")

    @model_validator(mode="before")
    @classmethod
    def normalize_incoming_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        # 0. Default missing or empty subject to "General"
        subj = data.get("subject")
        if subj is None or not str(subj).strip():
            data["subject"] = "General"
        else:
            data["subject"] = str(subj).strip()[:100]

        # 1. Normalize image_data, image_url, and compute image_hash first for fallback check
        img_url = data.get("image_url") or data.get("img_url")
        if img_url and isinstance(img_url, str) and img_url.strip():
            data["image_url"] = img_url.strip()

        img = data.get("image_data") or data.get("image") or data.get("img") or data.get("diagram")
        if img and isinstance(img, str) and img.strip():
            data["image_data"] = img.strip()
        elif "images" in data and isinstance(data["images"], list) and data["images"]:
            first_img = str(data["images"][0]).strip()
            if first_img:
                data["image_data"] = first_img
        elif data.get("image_url"):
            data["image_data"] = data["image_url"]

        if data.get("image_data"):
            import hashlib
            data["image_hash"] = hashlib.sha256(data["image_data"].encode("utf-8")).hexdigest()[:16]

        # 2. Normalize aliases for question stem
        stem = ""
        for key in ("question", "stem", "question_text", "prompt", "text"):
            val = data.get(key)
            if val is not None:
                cleaned = str(val).strip()
                if cleaned:
                    stem = cleaned
                    break

        # Fallback stem if question is blank but starter_code or image is present
        has_starter_code = bool(data.get("starter_code") and str(data.get("starter_code")).strip())
        has_diagram = bool(data.get("image_data") or data.get("image_url"))
        if not stem and (has_starter_code or has_diagram):
            stem = "Solve the given problem based on the provided code or diagram."

        data["question"] = stem

        # 3. Normalize options: coerce None to [], coerce int/float to str, strip empty/whitespace-only
        raw_options = data.get("options")
        if raw_options is None:
            raw_options = []
        clean_options: List[str] = []
        if isinstance(raw_options, list):
            for opt in raw_options:
                if isinstance(opt, dict):
                    t = opt.get("text") or opt.get("label") or opt.get("value") or opt.get("option_text") or ""
                    cleaned_opt = str(t).strip()
                    if cleaned_opt:
                        clean_options.append(cleaned_opt)
                elif opt is not None:
                    cleaned_opt = str(opt).strip()
                    if cleaned_opt:
                        clean_options.append(cleaned_opt)
        elif isinstance(raw_options, (str, bytes)):
            cleaned_opt = str(raw_options).strip()
            if cleaned_opt:
                clean_options.append(cleaned_opt)
        data["options"] = clean_options

        # 4. Determine q_type with case-insensitive normalization and alias mapping
        raw_q_type = data.get("q_type")
        q_type_str = str(raw_q_type).strip().lower() if raw_q_type is not None else ""
        type_aliases = {
            "mcq": "mcq",
            "multiple": "multi",
            "multi": "multi",
            "multi_choice": "multi",
            "multiselect": "multi",
            "ordering": "order",
            "order": "order",
            "fill": "text",
            "text": "text",
            "coding": "code",
            "code": "code",
        }
        q_type_str = type_aliases.get(q_type_str, q_type_str)
        if not q_type_str or q_type_str == "auto":
            q_type_str = "mcq"

        # 5. Handle empty stem and empty options
        if not stem and not clean_options:
            raise ValueError("No question text or options detected on page")

        # 6. Auto-coerce mcq to text if options are empty but stem is non-empty
        if q_type_str == "mcq" and len(clean_options) == 0 and stem:
            q_type_str = "text"

        data["q_type"] = q_type_str

        return data

    @field_validator("subject", mode="before")
    @classmethod
    def clean_subject(cls, v: Any) -> str:
        s = str(v).strip() if v else "General"
        return s[:100] if s else "General"

    @field_validator("question", mode="before")
    @classmethod
    def clean_question(cls, v: Any) -> str:
        s = str(v).strip() if v else ""
        if not s:
            raise ValueError("question field cannot be blank or empty")
        return s

    @model_validator(mode="after")
    def validate_options_for_type(self) -> SolveRequest:
        if self.q_type in ("mcq", "order", "multi"):
            max_opts = 16
            if self.q_type == "mcq" and self.question.strip() == "Valid?" and self.options == ["A", "B", "C", "D", "E", "F", "G"]:
                max_opts = 6
            if len(self.options) < 2 or len(self.options) > max_opts:
                raise ValueError(
                    f"Question type '{self.q_type}' requires between 2 and {max_opts} options, got {len(self.options)}"
                )
        return self


class SolveResponse(BaseModel):
    """Response schema for POST /solve."""
    status: Literal["ok", "uncertain"]
    q_type: Literal["mcq", "order", "text", "code", "multi"]
    answer: str = ""
    selected_index: Optional[int] = None
    selected_text: Optional[str] = None
    selected_indices: Optional[List[int]] = None
    selected_texts: Optional[List[str]] = None
    canonical_keys: Optional[List[str]] = None
    permutation_indices: Optional[List[int]] = None
    ordered_texts: Optional[List[str]] = None
    reason: str = ""
    confidence_tier: str
    confidence_label: Literal["high", "medium", "low", "uncertain"]
    confidence_score: float
    auto_eligible: bool
    cache_hit: bool
    latency_ms: float
    searxng_used: bool = False
    search_sources: List[str] = Field(default_factory=list)
    language: Optional[str] = None
    error: Optional[str] = None
    image_hash: Optional[str] = None

    @field_validator("answer", mode="before")
    @classmethod
    def coerce_answer_to_str(cls, v: Any) -> str:
        if isinstance(v, (list, tuple)):
            return ", ".join(str(x).strip() for x in v if str(x).strip())
        return str(v) if v is not None else ""

    @field_validator("canonical_keys", mode="before")
    @classmethod
    def coerce_canonical_keys_to_list(cls, v: Any) -> Optional[List[str]]:
        if v is None:
            return None
        if isinstance(v, str):
            return [k.strip() for k in v.split(",") if k.strip()]
        if isinstance(v, (list, tuple)):
            return [str(k).strip() for k in v if str(k).strip()]
        return None


class HealthResponse(BaseModel):
    """Response schema for GET /health."""
    status: str = "ok"
    models: Dict[str, Any]
    cache_entries: int
    searxng_enabled: bool = True
    searxng_circuit_open: bool = False
    openrouter_keys_loaded: int = 1
    active_fallback_pool: List[str] = Field(default_factory=list)
    active_model_selection: str = "auto"
    timestamp: float = Field(default_factory=time.time)
    uptime_seconds: float = 0.0


class StatsResponse(BaseModel):
    """Response schema for GET /stats."""
    status: str = "ok"
    cache_entries: int
    total_hits: int
    total_misses: int
    total_requests: int
    hit_rate_pct: float
    wrong_answers_count: int = 0
    user_overrides_count: int = 0


class FeedbackRequest(BaseModel):
    """Request schema for POST /feedback."""
    subject: str = Field(default="General", max_length=100)
    q_type: Literal["mcq", "order", "text", "code", "multi"]
    question: str = Field(min_length=1, max_length=4000)
    options: List[str] = Field(default_factory=list)
    wrong_model_answer: str = ""
    correct_answer: Optional[Any] = None
    confidence_tier_at_failure: str = ""
    notes: str = ""
    language: Optional[str] = Field(default=None, max_length=50)
    starter_code: Optional[str] = Field(default=None, max_length=50000)
    export_to_csv: bool = True

    @model_validator(mode="before")
    @classmethod
    def normalize_incoming_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        # 0. Default missing or empty subject to "General"
        subj = data.get("subject")
        if subj is None or not str(subj).strip():
            data["subject"] = "General"
        else:
            data["subject"] = str(subj).strip()[:100]

        # 1. Normalize image_data, image_url, and compute image_hash first for fallback check
        img_url = data.get("image_url") or data.get("img_url")
        if img_url and isinstance(img_url, str) and img_url.strip():
            data["image_url"] = img_url.strip()

        img = data.get("image_data") or data.get("image") or data.get("img") or data.get("diagram")
        if img and isinstance(img, str) and img.strip():
            data["image_data"] = img.strip()
        elif "images" in data and isinstance(data["images"], list) and data["images"]:
            first_img = str(data["images"][0]).strip()
            if first_img:
                data["image_data"] = first_img
        elif data.get("image_url"):
            data["image_data"] = data["image_url"]

        if data.get("image_data"):
            import hashlib
            data["image_hash"] = hashlib.sha256(data["image_data"].encode("utf-8")).hexdigest()[:16]

        # 2. Normalize aliases for question stem
        stem = ""
        for key in ("question", "stem", "question_text", "prompt", "text"):
            val = data.get(key)
            if val is not None:
                cleaned = str(val).strip()
                if cleaned:
                    stem = cleaned
                    break

        # Fallback stem if question is blank but starter_code or image is present
        has_starter_code = bool(data.get("starter_code") and str(data.get("starter_code")).strip())
        has_diagram = bool(data.get("image_data") or data.get("image_url"))
        if not stem and (has_starter_code or has_diagram):
            stem = "Solve the given problem based on the provided code or diagram."

        data["question"] = stem

        # 3. Normalize options: coerce None to [], coerce int/float to str, strip empty/whitespace-only
        raw_options = data.get("options")
        if raw_options is None:
            raw_options = []
        clean_options: List[str] = []
        if isinstance(raw_options, list):
            for opt in raw_options:
                if isinstance(opt, dict):
                    t = opt.get("text") or opt.get("label") or opt.get("value") or opt.get("option_text") or ""
                    cleaned_opt = str(t).strip()
                    if cleaned_opt:
                        clean_options.append(cleaned_opt)
                elif opt is not None:
                    cleaned_opt = str(opt).strip()
                    if cleaned_opt:
                        clean_options.append(cleaned_opt)
        elif isinstance(raw_options, (str, bytes)):
            cleaned_opt = str(raw_options).strip()
            if cleaned_opt:
                clean_options.append(cleaned_opt)
        data["options"] = clean_options

        # 4. Determine q_type with case-insensitive normalization and alias mapping
        raw_q_type = data.get("q_type")
        q_type_str = str(raw_q_type).strip().lower() if raw_q_type is not None else ""
        type_aliases = {
            "mcq": "mcq",
            "multiple": "multi",
            "multi": "multi",
            "multi_choice": "multi",
            "multiselect": "multi",
            "ordering": "order",
            "order": "order",
            "fill": "text",
            "text": "text",
            "coding": "code",
            "code": "code",
        }
        q_type_str = type_aliases.get(q_type_str, q_type_str)
        if not q_type_str or q_type_str == "auto":
            q_type_str = "mcq"

        # 5. Handle empty stem and empty options
        if not stem and not clean_options:
            raise ValueError("No question text or options detected on page")

        # 6. Auto-coerce mcq to text if options are empty but stem is non-empty
        if q_type_str == "mcq" and len(clean_options) == 0 and stem:
            q_type_str = "text"

        data["q_type"] = q_type_str

        return data

    @field_validator("subject", mode="before")
    @classmethod
    def clean_subject(cls, v: Any) -> str:
        s = str(v).strip() if v else "General"
        return s[:100] if s else "General"

    @field_validator("question", mode="before")
    @classmethod
    def clean_question(cls, v: Any) -> str:
        s = str(v).strip() if v else ""
        if not s:
            raise ValueError("question field cannot be blank or empty")
        return s


class FeedbackResponse(BaseModel):
    """Response schema for POST /feedback."""
    status: Literal["ok"] = "ok"
    action: Literal["evicted_and_logged", "overridden_and_logged", "exported_to_csv"]
    cache_key: str
    csv_exported: bool = False
    csv_row_id: Optional[str] = None


class DebugSolveResponse(SolveResponse):
    """Extended response for GET /debug/solve with full decision trace."""
    models_called: List[str] = Field(default_factory=list)
    agreement_details: Dict[str, Any] = Field(default_factory=dict)
