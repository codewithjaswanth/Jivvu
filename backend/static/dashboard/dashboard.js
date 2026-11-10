/**
 * QuizZy Observability Dashboard Client
 * Real-time telemetry polling, metrics visualization, and request inspection.
 */

(() => {
  // Application State
  const state = {
    autoRefresh: true,
    pollIntervalMs: 2000,
    timerId: null,
    timeWindow: "all",
    statusFilter: "",
    errorFilter: "",
    searchTerm: "",
    searchDebounceTimer: null,
    cachedRequests: [],
    providerFilter: "all",
    keySearchTerm: "",
    apiKeysData: null,
    countdownIntervalId: null,
  };

  // DOM Elements
  const el = {
    statusPill: document.getElementById("backend-status-pill"),
    statusText: document.getElementById("backend-status-text"),
    modelSelect: document.getElementById("model-select"),
    timeWindow: document.getElementById("time-window"),
    autoRefreshToggle: document.getElementById("auto-refresh-toggle"),
    btnRefresh: document.getElementById("btn-refresh"),
    btnClear: document.getElementById("btn-clear"),

    // KPI Elements
    perfScore: document.getElementById("perf-score"),
    perfProgressBar: document.getElementById("perf-progress-bar"),
    healthGradeBadge: document.getElementById("health-grade-badge"),
    totalRequests: document.getElementById("total-requests"),
    successCount: document.getElementById("success-count"),
    failureCount: document.getElementById("failure-count"),
    successRate: document.getElementById("success-rate"),
    errorRate: document.getElementById("error-rate"),
    splitBarSuccess: document.getElementById("split-bar-success"),
    splitBarError: document.getElementById("split-bar-error"),
    accuracyRate: document.getElementById("accuracy-rate"),
    accuracyProgressBar: document.getElementById("accuracy-progress-bar"),
    accurateCount: document.getElementById("accurate-count"),
    avgLatency: document.getElementById("avg-latency"),
    p95Latency: document.getElementById("p95-latency"),
    minLatency: document.getElementById("min-latency"),
    maxLatency: document.getElementById("max-latency"),
    backendStatusCardLabel: document.getElementById("backend-status-card-label"),
    backendPingMs: document.getElementById("backend-ping-ms"),
    backendActiveModelBadge: document.getElementById("backend-active-model-badge"),
    backendKeysBadge: document.getElementById("backend-keys-badge"),
    backendUptime: document.getElementById("backend-uptime"),

    // API Key Telemetry Elements
    sumTotalKeys: document.getElementById("sum-total-keys"),
    sumHealthyKeys: document.getElementById("sum-healthy-keys"),
    sumCooldownKeys: document.getElementById("sum-cooldown-keys"),
    sumQuarantinedKeys: document.getElementById("sum-quarantined-keys"),
    sumGlobalRpm: document.getElementById("sum-global-rpm"),
    sumGlobalTpm: document.getElementById("sum-global-tpm"),
    sumTotalTokens: document.getElementById("sum-total-tokens"),
    sumTotal429s: document.getElementById("sum-total-429s"),
    countTabAll: document.getElementById("count-tab-all"),
    countTabGemini: document.getElementById("count-tab-gemini"),
    countTabGroq: document.getElementById("count-tab-groq"),
    countTabOpenrouter: document.getElementById("count-tab-openrouter"),
    apiKeySearch: document.getElementById("api-key-search"),
    keyCardsContainer: document.getElementById("key-cards-container"),
    btnResetAllCooldowns: document.getElementById("btn-reset-all-cooldowns"),

    // Panels
    errorBreakdownList: document.getElementById("error-breakdown-list"),
    timelineChart: document.getElementById("timeline-chart"),
    logsCountBadge: document.getElementById("logs-count-badge"),
    searchInput: document.getElementById("search-input"),
    filterStatus: document.getElementById("filter-status"),
    filterErrorType: document.getElementById("filter-error-type"),
    telemetryTbody: document.getElementById("telemetry-tbody"),

    // Modal
    modal: document.getElementById("inspector-modal"),
    modalReqId: document.getElementById("modal-req-id"),
    modalContent: document.getElementById("modal-content"),
    modalCloseBtn: document.getElementById("modal-close-btn"),
    modalDoneBtn: document.getElementById("modal-done-btn"),
  };

  // Helper formatting functions
  function formatIsoTime(isoStr) {
    if (!isoStr) return "--:--:--";
    try {
      const d = new Date(isoStr);
      return d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return isoStr.slice(11, 19);
    }
  }

  function formatUptime(seconds) {
    if (seconds == null || isNaN(seconds)) return "--";
    const s = Math.floor(seconds);
    const hrs = Math.floor(s / 3600);
    const mins = Math.floor((s % 3600) / 60);
    const secs = s % 60;
    if (hrs > 0) return `${hrs}h ${mins}m ${secs}s`;
    if (mins > 0) return `${mins}m ${secs}s`;
    return `${secs}s`;
  }

  // 1. Fetch Backend Live Status (Port 8000)
  async function pollBackendStatus() {
    try {
      const res = await fetch("/api/dashboard/backend-status");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      if (data.online) {
        el.statusPill.className = "status-pill status-pill-on";
        el.statusText.textContent = `BACKEND: ON • 127.0.0.1:8000 (${data.ping_ms}ms)`;

        el.backendStatusCardLabel.textContent = "ON";
        el.backendStatusCardLabel.style.color = "var(--emerald)";
        el.backendPingMs.textContent = `${data.ping_ms} ms`;
        if (data.active_model_label || data.active_model) {
          if (el.backendActiveModelBadge) el.backendActiveModelBadge.textContent = data.active_model_label || data.active_model;
        }
        if (el.backendKeysBadge) {
          const gemKeys = data.details?.gemini_keys_loaded ?? 4;
          const groqKeys = data.details?.groq_keys_loaded ?? 7;
          const orKeys = data.details?.openrouter_keys_loaded ?? 5;
          el.backendKeysBadge.textContent = `Gemini (${gemKeys}) • Groq (${groqKeys}) • OR (${orKeys})`;
        }
        const uptimeSec = data.details?.uptime_seconds;
        el.backendUptime.textContent = formatUptime(uptimeSec);
      } else {
        el.statusPill.className = "status-pill status-pill-off";
        el.statusText.textContent = "BACKEND: OFF • 127.0.0.1:8000 Unreachable";

        el.backendStatusCardLabel.textContent = "OFF";
        el.backendStatusCardLabel.style.color = "var(--rose)";
        el.backendPingMs.textContent = "Offline";
        if (el.backendKeysBadge) el.backendKeysBadge.textContent = "--";
        el.backendUptime.textContent = "Unreachable";
      }
    } catch (err) {
      el.statusPill.className = "status-pill status-pill-off";
      el.statusText.textContent = "BACKEND: OFF • Dashboard Disconnected";
      el.backendStatusCardLabel.textContent = "OFF";
      el.backendStatusCardLabel.style.color = "var(--rose)";
      el.backendPingMs.textContent = "Error";
      el.backendUptime.textContent = "--";
    }
  }

  // 2. Fetch Aggregated Metrics & Timeline
  async function pollMetrics() {
    try {
      const res = await fetch(`/api/dashboard/metrics?window=${state.timeWindow}`);
      if (!res.ok) return;
      const data = await res.json();

      // Overall Performance & Health Grade
      if (data.total_requests === 0 || data.health_grade === "STANDBY") {
        el.perfScore.textContent = "100.0";
        el.perfProgressBar.style.width = "100%";
        el.healthGradeBadge.textContent = "STANDBY (0 Requests)";
        el.healthGradeBadge.style.color = "#38bdf8";
        el.healthGradeBadge.style.borderColor = "rgba(56, 189, 248, 0.4)";
      } else {
        el.perfScore.textContent = data.overall_performance.toFixed(1);
        el.perfProgressBar.style.width = `${Math.min(100, data.overall_performance)}%`;
        el.healthGradeBadge.textContent = data.health_grade;

        // Grade badge styling
        if (data.health_grade.startsWith("A")) {
          el.healthGradeBadge.style.color = "#34d399";
          el.healthGradeBadge.style.borderColor = "rgba(16, 185, 129, 0.4)";
        } else if (data.health_grade === "B") {
          el.healthGradeBadge.style.color = "#38bdf8";
          el.healthGradeBadge.style.borderColor = "rgba(56, 189, 248, 0.4)";
        } else if (data.health_grade === "C" || data.health_grade === "D") {
          el.healthGradeBadge.style.color = "#fbbf24";
          el.healthGradeBadge.style.borderColor = "rgba(245, 158, 11, 0.4)";
        } else {
          el.healthGradeBadge.style.color = "#f87171";
          el.healthGradeBadge.style.borderColor = "rgba(239, 68, 68, 0.4)";
        }
      }

      // Total Requests & Counts
      el.totalRequests.textContent = data.total_requests.toLocaleString();
      el.successCount.textContent = data.success_count.toLocaleString();
      el.failureCount.textContent = data.failure_count.toLocaleString();

      // Success vs Error Rates
      el.successRate.textContent = `${data.success_rate.toFixed(1)}%`;
      el.errorRate.textContent = `${data.error_rate.toFixed(1)}%`;
      el.splitBarSuccess.style.width = `${data.success_rate}%`;
      el.splitBarError.style.width = `${data.error_rate}%`;

      // Strict Accuracy
      el.accuracyRate.textContent = `${data.accuracy_rate.toFixed(1)}%`;
      el.accuracyProgressBar.style.width = `${data.accuracy_rate}%`;
      el.accurateCount.textContent = data.accurate_count.toLocaleString();

      // Latencies
      el.avgLatency.textContent = Math.round(data.avg_latency_ms);
      el.p95Latency.textContent = `${Math.round(data.p95_latency_ms)} ms`;
      el.minLatency.textContent = `${Math.round(data.min_latency_ms)} ms`;
      el.maxLatency.textContent = `${Math.round(data.max_latency_ms)} ms`;

      // Render Error Breakdown
      renderErrorBreakdown(data.error_breakdown || {});

      // Render Timeline Stream (last 50)
      renderTimeline(data.timeline || []);
    } catch (err) {
      console.warn("Failed to load telemetry metrics:", err);
    }
  }

  function renderErrorBreakdown(breakdown) {
    el.errorBreakdownList.innerHTML = "";
    const entries = Object.entries(breakdown);
    if (!entries.length) {
      el.errorBreakdownList.innerHTML = `<div class="timeline-empty-state">No error breakdown data.</div>`;
      return;
    }

    entries.forEach(([errType, stats]) => {
      const isNone = errType === "NONE";
      const isHalluc = errType === "HALLUCINATED_OPTION" || errType === "EMPTY_ANSWER";
      const fillClass = isNone ? "fill-none" : isHalluc ? "fill-amber" : "fill-rose";

      const item = document.createElement("div");
      item.className = "error-bar-item";
      item.innerHTML = `
        <div class="error-bar-labels">
          <span class="error-name">${errType}</span>
          <span class="error-stats">${stats.count} (${stats.percentage.toFixed(1)}%)</span>
        </div>
        <div class="error-track">
          <div class="error-fill ${fillClass}" style="width: ${Math.min(100, Math.max(stats.count > 0 ? 3 : 0, stats.percentage))}%"></div>
        </div>
      `;
      el.errorBreakdownList.appendChild(item);
    });
  }

  function renderTimeline(timeline) {
    el.timelineChart.innerHTML = "";
    if (!timeline.length) {
      el.timelineChart.innerHTML = `<div class="timeline-empty-state">No requests recorded in window yet.</div>`;
      return;
    }

    // Determine max latency to scale vertical heights (clamped 1000 - 15000)
    const maxLat = Math.max(1000, ...timeline.map((t) => t.latency_ms));

    timeline.forEach((req) => {
      const bar = document.createElement("div");
      bar.className = "timeline-bar";

      // Height scaled between 15% and 100%
      const heightPct = Math.max(12, Math.min(100, (req.latency_ms / maxLat) * 100));
      bar.style.height = `${heightPct}%`;

      // Outcome Color: Emerald (Accurate/Success), Amber (Hallucinated/Empty), Rose (Fatal/Error)
      if (req.is_accurate && req.status === "SUCCESS") {
        bar.style.backgroundColor = "var(--emerald)";
      } else if (req.error_type === "HALLUCINATED_OPTION" || req.error_type === "EMPTY_ANSWER") {
        bar.style.backgroundColor = "var(--amber)";
      } else {
        bar.style.backgroundColor = "var(--rose)";
      }

      bar.title = `REQ #${req.id} • ${req.endpoint} • ${req.latency_ms}ms • ${req.error_type} (${req.status})`;
      bar.addEventListener("click", () => openInspectorModal(req.id));

      el.timelineChart.appendChild(bar);
    });
  }

  // 3. Fetch Request Logs Table
  async function pollRequests() {
    try {
      const params = new URLSearchParams({
        limit: "100",
        window: state.timeWindow,
      });
      if (state.statusFilter) params.set("status", state.statusFilter);
      if (state.errorFilter) params.set("error_type", state.errorFilter);
      if (state.searchTerm) params.set("search", state.searchTerm);

      const res = await fetch(`/api/dashboard/requests?${params.toString()}`);
      if (!res.ok) return;
      const data = await res.json();

      state.cachedRequests = data.logs || [];
      el.logsCountBadge.textContent = `${data.total.toLocaleString()} logs`;

      renderTable(data.logs || []);
    } catch (err) {
      console.warn("Failed to load requests list:", err);
    }
  }

  function renderTable(logs) {
    el.telemetryTbody.innerHTML = "";
    if (!logs.length) {
      el.telemetryTbody.innerHTML = `
        <tr>
          <td colspan="10" class="table-loading-cell">No requests match current filters.</td>
        </tr>
      `;
      return;
    }

    logs.forEach((log) => {
      const tr = document.createElement("tr");

      const isOk = log.status === "SUCCESS";
      const statusBadge = isOk
        ? `<span class="badge-status-ok">SUCCESS</span>`
        : `<span class="badge-status-fail">FAILURE</span>`;

      const accBadge = log.is_accurate
        ? `<span class="badge-acc-true">Valid</span>`
        : `<span class="badge-acc-false">Inaccurate</span>`;

      let errClass = "badge-error-type";
      if (log.error_type === "HALLUCINATED_OPTION" || log.error_type === "EMPTY_ANSWER") {
        errClass += " badge-error-halluc";
      } else if (log.error_type !== "NONE") {
        errClass += " badge-error-fatal";
      }

      const qSnippet = log.question_text ? escapeHtml(log.question_text.slice(0, 75)) : "<em>(Empty stem)</em>";
      const ansSnippet = log.selected_answer ? escapeHtml(String(log.selected_answer).slice(0, 45)) : "<em>--</em>";
      let modelBadge = "Solver";
      if (log.cache_hit) {
        modelBadge = `<span class="badge-cache-hit">Cache Hit</span>`;
      } else if (log.model_name) {
        const m = log.model_name.toLowerCase();
        if (m.includes("gemini")) {
          modelBadge = `<span class="badge-gemini" title="${escapeHtml(log.model_name)}">gemini-3.8-flash</span>`;
        } else if (m.includes("gpt-oss-120b")) {
          modelBadge = `<span class="badge-groq" title="${escapeHtml(log.model_name)}">gpt-oss-120b</span>`;
        } else if (m.includes("gpt-oss-20b")) {
          modelBadge = `<span class="badge-groq" title="${escapeHtml(log.model_name)}">gpt-oss-20b</span>`;
        } else if (m.includes("qwen")) {
          modelBadge = `<span class="badge-groq" title="${escapeHtml(log.model_name)}">qwen3.8-27b</span>`;
        } else {
          const shortName = log.model_name.split("/").pop() || log.model_name;
          modelBadge = `<span class="badge-model-other" title="${escapeHtml(log.model_name)}">${escapeHtml(shortName)}</span>`;
        }
      }

      tr.innerHTML = `
        <td class="table-id-cell">#${log.id}</td>
        <td class="table-time-cell">${formatIsoTime(log.timestamp)}</td>
        <td><span class="table-badge-type">${escapeHtml(log.q_type || "mcq")}</span></td>
        <td class="table-q-cell" title="${escapeHtml(log.question_text || '')}">${qSnippet}</td>
        <td class="table-ans-cell" title="${escapeHtml(String(log.selected_answer || ''))}">${ansSnippet}</td>
        <td>${accBadge}</td>
        <td>${statusBadge}</td>
        <td><span class="${errClass}">${log.error_type}</span></td>
        <td class="table-latency-cell">${Math.round(log.latency_ms)} ms</td>
        <td class="table-time-cell">${modelBadge}</td>
      `;

      tr.addEventListener("click", () => openInspectorModal(log.id));
      el.telemetryTbody.appendChild(tr);
    });
  }

  // 4. Deep Request Inspector Modal
  async function openInspectorModal(requestId) {
    try {
      const res = await fetch(`/api/dashboard/requests/${requestId}`);
      if (!res.ok) throw new Error("Request not found");
      const r = await res.json();

      el.modalReqId.textContent = `REQ #${r.id}`;

      // Build options list
      let optionsHtml = '<div class="timeline-empty-state">No options provided (Free-form / Code)</div>';
      if (Array.isArray(r.options) && r.options.length > 0) {
        const rawAns = String(r.selected_answer || "").trim();
        const selectedLetters = new Set(
          rawAns.split(/[\,\s]+/).map(s => s.trim().toUpperCase()).filter(s => /^[A-Z]$/.test(s))
        );
        const selectedIndices = Array.isArray(r.selected_indices) ? new Set(r.selected_indices) : new Set();
        if (typeof r.selected_index === "number" && r.selected_index >= 0) {
          selectedIndices.add(r.selected_index);
        }

        optionsHtml = r.options
          .map((opt, idx) => {
            const letter = String.fromCharCode(65 + idx);
            const isSelected =
              selectedIndices.has(idx) ||
              selectedLetters.has(letter) ||
              rawAns.toLowerCase() === String(opt).trim().toLowerCase();
            return `
              <div class="option-item ${isSelected ? "option-selected" : ""}">
                <span><strong>[${letter}]</strong> ${escapeHtml(opt)}</span>
                ${isSelected ? '<span class="option-selected-tag">Selected Choice</span>' : ""}
              </div>
            `;
          })
          .join("");
      }

      // Accuracy banner
      let accBannerClass = r.is_accurate ? "pass" : "fail";
      let accExplanation = r.is_accurate
        ? "Passed Zero-Hallucination verification: Answer maps directly to a valid choice."
        : `Failed Verification: Classified as ${r.error_type}. ${r.error_message || "Answer is invalid or absent."}`;

      el.modalContent.innerHTML = `
        <!-- Accuracy Check Banner -->
        <div class="accuracy-banner ${accBannerClass}">
          <strong>${r.is_accurate ? "STRICT ACCURACY: PASSED" : "STRICT ACCURACY: FAILED"}</strong>
          <span>${escapeHtml(accExplanation)}</span>
        </div>

        <!-- Question Stem -->
        <div>
          <div class="detail-section-title">Question Stem (${escapeHtml(r.subject || "General")} • ${escapeHtml(r.q_type || "mcq")})</div>
          <div class="detail-question-box">${escapeHtml(r.question_text || "(No stem provided)")}</div>
        </div>

        <!-- Options -->
        <div>
          <div class="detail-section-title">Input Choices & Selection</div>
          <div class="options-list">${optionsHtml}</div>
        </div>

        <!-- Latency & Model Execution Details -->
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
          <div class="option-item">
            <span>Latency: <strong>${Math.round(r.latency_ms)} ms</strong></span>
          </div>
          <div class="option-item">
            <span>Model / Engine: <strong>${escapeHtml(r.model_name || (r.cache_hit ? "Cache Hit" : "Default"))}</strong></span>
          </div>
        </div>

        <!-- Error Stack Trace if any -->
        ${
          r.error_message
            ? `
          <div>
            <div class="detail-section-title" style="color: var(--rose)">Reported Fault Message</div>
            <pre class="raw-json-pre" style="border-color: rgba(239, 68, 68, 0.4); color: #f87171">${escapeHtml(r.error_message)}</pre>
          </div>
        `
            : ""
        }

        <!-- Raw Metadata / Payload Preview -->
        <div>
          <div class="detail-section-title">Telemetry Record JSON</div>
          <pre class="raw-json-pre">${escapeHtml(JSON.stringify(r, null, 2))}</pre>
        </div>
      `;

      el.modal.classList.remove("hidden");
    } catch (err) {
      alert(`Could not load request #${requestId}: ${err.message}`);
    }
  }

  function closeModal() {
    el.modal.classList.add("hidden");
  }

  // 5. Clear Stored Logs
  async function handleClearLogs() {
    if (!confirm("Are you sure you want to clear all telemetry logs from QuizZy?")) {
      return;
    }
    try {
      const res = await fetch("/api/dashboard/clear", { method: "POST" });
      if (res.ok) {
        await Promise.all([pollMetrics(), pollRequests()]);
      }
    } catch (err) {
      alert(`Failed to clear logs: ${err.message}`);
    }
  }

  // 5. API Keys Observability & Live Quotas
  function formatTokens(num) {
    if (num == null || isNaN(num)) return "0";
    const n = Number(num);
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
    return String(n);
  }

  async function pollApiKeys() {
    try {
      const res = await fetch("/api/dashboard/api-keys");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.apiKeysData = data;

      // Update Summary Bar
      const summary = data.summary || {};
      if (el.sumTotalKeys) el.sumTotalKeys.textContent = summary.total_keys ?? 0;
      if (el.sumHealthyKeys) el.sumHealthyKeys.textContent = summary.healthy_keys ?? 0;
      if (el.sumCooldownKeys) el.sumCooldownKeys.textContent = summary.cooldown_keys ?? 0;
      if (el.sumQuarantinedKeys) el.sumQuarantinedKeys.textContent = summary.quarantined_keys ?? 0;
      if (el.sumGlobalRpm) el.sumGlobalRpm.textContent = summary.global_rpm ?? 0;
      if (el.sumGlobalTpm) el.sumGlobalTpm.textContent = formatTokens(summary.global_tpm ?? 0);
      if (el.sumTotalTokens) el.sumTotalTokens.textContent = formatTokens(summary.total_tokens ?? 0);
      if (el.sumTotal429s) el.sumTotal429s.textContent = summary.total_429s ?? 0;

      // Update Tab Badges
      const byProv = summary.by_provider || {};
      if (el.countTabAll) el.countTabAll.textContent = summary.total_keys ?? 16;
      if (el.countTabGemini) el.countTabGemini.textContent = byProv.gemini?.total ?? 4;
      if (el.countTabGroq) el.countTabGroq.textContent = byProv.groq?.total ?? 7;
      if (el.countTabOpenrouter) el.countTabOpenrouter.textContent = byProv.openrouter?.total ?? 5;

      renderKeyCards();
    } catch (err) {
      console.warn("Failed to poll API keys telemetry:", err);
    }
  }

  function renderKeyCards() {
    if (!el.keyCardsContainer || !state.apiKeysData) return;
    const keys = state.apiKeysData.keys || [];

    const provFilter = state.providerFilter.toLowerCase();
    const search = state.keySearchTerm.trim().toLowerCase();

    const filtered = keys.filter((k) => {
      if (provFilter !== "all" && k.provider.toLowerCase() !== provFilter) return false;
      if (search) {
        const matchLabel = (k.key_label || "").toLowerCase().includes(search);
        const matchKey = (k.masked_key || "").toLowerCase().includes(search);
        const matchModel = (k.last_model || "").toLowerCase().includes(search);
        if (!matchLabel && !matchKey && !matchModel) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      el.keyCardsContainer.innerHTML = `
        <div class="empty-state-box" style="grid-column: 1 / -1; padding: 32px; text-align: center; color: var(--text-dim);">
          No API keys match the selected provider and filter.
        </div>
      `;
      return;
    }

    el.keyCardsContainer.innerHTML = filtered.map((k) => {
      const statusLower = (k.status || "idle").toLowerCase();
      let statusPillClass = "status-pill-idle";
      let statusLabel = "IDLE";
      if (statusLower === "healthy") {
        statusPillClass = "status-pill-healthy";
        statusLabel = "HEALTHY";
      } else if (statusLower === "cooldown") {
        statusPillClass = "status-pill-cooldown";
        statusLabel = "COOLDOWN";
      } else if (statusLower === "quarantined") {
        statusPillClass = "status-pill-quarantined";
        statusLabel = "QUARANTINED";
      }

      const provClass = (k.provider || "gemini").toLowerCase();
      const provTag = (k.provider || "gemini").toUpperCase();

      const rpmUtil = k.rpm_utilization_pct || 0;
      let rpmColorClass = "progress-emerald";
      if (rpmUtil > 85) rpmColorClass = "progress-rose";
      else if (rpmUtil > 70) rpmColorClass = "progress-amber";

      const tpmUtil = k.tpm_utilization_pct || 0;
      let tpmColorClass = "progress-cyan";
      if (tpmUtil > 85) tpmColorClass = "progress-rose";
      else if (tpmUtil > 70) tpmColorClass = "progress-amber";

      const cdRemain = k.cooldown_remaining_s || 0;
      const winReset = k.window_reset_in_s || 0;
      const activeTimerSec = cdRemain > 0 ? cdRemain : winReset;
      let timerText = "Ready / Idle";
      let timerActive = false;
      if (activeTimerSec > 0) {
        timerActive = true;
        const mins = Math.floor(activeTimerSec / 60);
        const secs = Math.floor(activeTimerSec % 60);
        timerText = `Reset in ${mins}:${secs < 10 ? "0" : ""}${secs}`;
      }

      return `
        <div class="key-card status-${statusLower}" data-provider="${escapeHtml(k.provider)}" data-key-id="${escapeHtml(k.key_id)}">
          <div class="key-card-header">
            <div class="key-card-title-group">
              <span class="provider-tag ${provClass}">${escapeHtml(provTag)}</span>
              <h3 class="key-label">${escapeHtml(k.key_label || "API Key")}</h3>
            </div>
            <span class="status-pill ${statusPillClass}">
              <span class="pulse-indicator"></span>
              ${escapeHtml(statusLabel)}
            </span>
          </div>

          <div class="key-card-badge-row">
            <code class="masked-key-badge" title="Key ID: ${escapeHtml(k.key_id)}">${escapeHtml(k.masked_key || "------")}</code>
            <span class="key-last-model" title="${escapeHtml(k.last_model || "")}">${escapeHtml(k.last_model || "--")}</span>
          </div>

          <div class="key-card-metrics-grid">
            <div class="kcm-item">
              <span class="kcm-label">Reqs (24h/All)</span>
              <span class="kcm-value"><strong>${k.requests_24h ?? 0}</strong> / ${k.total_requests ?? 0}</span>
            </div>
            <div class="kcm-item">
              <span class="kcm-label">Success Rate</span>
              <span class="kcm-value text-emerald">${(k.success_rate ?? 100).toFixed(1)}%</span>
            </div>
            <div class="kcm-item">
              <span class="kcm-label">Avg Latency</span>
              <span class="kcm-value">${(k.avg_latency_ms ?? 0).toFixed(1)} ms</span>
            </div>
            <div class="kcm-item">
              <span class="kcm-label">429 / 503</span>
              <span class="kcm-value ${k.count_429 > 0 ? "text-rose" : "text-slate"}">${k.count_429 ?? 0} / ${k.count_503 ?? 0}</span>
            </div>
          </div>

          <div class="key-card-limits">
            <div class="limit-bar-row">
              <div class="limit-bar-labels">
                <span>RPM (60s)</span>
                <span>${k.rpm_current ?? 0} / ${k.rpm_limit ? k.rpm_limit.toLocaleString() : "--"} (${rpmUtil.toFixed(1)}%)</span>
              </div>
              <div class="progress-bar-container">
                <div class="progress-bar-fill ${rpmColorClass}" style="width: ${Math.min(100, Math.max(0, rpmUtil))}%"></div>
              </div>
            </div>
            <div class="limit-bar-row">
              <div class="limit-bar-labels">
                <span>TPM (60s)</span>
                <span>${formatTokens(k.tpm_current ?? 0)} / ${k.tpm_limit ? formatTokens(k.tpm_limit) : "--"} (${tpmUtil.toFixed(1)}%)</span>
              </div>
              <div class="progress-bar-container">
                <div class="progress-bar-fill ${tpmColorClass}" style="width: ${Math.min(100, Math.max(0, tpmUtil))}%"></div>
              </div>
            </div>
          </div>

          <div class="key-card-tokens-row">
            <span>Tokens: <strong>${formatTokens(k.prompt_tokens ?? 0)} in</strong> • <strong>${formatTokens(k.completion_tokens ?? 0)} out</strong>${k.thinking_tokens ? ` • <strong>${formatTokens(k.thinking_tokens)} think</strong>` : ""}</span>
          </div>

          <div class="key-card-footer">
            <div class="key-reset-timer ${timerActive ? "active-timer" : ""}" id="timer-${escapeHtml(k.key_id)}" data-remain="${activeTimerSec}">
              <svg class="timer-icon" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="12 6 12 12 16 14"></polyline>
              </svg>
              <span class="timer-text">${escapeHtml(timerText)}</span>
            </div>
            <button class="btn-reset-key-cooldown" data-key-id="${escapeHtml(k.key_id)}" title="Reset cooldown for this key">
              Reset
            </button>
          </div>
        </div>
      `;
    }).join("");

    const resetBtns = el.keyCardsContainer.querySelectorAll(".btn-reset-key-cooldown");
    resetBtns.forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const kId = e.currentTarget.getAttribute("data-key-id");
        if (kId) handleResetKeyCooldown(kId);
      });
    });
  }

  function startCountdownTicker() {
    if (state.countdownIntervalId) clearInterval(state.countdownIntervalId);
    state.countdownIntervalId = setInterval(() => {
      const timerEls = document.querySelectorAll(".key-reset-timer");
      timerEls.forEach((tel) => {
        let sec = parseFloat(tel.getAttribute("data-remain") || "0");
        if (sec > 0) {
          sec = Math.max(0, sec - 1);
          tel.setAttribute("data-remain", sec);
          const span = tel.querySelector(".timer-text");
          if (span) {
            if (sec > 0) {
              const mins = Math.floor(sec / 60);
              const s = Math.floor(sec % 60);
              span.textContent = `Reset in ${mins}:${s < 10 ? "0" : ""}${s}`;
            } else {
              tel.classList.remove("active-timer");
              span.textContent = "Ready / Idle";
            }
          }
        }
      });
    }, 1000);
  }

  async function handleResetKeyCooldown(keyId) {
    try {
      const res = await fetch("/api/dashboard/api-keys/reset-cooldown", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key_id: keyId }),
      });
      if (res.ok) {
        await pollApiKeys();
      }
    } catch (err) {
      console.warn("Failed to reset cooldown:", err);
    }
  }

  // Refresh All Telemetry
  async function refreshAll() {
    await Promise.all([pollBackendStatus(), pollMetrics(), pollRequests(), pollApiKeys()]);
  }

  // 6. Model Selection Management
  function populateModelDropdown(groups, selectedId) {
    if (!el.modelSelect || !groups || !groups.length) return;
    const currentVal = selectedId || el.modelSelect.value || "auto";
    el.modelSelect.innerHTML = "";

    groups.forEach((grp) => {
      if (grp.options && grp.options.length) {
        const optgroup = document.createElement("optgroup");
        optgroup.label = grp.label;
        grp.options.forEach((opt) => {
          const option = document.createElement("option");
          option.value = opt.id;
          option.textContent = opt.label;
          if (opt.id === currentVal) {
            option.selected = true;
          }
          optgroup.appendChild(option);
        });
        el.modelSelect.appendChild(optgroup);
      }
    });
    el.modelSelect.value = currentVal;
  }

  async function fetchModelSelection() {
    try {
      const res = await fetch("/api/dashboard/model-selection");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.catalog && data.catalog.groups && el.modelSelect) {
        populateModelDropdown(data.catalog.groups, data.selected_model);
      } else if (data.selected_model && el.modelSelect) {
        el.modelSelect.value = data.selected_model;
      }
      if (el.backendActiveModelBadge) {
        el.backendActiveModelBadge.textContent = data.selected_label || data.selected_model || "Auto";
      }
    } catch (err) {
      const stored = localStorage.getItem("quizzy_active_model");
      if (stored && el.modelSelect) {
        el.modelSelect.value = stored;
      }
    }
  }

  async function handleModelChange(modelId) {
    try {
      localStorage.setItem("quizzy_active_model", modelId);
      const res = await fetch("/api/dashboard/model-selection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelId }),
      });
      if (res.ok) {
        const data = await res.json();
        if (el.backendActiveModelBadge) {
          el.backendActiveModelBadge.textContent = data.selected_label || data.selected_model || "Auto";
        }
      }
    } catch (err) {
      console.warn("Failed to set model selection:", err);
    }
  }

  // Polling Management
  function startPolling() {
    stopPolling();
    if (state.autoRefresh) {
      state.timerId = setInterval(refreshAll, state.pollIntervalMs);
    }
  }

  function stopPolling() {
    if (state.timerId) {
      clearInterval(state.timerId);
      state.timerId = null;
    }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Event Listeners
  function initListeners() {
    // Model selection change
    if (el.modelSelect) {
      el.modelSelect.addEventListener("change", (e) => {
        handleModelChange(e.target.value);
      });
    }

    // Window change
    el.timeWindow.addEventListener("change", (e) => {
      state.timeWindow = e.target.value;
      refreshAll();
    });

    // Auto-refresh toggle
    el.autoRefreshToggle.addEventListener("change", (e) => {
      state.autoRefresh = e.target.checked;
      if (state.autoRefresh) {
        refreshAll();
        startPolling();
      } else {
        stopPolling();
      }
    });

    // Manual refresh
    el.btnRefresh.addEventListener("click", () => {
      refreshAll();
    });

    // Clear logs
    el.btnClear.addEventListener("click", handleClearLogs);

    // Status filter
    el.filterStatus.addEventListener("change", (e) => {
      state.statusFilter = e.target.value;
      pollRequests();
    });

    // Error filter
    el.filterErrorType.addEventListener("change", (e) => {
      state.errorFilter = e.target.value;
      pollRequests();
    });

    // Search input (debounced 300ms)
    el.searchInput.addEventListener("input", (e) => {
      clearTimeout(state.searchDebounceTimer);
      state.searchDebounceTimer = setTimeout(() => {
        state.searchTerm = e.target.value;
        pollRequests();
      }, 300);
    });

    // Modal close events
    el.modalCloseBtn.addEventListener("click", closeModal);
    el.modalDoneBtn.addEventListener("click", closeModal);
    el.modal.addEventListener("click", (e) => {
      if (e.target === el.modal) closeModal();
    });
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !el.modal.classList.contains("hidden")) {
        closeModal();
      }
    });

    // Provider Tabs Switching
    const provTabs = document.querySelectorAll(".prov-tab");
    provTabs.forEach((tab) => {
      tab.addEventListener("click", (e) => {
        provTabs.forEach((t) => t.classList.remove("active"));
        const btn = e.currentTarget;
        btn.classList.add("active");
        state.providerFilter = btn.getAttribute("data-provider") || "all";
        renderKeyCards();
      });
    });

    // API Key Search Input (debounced 200ms)
    if (el.apiKeySearch) {
      let keySearchTimer = null;
      el.apiKeySearch.addEventListener("input", (e) => {
        clearTimeout(keySearchTimer);
        keySearchTimer = setTimeout(() => {
          state.keySearchTerm = e.target.value;
          renderKeyCards();
        }, 200);
      });
    }

    // Reset All Cooldowns
    if (el.btnResetAllCooldowns) {
      el.btnResetAllCooldowns.addEventListener("click", () => {
        handleResetKeyCooldown("all");
      });
    }
  }

  // App Initialization
  async function init() {
    initListeners();
    startCountdownTicker();
    await Promise.all([fetchModelSelection(), refreshAll()]);
    startPolling();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

