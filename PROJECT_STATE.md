# QuizZy / Jivvu Project State

## Current Milestone: Gemini 3.8 Flash Primary Consensus & Multimodal Vision Engine
- **Status**: Production-Ready / 311 Pytest Tests Passing (100% Pass Rate) / Live Daemon Verified on `:8000`.
- **Primary Repository Remote**: `https://github.com/codewithjaswanth/Jivvu.git` (`main` branch).
- **Core Production Upgrades Delivered**:
  - **Gemini 3.8 Flash Native Integration & Multi-Key Pool (`backend/core/key_pool.py`, `backend/core/providers.py`)**:
    - Deduplicated 4-key round-robin pool across verified Google AI Studio keys (`Standard / Pay-As-You-Go Tier`: 1,000-2,000 RPM, 4M TPM).
    - Granular cooldown mechanics: 8.0s (or `Retry-After`) on HTTP 429; 1.5s micro-cooldown with instant sub-5ms key rotation on transient HTTP 503.
    - Native `v1beta/models/gemini-3.8-flash:generateContent` endpoint with `x-goog-api-key` header authentication.
    - Multi-part `"thought": true` filtering cleanly separating thinking parts from verified JSON answers.
    - Adaptive `thinkingConfig`: Fast Recall (128 tokens, ~450-650ms), Analytical (1024 tokens), Deep Aptitude & Vision (2048 tokens). Safe `maxOutputTokens >= thinking_budget + 1024`.
  - **Intelligent Dual-Engine Racing Architecture (`backend/core/llm_client.py`, `models.json`)**:
    - Mode A (Visual/Diagram Questions): Concurrent dual-Gemini race (Primary 1 temp 0.1 budget 2048 vs Primary 2 temp 0.2 budget 1536) with instant single-survivor rescue and Arbiter tiebreaking. High precision inline base64 image ingestion.
    - Mode B (Text/Aptitude/Code/Ordering Questions): Concurrent Gemini Primary A + Groq Primary B (openai/gpt-oss-120b). Disagreements break ties via Gemini Arbiter (budget 2048) and Per-Option 2-of-3 Majority Voting for multi-select.
  - **Quantitative, Logical & Visual Aptitude Domain Pack (`backend/core/prompts.py`)**:
    - Rigorous formulas for Permutations/Combinations (`N! / f1!...`), circular seating (`(N-1)!`), relative speed ($S_1 \pm S_2$), LCM unit work/pipes, clock angle ($|30H - 5.5M|$), calendar odd days mod 7, vector directions, and figure counting ($n(n+2)(2n+1)/8$, nested triangles $1+4m$, square diagonals $2k$).
  - **Dedicated Diagnostic & Hardening Suite (`tests/test_gemini_38_flash_aptitude_and_vision.py`)**:
    - 7 comprehensive diagnostic tests covering key loading, thought filtering, adaptive budget, failover, dual-vision racing, aptitude prompts, and 2-of-3 voting.
- **Active Daemons & Interfaces**:
  - Local Inference Backend (`127.0.0.1:8000`): Healthy and active (`/health` HTTP 200, Gemini 3.8 Flash primary).
  - Observability Dashboard (`127.0.0.1:8765`): Healthy and active (`ON`), undisturbed.
- **Test Integrity & Evidence**:
  - Full Pytest Suite (`pytest tests -q`): 311 passed, 2 skipped, 1 warning (100% pass rate in 133s).
  - Gemini 3.8 Flash Diagnostic Suite: 7/7 passed (100%).
  - Offline Deterministic Self-Test (`eval/run_eval.py --self-test`): 8/8 passed (100%).
  - Real Screenshots Demo Suite (`tests/test_real_screenshots_demo.py`): 4/4 passed (100%).
