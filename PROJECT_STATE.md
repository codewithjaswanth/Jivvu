# QuizZy / Jivvu Project State

## Current Milestone: Per-API-Key Telemetry & Live Quota Observability Suite (:8765)
- **Status**: Production-Ready / 315 Passing Tests (100% Pass Rate) / Live Daemons on `:8000` & `:8765`.
- **Primary Repository Remote**: `https://github.com/codewithjaswanth/Jivvu.git` (`main` branch).
- **Core Production Upgrades Delivered**:
  - **Per-API-Key Telemetry & Sliding-Window Quotas (`backend/core/key_pool.py`, `backend/telemetry_db.py`)**:
    - Deterministic SHA256 `key_id` and zero-leak masking across all 16 configured keys (Gemini 4, Groq 7, OpenRouter 5).
    - SQLite WAL persistent tables `api_key_status` and `api_key_events` tracking 60s sliding-window RPM & TPM and 24h requests.
    - Robust rate-limit reset duration parsing across formats (`2m30s`, `450ms`, `15s`, timestamps).
  - **Provider Invocation Instrumentation (`backend/core/providers.py`)**:
    - Centralized instrumentation in `call_single_completion` capturing exact token counts (prompt, completion, thinking) and rate-limit headers.
    - Non-blocking telemetry recording across HTTP 200, 429, 503, 401/403, and network timeouts.
  - **Executive-Grade Dashboard Observability Suite (`backend/static/dashboard/*`, `backend/dashboard_server.py`)**:
    - Provider Summary Bar tracking total, healthy, cooldown, quarantined keys, global RPM/TPM, and total tokens.
    - Interactive Provider Tabs ("All Keys", "Gemini 3.8 Flash", "Groq", "OpenRouter") with debounced search.
    - High-density Grid Cards with dual RPM & TPM progress bars, token breakdown, and live 1s ticking reset countdowns.
    - Targeted per-key cooldown reset and global "Reset All Cooldowns" button synced to Port 8000.
  - **Synchronized REST APIs (`backend/app.py`, `backend/dashboard_server.py`)**:
    - `:8765/api/dashboard/api-keys` & `:8000/api/key-pool-status` returning live quota metrics.
    - `:8765/api/dashboard/api-keys/reset-cooldown` & `:8000/api/reset-state` clearing in-memory and SQLite cooldowns.
- **Active Daemons & Interfaces**:
  - Local Inference Backend (`127.0.0.1:8000`): Healthy and active (`/health` HTTP 200).
  - Observability Dashboard (`127.0.0.1:8765`): v1.3 active (`ON`), live telemetry stream running.
- **Test Integrity & Evidence**:
  - Full Pytest Suite (`pytest tests -q`): 315 passed, 3 skipped, 1 warning (100% pass rate in 137s).
  - API Key Telemetry Suite (`tests/test_api_key_dashboard_telemetry.py`): 5/5 passed (100%).
  - Model Selection & Dashboard Suite (`tests/test_model_selection.py`): 9/9 passed (100%).
  - Offline Deterministic Self-Test (`eval/run_eval.py --self-test`): 8/8 passed (100%).
