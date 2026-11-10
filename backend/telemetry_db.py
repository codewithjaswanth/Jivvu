"""QuizZy persistent telemetry storage module with SQLite WAL concurrency."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from backend.db_util import connect_sqlite_wal

logger = logging.getLogger("quizzy.telemetry")

PROD_DB_PATH = Path(__file__).resolve().parent / "telemetry.db"
DEFAULT_DB_PATH = PROD_DB_PATH

# Deterministic error type classification enum values
VALID_ERROR_TYPES = [
    "NONE",
    "HALLUCINATED_OPTION",
    "EMPTY_ANSWER",
    "LLM_TIMEOUT",
    "RATE_LIMIT_429",
    "SCHEMA_PARSE_ERROR",
    "VALIDATION_ERROR",
    "PROVIDER_API_ERROR",
    "INTERNAL_SERVER_ERROR",
]


def resolve_db_path(db_path: Optional[Union[str, Path]] = None) -> Path:
    """
    Resolve database path with test isolation:
    1. Explicit db_path argument always takes priority.
    2. If DEFAULT_DB_PATH was altered/monkeypatched (e.g. by a test fixture), respect it.
    3. QUIZZY_TELEMETRY_DB environment variable takes precedence when set.
    4. If running inside pytest, route to a temporary test database to prevent polluting production.
    5. Default to backend/telemetry.db anchored strictly to file location.
    """
    if db_path is not None:
        return Path(db_path).resolve()
    if DEFAULT_DB_PATH != PROD_DB_PATH:
        return Path(DEFAULT_DB_PATH).resolve()
    if "QUIZZY_TELEMETRY_DB" in os.environ and os.environ["QUIZZY_TELEMETRY_DB"].strip():
        return Path(os.environ["QUIZZY_TELEMETRY_DB"]).resolve()
    if "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules:
        return (Path(tempfile.gettempdir()) / "quizzy_pytest_telemetry.db").resolve()
    return DEFAULT_DB_PATH


def get_db_connection(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """
    Open an SQLite connection configured with WAL mode and row factory.
    Enables concurrent multi-process reads and writes between backend and dashboard.
    """
    return connect_sqlite_wal(resolve_db_path(db_path), timeout=15.0)


def init_db(db_path: Optional[Union[str, Path]] = None) -> None:
    """Initialize telemetry tables and performance indexes."""
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS request_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    subject TEXT,
                    q_type TEXT,
                    question_text TEXT,
                    options_json TEXT,
                    selected_answer TEXT,
                    selected_index INTEGER,
                    canonical_key TEXT,
                    status TEXT NOT NULL,
                    is_accurate INTEGER NOT NULL,
                    error_type TEXT NOT NULL,
                    error_message TEXT,
                    latency_ms REAL NOT NULL,
                    model_name TEXT,
                    cache_hit INTEGER NOT NULL,
                    raw_response_preview TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_req_timestamp ON request_logs(timestamp);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_req_status ON request_logs(status);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_req_error_type ON request_logs(error_type);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_req_is_accurate ON request_logs(is_accurate);"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
    finally:
        conn.close()

    # Automatically heal historical false-positive HALLUCINATED_OPTION entries
    repair_historical_hallucinated_options(db_path)


init_telemetry_db = init_db


def repair_historical_hallucinated_options(db_path: Optional[Union[str, Path]] = None) -> int:
    """
    Retrospectively repair false-positive HALLUCINATED_OPTION records in telemetry.db
    for multi and order questions where every letter/index maps to a valid option.
    """
    path = resolve_db_path(db_path)
    if not path.is_file():
        return 0

    repaired_count = 0
    try:
        conn = get_db_connection(path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, q_type, options_json, selected_answer, canonical_key
                FROM request_logs
                WHERE error_type = 'HALLUCINATED_OPTION'
                  AND (q_type IN ('multi', 'order', 'multi_mcq', 'multiselect') OR selected_answer LIKE '%,%')
            """)
            rows = cursor.fetchall()
            for row in rows:
                row_id = row["id"]
                opts_json = row["options_json"]
                sel_ans = row["selected_answer"] or row["canonical_key"] or ""
                if not opts_json or not sel_ans:
                    continue
                try:
                    options = json.loads(opts_json)
                except Exception:
                    continue
                if not isinstance(options, list) or len(options) == 0:
                    continue

                clean_ans = str(sel_ans).strip()
                if clean_ans.startswith("[") and clean_ans.endswith("]"):
                    try:
                        parsed_arr = json.loads(clean_ans)
                        if isinstance(parsed_arr, list):
                            parts = [str(x).strip().strip("'\"[]()").upper() for x in parsed_arr if str(x).strip()]
                        else:
                            parts = [p.strip().strip("'\"[]()").upper() for p in re.split(r"[,;>]+|->", clean_ans) if p.strip()]
                    except Exception:
                        parts = [p.strip().strip("'\"[]()").upper() for p in re.split(r"[,;>]+|->", clean_ans) if p.strip()]
                else:
                    parts = [p.strip().strip("'\"[]()").upper() for p in re.split(r"[,;>]+|->", clean_ans) if p.strip()]

                if not parts:
                    continue

                all_valid = True
                for p in parts:
                    if p.isdigit():
                        idx = int(p)
                        if not (0 <= idx < len(options)):
                            all_valid = False
                            break
                    elif len(p) == 1 and p in "ABCDEFGHIJKLMNOP":
                        idx = ord(p) - 65
                        if not (0 <= idx < len(options)):
                            all_valid = False
                            break
                    else:
                        # Strip prefixes like "Option A"
                        m = re.match(r"^(?:OPTION|CHOICE)?\s*([A-P])(?:[\.\:\)]|$)", p)
                        if m:
                            idx = ord(m.group(1)) - 65
                            if not (0 <= idx < len(options)):
                                all_valid = False
                                break
                        else:
                            p_norm = re.sub(r"\s+", " ", p).strip().lower()
                            if not any(re.sub(r"\s+", " ", str(o)).strip().lower() == p_norm for o in options):
                                all_valid = False
                                break

                if all_valid:
                    cursor.execute("""
                        UPDATE request_logs
                        SET error_type = 'NONE',
                            status = 'SUCCESS',
                            is_accurate = 1,
                            error_message = NULL
                        WHERE id = ?
                    """, (row_id,))
                    repaired_count += 1
            if repaired_count > 0:
                conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Error running retrospective telemetry repair: %s", exc)

    return repaired_count


def get_system_setting(
    key: str,
    default: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> Optional[str]:
    """Retrieve persistent setting value from system_settings table."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?;", (key,)).fetchone()
        if row is not None:
            return str(row["value"])
        return default
    except Exception as exc:
        logger.warning("Failed to read system setting '%s': %s", key, exc)
        return default
    finally:
        conn.close()


def set_system_setting(
    key: str,
    value: str,
    db_path: Optional[Union[str, Path]] = None,
) -> bool:
    """Insert or update persistent setting in system_settings table."""
    init_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO system_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at;
                """,
                (key, str(value), now_iso),
            )
        return True
    except Exception as exc:
        logger.warning("Failed to write system setting '%s': %s", key, exc)
        return False
    finally:
        conn.close()


def record_request_log(
    endpoint: str,
    subject: Optional[str] = "General",
    q_type: Optional[str] = None,
    question_text: Optional[str] = "",
    options: Optional[List[str]] = None,
    selected_answer: Optional[str] = None,
    selected_index: Optional[int] = None,
    canonical_key: Optional[str] = None,
    status: str = "SUCCESS",
    is_accurate: bool = True,
    error_type: str = "NONE",
    error_message: Optional[str] = None,
    latency_ms: float = 0.0,
    model_name: Optional[str] = None,
    cache_hit: bool = False,
    raw_response_preview: Optional[str] = None,
    timestamp: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> Optional[int]:
    """
    Safely record a request event to the telemetry database.
    Never raises an exception so telemetry failures never crash the solver pipeline.
    """
    try:
        init_db(db_path)
        iso_timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        options_json = json.dumps(options or [])

        # Validate error type against supported enum
        clean_error_type = error_type.strip().upper() if error_type else "NONE"
        if clean_error_type not in VALID_ERROR_TYPES:
            clean_error_type = "INTERNAL_SERVER_ERROR"

        # Any request with error_type != NONE or is_accurate == False MUST be marked FAILURE
        # This guarantees Success Count + Failure Count == Total Requests and Error Breakdown consistency
        if clean_error_type != "NONE" or not is_accurate:
            clean_status = "FAILURE"
            is_acc_val = 0
        else:
            clean_status = "SUCCESS" if status.strip().upper() == "SUCCESS" else "FAILURE"
            is_acc_val = 1 if clean_status == "SUCCESS" else 0

        conn = get_db_connection(db_path)
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO request_logs (
                        timestamp, endpoint, subject, q_type, question_text,
                        options_json, selected_answer, selected_index, canonical_key,
                        status, is_accurate, error_type, error_message,
                        latency_ms, model_name, cache_hit, raw_response_preview
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        iso_timestamp,
                        endpoint,
                        subject or "General",
                        q_type or "",
                        (question_text or "")[:4000],
                        options_json,
                        str(selected_answer) if selected_answer is not None else None,
                        selected_index,
                        canonical_key,
                        clean_status,
                        is_acc_val,
                        clean_error_type,
                        str(error_message) if error_message else None,
                        round(float(latency_ms), 2),
                        model_name or "",
                        1 if cache_hit else 0,
                        (raw_response_preview or "")[:2000],
                    ),
                )
                return cursor.lastrowid
        finally:
            conn.close()
    except Exception as exc:
        logger.error(f"Telemetry logging failed safely: {exc}")
        return None


def _get_window_cutoff(window: str) -> Optional[str]:
    """Calculate ISO timestamp cutoff for window filtering."""
    w = (window or "all").lower().strip()
    now = datetime.now(timezone.utc)
    if w == "1h":
        return (now - timedelta(hours=1)).isoformat()
    if w == "24h":
        return (now - timedelta(hours=24)).isoformat()
    return None


def get_dashboard_summary(
    window: str = "all", db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Calculate and return aggregated KPIs, error breakdown, and recent timeline.
    Strict non-hallucination metrics and weighted performance scores.
    """
    init_db(db_path)
    cutoff = _get_window_cutoff(window)
    where_clause = "WHERE timestamp >= ?" if cutoff else ""
    params = [cutoff] if cutoff else []

    conn = get_db_connection(db_path)
    try:
        # 1. Basic counts
        query = f"""
            SELECT 
                COUNT(*) as total_requests,
                SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status != 'SUCCESS' THEN 1 ELSE 0 END) as failure_count,
                SUM(CASE WHEN is_accurate = 1 THEN 1 ELSE 0 END) as accurate_count,
                AVG(latency_ms) as avg_latency_ms,
                MIN(latency_ms) as min_latency_ms,
                MAX(latency_ms) as max_latency_ms
            FROM request_logs
            {where_clause}
        """
        row = conn.execute(query, params).fetchone()
        total_requests = int(row["total_requests"] or 0)
        success_count = int(row["success_count"] or 0)
        failure_count = int(row["failure_count"] or 0)
        accurate_count = int(row["accurate_count"] or 0)
        avg_latency = float(row["avg_latency_ms"] or 0.0)
        min_latency = float(row["min_latency_ms"] or 0.0)
        max_latency = float(row["max_latency_ms"] or 0.0)

        # Zero-state handling (Fresh database or cleared logs)
        # Avoids 20/100 Grade F bug when total_requests == 0
        if total_requests == 0:
            return {
                "window": window,
                "overall_performance": 100.0,
                "health_grade": "STANDBY",
                "total_requests": 0,
                "success_count": 0,
                "failure_count": 0,
                "accurate_count": 0,
                "success_rate": 0.0,
                "error_rate": 0.0,
                "accuracy_rate": 0.0,
                "avg_latency_ms": 0.0,
                "min_latency_ms": 0.0,
                "max_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "latency_score": 100.0,
                "error_breakdown": {et: {"count": 0, "percentage": 0.0} for et in VALID_ERROR_TYPES},
                "timeline": [],
            }

        # 2. Latency percentiles (P95)
        p_query = f"""
            SELECT latency_ms FROM request_logs
            {where_clause}
            ORDER BY latency_ms ASC
        """
        latencies = [float(r["latency_ms"]) for r in conn.execute(p_query, params).fetchall()]
        p95_latency = 0.0
        if latencies:
            idx = max(0, math.ceil(0.95 * len(latencies)) - 1)
            p95_latency = latencies[idx]

        # 3. Rate Calculations
        success_rate = round((success_count / total_requests) * 100.0, 2)
        error_rate = round((failure_count / total_requests) * 100.0, 2)
        accuracy_rate = round((accurate_count / total_requests) * 100.0, 2)

        # 4. Latency Score (100 if <=1500ms, down to 0 at >=10000ms)
        if total_requests == 0 or avg_latency <= 1500.0:
            latency_score = 100.0
        elif avg_latency >= 10000.0:
            latency_score = 0.0
        else:
            latency_score = 100.0 - ((avg_latency - 1500.0) / (10000.0 - 1500.0)) * 100.0
        latency_score = max(0.0, min(100.0, latency_score))

        # 5. QuizZy Overall Performance Score (0-100)
        # 50% Accuracy, 30% Success Rate, 20% Latency Score
        overall_score = round(
            (0.50 * accuracy_rate) + (0.30 * success_rate) + (0.20 * latency_score), 1
        )
        overall_score = max(0.0, min(100.0, overall_score))

        # Health grade
        if overall_score >= 95.0:
            health_grade = "A+"
        elif overall_score >= 90.0:
            health_grade = "A"
        elif overall_score >= 80.0:
            health_grade = "B"
        elif overall_score >= 70.0:
            health_grade = "C"
        elif overall_score >= 60.0:
            health_grade = "D"
        else:
            health_grade = "F"

        # 6. Error types breakdown
        err_query = f"""
            SELECT error_type, COUNT(*) as cnt
            FROM request_logs
            {where_clause}
            GROUP BY error_type
        """
        err_rows = conn.execute(err_query, params).fetchall()
        counts_by_type = {r["error_type"]: int(r["cnt"]) for r in err_rows}

        error_breakdown: Dict[str, Dict[str, Any]] = {}
        for et in VALID_ERROR_TYPES:
            cnt = counts_by_type.get(et, 0)
            pct = round((cnt / total_requests) * 100.0, 2) if total_requests > 0 else 0.0
            error_breakdown[et] = {"count": cnt, "percentage": pct}

        # 7. Recent timeline for visual chart (last 50 requests)
        timeline_query = f"""
            SELECT id, timestamp, endpoint, status, is_accurate, error_type, latency_ms, model_name, cache_hit
            FROM request_logs
            {where_clause}
            ORDER BY id DESC
            LIMIT 50
        """
        timeline_rows = conn.execute(timeline_query, params).fetchall()
        timeline = [
            {
                "id": r["id"],
                "timestamp": r["timestamp"],
                "endpoint": r["endpoint"],
                "status": r["status"],
                "is_accurate": bool(r["is_accurate"]),
                "error_type": r["error_type"],
                "latency_ms": round(float(r["latency_ms"]), 2),
                "model_name": r["model_name"],
                "cache_hit": bool(r["cache_hit"]),
            }
            for r in reversed(timeline_rows)
        ]

        return {
            "window": window,
            "overall_performance": overall_score,
            "health_grade": health_grade,
            "total_requests": total_requests,
            "success_count": success_count,
            "failure_count": failure_count,
            "accurate_count": accurate_count,
            "success_rate": success_rate,
            "error_rate": error_rate,
            "accuracy_rate": accuracy_rate,
            "avg_latency_ms": round(avg_latency, 2),
            "min_latency_ms": round(min_latency, 2),
            "max_latency_ms": round(max_latency, 2),
            "p95_latency_ms": round(p95_latency, 2),
            "latency_score": round(latency_score, 1),
            "error_breakdown": error_breakdown,
            "timeline": timeline,
        }
    finally:
        conn.close()


def get_request_logs(
    limit: int = 200,
    offset: int = 0,
    status_filter: Optional[str] = None,
    error_filter: Optional[str] = None,
    search: Optional[str] = None,
    window: str = "all",
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    Retrieve paginated and filtered request records.
    Returns matching logs and total count.
    """
    init_db(db_path)
    conditions = []
    params: List[Any] = []

    cutoff = _get_window_cutoff(window)
    if cutoff:
        conditions.append("timestamp >= ?")
        params.append(cutoff)

    if status_filter and status_filter.upper() in ("SUCCESS", "FAILURE"):
        conditions.append("status = ?")
        params.append(status_filter.upper())

    if error_filter and error_filter.upper() in VALID_ERROR_TYPES:
        conditions.append("error_type = ?")
        params.append(error_filter.upper())

    if search and search.strip():
        term = f"%{search.strip()}%"
        conditions.append(
            "(question_text LIKE ? OR selected_answer LIKE ? OR error_message LIKE ? OR model_name LIKE ?)"
        )
        params.extend([term, term, term, term])

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    conn = get_db_connection(db_path)
    try:
        count_sql = f"SELECT COUNT(*) as total FROM request_logs {where_sql}"
        total = int(conn.execute(count_sql, params).fetchone()["total"])

        data_sql = f"""
            SELECT id, timestamp, endpoint, subject, q_type, question_text,
                   options_json, selected_answer, selected_index, canonical_key,
                   status, is_accurate, error_type, error_message, latency_ms,
                   model_name, cache_hit, raw_response_preview
            FROM request_logs
            {where_sql}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(data_sql, params + [max(1, limit), max(0, offset)]).fetchall()

        logs = []
        for r in rows:
            try:
                opts = json.loads(r["options_json"] or "[]")
            except Exception:
                opts = []
            logs.append({
                "id": r["id"],
                "timestamp": r["timestamp"],
                "endpoint": r["endpoint"],
                "subject": r["subject"],
                "q_type": r["q_type"],
                "question_text": r["question_text"],
                "options": opts,
                "selected_answer": r["selected_answer"],
                "selected_index": r["selected_index"],
                "canonical_key": r["canonical_key"],
                "status": r["status"],
                "is_accurate": bool(r["is_accurate"]),
                "error_type": r["error_type"],
                "error_message": r["error_message"],
                "latency_ms": round(float(r["latency_ms"]), 2),
                "model_name": r["model_name"],
                "cache_hit": bool(r["cache_hit"]),
                "raw_response_preview": r["raw_response_preview"],
            })

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "logs": logs,
        }
    finally:
        conn.close()


def get_request_detail(
    request_id: int, db_path: Optional[Union[str, Path]] = None
) -> Optional[Dict[str, Any]]:
    """Retrieve full details of a specific request record by ID."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM request_logs WHERE id = ?", (request_id,)
        ).fetchone()
        if not row:
            return None
        try:
            opts = json.loads(row["options_json"] or "[]")
        except Exception:
            opts = []
        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "endpoint": row["endpoint"],
            "subject": row["subject"],
            "q_type": row["q_type"],
            "question_text": row["question_text"],
            "options": opts,
            "selected_answer": row["selected_answer"],
            "selected_index": row["selected_index"],
            "canonical_key": row["canonical_key"],
            "status": row["status"],
            "is_accurate": bool(row["is_accurate"]),
            "error_type": row["error_type"],
            "error_message": row["error_message"],
            "latency_ms": round(float(row["latency_ms"]), 2),
            "model_name": row["model_name"],
            "cache_hit": bool(row["cache_hit"]),
            "raw_response_preview": row["raw_response_preview"],
        }
    finally:
        conn.close()


def clear_logs(db_path: Optional[Union[str, Path]] = None) -> int:
    """Clear all records from request_logs table without VACUUM locking."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.execute("DELETE FROM request_logs;")
            count = cursor.rowcount
            conn.execute("DELETE FROM sqlite_sequence WHERE name='request_logs';")
        conn.commit()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except sqlite3.OperationalError:
            pass
        return count
    finally:
        conn.close()


def validate_accuracy_and_classify_error(
    q_type: Optional[str] = None,
    options: Optional[List[str]] = None,
    response: Optional[Any] = None,
    exception: Optional[Exception] = None,
    error_msg: Optional[str] = None,
) -> tuple[bool, str, str]:
    """
    Evaluates Strict Non-Hallucination Accuracy and deterministically classifies error.
    
    Accuracy is defined strictly as whether the request provides a valid,
    non-hallucinated answer.
    
    Returns:
        (is_accurate: bool, error_type: str, status: str)
        status is 'SUCCESS' or 'FAILURE'
    """
    try:
        from backend.core.normalizer import normalize_text_answer
    except Exception:
        def normalize_text_answer(s: str) -> str:
            return "".join(c for c in str(s).lower() if c.isalnum())

    # 1. Handle explicit exception or fatal failure
    if exception is not None or (error_msg and not response):
        full_err = str(error_msg or exception or "")
        err_lower = full_err.lower()

        # Classify deterministic error types
        if (
            "timeout" in err_lower
            or (isinstance(exception, (TimeoutError, asyncio.TimeoutError)))
            or ("timed out" in err_lower)
        ):
            return False, "LLM_TIMEOUT", "FAILURE"

        if (
            "429" in full_err
            or "rate limit" in err_lower
            or (hasattr(exception, "status_code") and getattr(exception, "status_code") == 429)
        ):
            return False, "RATE_LIMIT_429", "FAILURE"

        if (
            isinstance(exception, (ValueError, AssertionError))
            or (hasattr(exception, "status_code") and getattr(exception, "status_code") in (400, 422))
            or "validation" in err_lower
            or "value_error" in err_lower
        ):
            return False, "VALIDATION_ERROR", "FAILURE"

        if (
            isinstance(exception, json.JSONDecodeError)
            or "jsondecode" in err_lower
            or "malformed" in err_lower
            or "schema" in err_lower
        ):
            return False, "SCHEMA_PARSE_ERROR", "FAILURE"

        if "cascade" in err_lower or "exhausted" in err_lower or "all cascade models failed" in err_lower:
            return False, "CASCADE_EXHAUSTED", "FAILURE"

        if (
            (hasattr(exception, "status_code") and getattr(exception, "status_code") in (500, 502, 503, 504))
            or "provider" in err_lower
            or "openrouter" in err_lower
            or "groq" in err_lower
            or "upstream" in err_lower
            or "connecterror" in err_lower
            or "httpx" in err_lower
            or "http_" in err_lower
            or "invalid_request" in err_lower
        ):
            return False, "PROVIDER_API_ERROR", "FAILURE"

        return False, "INTERNAL_SERVER_ERROR", "FAILURE"

    # 2. Check response object if provided
    if response is None:
        return False, "EMPTY_ANSWER", "FAILURE"

    # Check if response has an internal error field
    resp_err = getattr(response, "error", None)
    if resp_err:
        err_lower = str(resp_err).lower()
        if "timeout" in err_lower or "timed out" in err_lower:
            return False, "LLM_TIMEOUT", "FAILURE"
        if "429" in str(resp_err) or "rate limit" in err_lower:
            return False, "RATE_LIMIT_429", "FAILURE"
        if "parse" in err_lower or "json" in err_lower:
            return False, "SCHEMA_PARSE_ERROR", "FAILURE"
        if "cascade" in err_lower or "exhausted" in err_lower or "all cascade models failed" in err_lower:
            return False, "CASCADE_EXHAUSTED", "FAILURE"
        if (
            "provider" in err_lower
            or "upstream" in err_lower
            or "openrouter" in err_lower
            or "groq" in err_lower
            or "http_" in err_lower
            or "invalid_request" in err_lower
        ):
            return False, "PROVIDER_API_ERROR", "FAILURE"
        return False, "CASCADE_EXHAUSTED", "FAILURE"

    # 3. Extract answer components
    ans_val = getattr(response, "answer", None)
    sel_text = getattr(response, "selected_text", None)
    sel_idx = getattr(response, "selected_index", None)
    canon_key = getattr(response, "canonical_key", None)
    sel_indices = getattr(response, "selected_indices", None)
    perm_indices = getattr(response, "permutation_indices", None)
    canon_keys = getattr(response, "canonical_keys", None)

    has_answer = False
    if ans_val is not None and str(ans_val).strip() != "":
        has_answer = True
    elif sel_text is not None and str(sel_text).strip() != "":
        has_answer = True
    elif sel_idx is not None:
        has_answer = True
    elif canon_key is not None and str(canon_key).strip() != "":
        has_answer = True
    elif sel_indices and len(sel_indices) > 0:
        has_answer = True
    elif perm_indices and len(perm_indices) > 0:
        has_answer = True
    elif canon_keys and len(canon_keys) > 0:
        has_answer = True

    if not has_answer:
        return False, "EMPTY_ANSWER", "FAILURE"

    # 4. Strict Non-Hallucination Check for option-based questions
    if options and len(options) > 0 and (q_type in ("mcq", "order", "multi") or q_type is None):
        norm_opts = [normalize_text_answer(o) for o in options]
        lower_opts = [str(o).strip().lower() for o in options]

        def opt_or_letter_matches(item: Any) -> bool:
            if item is None:
                return False
            s = str(item).strip().strip("'\"[]()")
            if not s:
                return False
            if len(s) == 1 and s.upper() in "ABCDEFGHIJKLMNOP":
                idx = ord(s.upper()) - 65
                if 0 <= idx < len(options):
                    return True
            if s.isdigit():
                idx = int(s)
                if 0 <= idx < len(options):
                    return True
            if s.lower() in lower_opts:
                return True
            if normalize_text_answer(s) in norm_opts:
                return True
            m = re.match(r"^(?:option|choice)?\s*([A-P])(?:[\.\:\)]|$)", s, re.IGNORECASE)
            if m:
                idx = ord(m.group(1).upper()) - 65
                if 0 <= idx < len(options):
                    return True
            return False

        # Validate index if given (for mcq, or anchor index for multi/order)
        if sel_idx is not None:
            if not isinstance(sel_idx, int) or sel_idx < 0 or sel_idx >= len(options):
                return False, "HALLUCINATED_OPTION", "FAILURE"

        # Validate selected_indices for multi
        if sel_indices:
            for idx in sel_indices:
                if not isinstance(idx, int) or idx < 0 or idx >= len(options):
                    return False, "HALLUCINATED_OPTION", "FAILURE"

        # Validate permutation_indices for order
        if perm_indices:
            for idx in perm_indices:
                if not isinstance(idx, int) or idx < 0 or idx >= len(options):
                    return False, "HALLUCINATED_OPTION", "FAILURE"

        # Validate canonical_keys if provided
        if canon_keys:
            for k in canon_keys:
                if not opt_or_letter_matches(k):
                    return False, "HALLUCINATED_OPTION", "FAILURE"

        # Validate selected_texts if provided
        sel_texts_list = getattr(response, "selected_texts", None)
        if sel_texts_list and isinstance(sel_texts_list, (list, tuple)):
            for t in sel_texts_list:
                if not opt_or_letter_matches(t):
                    return False, "HALLUCINATED_OPTION", "FAILURE"

        # Multi-Answer Validation: handle comma-separated options/letters without false-positive hallucination
        is_multi_shape = (
            q_type in ("multi", "multi_mcq", "multiselect")
            or (canon_keys and len(canon_keys) > 1)
            or (sel_indices and len(sel_indices) > 1)
            or ("," in str(ans_val or "") and q_type != "order")
        )

        if is_multi_shape:
            if ans_val is not None and str(ans_val).strip():
                parts = [p.strip() for p in re.split(r"[,;]+", str(ans_val)) if p.strip()]
                if not parts:
                    return False, "EMPTY_ANSWER", "FAILURE"
                for p in parts:
                    if not opt_or_letter_matches(p):
                        return False, "HALLUCINATED_OPTION", "FAILURE"
            if sel_text is not None and str(sel_text).strip() and not sel_texts_list and not sel_indices:
                parts = [p.strip() for p in re.split(r"[,;]+", str(sel_text)) if p.strip()]
                for p in parts:
                    if not opt_or_letter_matches(p):
                        return False, "HALLUCINATED_OPTION", "FAILURE"
        elif q_type == "order":
            if ans_val is not None and str(ans_val).strip():
                parts = [p.strip() for p in re.split(r"[,;>]+|->", str(ans_val)) if p.strip()]
                if parts:
                    for p in parts:
                        if not opt_or_letter_matches(p):
                            return False, "HALLUCINATED_OPTION", "FAILURE"
        else:
            # Single-choice MCQ or text validation
            if sel_text is not None and str(sel_text).strip():
                if not opt_or_letter_matches(sel_text):
                    return False, "HALLUCINATED_OPTION", "FAILURE"
            elif ans_val is not None and str(ans_val).strip() and sel_idx is None and not perm_indices and not sel_indices:
                if not opt_or_letter_matches(ans_val):
                    return False, "HALLUCINATED_OPTION", "FAILURE"

    # Check overall response status
    resp_status = str(getattr(response, "status", "ok")).strip().lower()
    if resp_status not in ("ok", "success"):
        return False, "CASCADE_EXHAUSTED", "FAILURE"

    return True, "NONE", "SUCCESS"
