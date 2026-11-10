/**
 * QuizZy - Side Panel Controller & UI State Manager (Phase 3 Auto Mode Integrated)
 */

"use strict";

document.addEventListener("DOMContentLoaded", () => {
  // Popup Window Mode Support (for zero-DOM proctored coding pages)
  try {
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get("mode") === "popup") {
      document.body.classList.add("popup-mode");
      const launchBanner = document.querySelector(".hud-launch-banner");
      if (launchBanner) launchBanner.style.display = "none";
    }
  } catch (_) { }

  // DOM Elements - Header & Status
  const statusPill = document.getElementById("backend-status-pill");
  const statusText = document.getElementById("backend-status-text");
  const btnRefreshHealth = document.getElementById("btn-refresh-health");

  // DOM Elements - Manual Controls
  const inputSubject = document.getElementById("input-subject");
  const selectQType = document.getElementById("select-qtype");
  const btnExtractSolve = document.getElementById("btn-extract-solve");
  const btnExtractOnly = document.getElementById("btn-extract-only");
  const btnSolveOnly = document.getElementById("btn-solve-only");

  // DOM Elements - Notifications & Preview
  const alertBox = document.getElementById("panel-alert");
  const alertText = document.getElementById("panel-alert-text");
  const btnCloseAlert = document.getElementById("btn-close-alert");

  const previewQuestion = document.getElementById("preview-question");
  const previewOptionsContainer = document.getElementById("preview-options-container");
  const previewCountBadge = document.getElementById("preview-count-badge");

  // DOM Elements - Solution Display
  const solutionIdleState = document.getElementById("solution-idle-state");
  const solutionLoadingState = document.getElementById("solution-loading-state");
  const loadingStatusText = document.getElementById("loading-status-text");
  const bannerUncertain = document.getElementById("banner-uncertain");
  const uncertainReasonText = document.getElementById("uncertain-reason-text");

  const solutionContent = document.getElementById("solution-content");
  const solutionMcqContainer = document.getElementById("solution-mcq-container");
  const solutionMcqOptions = document.getElementById("solution-mcq-options");
  const solutionOrderContainer = document.getElementById("solution-order-container");
  const solutionOrderItems = document.getElementById("solution-order-items");
  const solutionTextContainer = document.getElementById("solution-text-container");
  const solutionTextVal = document.getElementById("solution-text-val");
  const btnCopyAnswer = document.getElementById("btn-copy-answer");
  const copyBtnLabel = document.getElementById("copy-btn-label");

  // Code Solution Display (Phase 7)
  const solutionCodeContainer = document.getElementById("solution-code-container");
  const solutionCodeVal = document.getElementById("solution-code-val");
  const solutionCodeLang = document.getElementById("solution-code-lang");
  const btnCopyCode = document.getElementById("btn-copy-code");
  const copyCodeBtnLabel = document.getElementById("copy-code-btn-label");
  const btnApplyCode = document.getElementById("btn-apply-code");

  const solutionReason = document.getElementById("solution-reason");

  const badgeConfidence = document.getElementById("badge-confidence");
  const badgeCache = document.getElementById("badge-cache");
  const badgeLatency = document.getElementById("badge-latency");
  const badgeSearxng = document.getElementById("badge-searxng");
  const solutionSearxngSources = document.getElementById("solution-searxng-sources");
  const searxngSourcesList = document.getElementById("searxng-sources-list");

  // DOM Elements - Feedback & Wrong Answer Overrides (Phase 5)
  const btnFlagWrong = document.getElementById("btn-flag-wrong");
  const btnSaveEval = document.getElementById("btn-save-eval");
  const feedbackCorrectionBox = document.getElementById("feedback-correction-box");
  const btnCloseCorrection = document.getElementById("btn-close-correction");
  const inputCorrectAnswer = document.getElementById("input-correct-answer");
  const inputFeedbackNotes = document.getElementById("input-feedback-notes");
  const btnApplyOverride = document.getElementById("btn-apply-override");
  const feedbackStatusMsg = document.getElementById("feedback-status-msg");
  const correctionInputHint = document.getElementById("correction-input-hint");

  // DOM Elements - Auto Mode Control Center (Phase 3)
  const badgeHostAllowed = document.getElementById("badge-host-allowed");
  const badgeHostBlocked = document.getElementById("badge-host-blocked");
  const automodeHostDisplay = document.getElementById("automode-host-display");
  const btnToggleHostAllow = document.getElementById("btn-toggle-host-allow");
  const inputMaxSteps = document.getElementById("input-max-steps");
  const inputStepDelay = document.getElementById("input-step-delay");
  const selectOrderMode = document.getElementById("select-order-mode");
  const chkRequireHigh = document.getElementById("chk-require-high") || document.getElementById("chk-high-agree-only");
  const btnApplyOnce = document.getElementById("btn-apply-once");
  const btnSwipeAdvance = document.getElementById("btn-swipe-advance");
  const btnStartAuto = document.getElementById("btn-start-auto");
  const btnKillSwitch = document.getElementById("btn-kill-switch");
  const btnResumeAuto = document.getElementById("btn-resume-auto");

  const telemetryStateBadge = document.getElementById("telemetry-state-badge");
  const telemetryStepCounter = document.getElementById("telemetry-step-counter");
  const telemetryLog = document.getElementById("telemetry-log");

  // DOM Elements - Settings Drawer
  const activeHostBadge = document.getElementById("active-host-badge");
  const inputQuestionSel = document.getElementById("setting-question-selector");
  const inputOptionsSel = document.getElementById("setting-options-selector");
  const inputTextInputSel = document.getElementById("setting-textinput-selector");
  const inputSubjectSel = document.getElementById("setting-subject-selector");
  const inputNextSel = document.getElementById("setting-next-selector");
  const btnSaveSelectors = document.getElementById("btn-save-selectors");
  const btnResetSelectors = document.getElementById("btn-reset-selectors");
  const toggleDomHighlight = document.getElementById("toggle-dom-highlight");
  const toggleBypassCache = document.getElementById("toggle-bypass-cache");

  // DOM Elements - Mode Switcher Pill (MCQ | CODE)
  const btnModeMcq = document.getElementById("btn-mode-mcq");
  const btnModeCode = document.getElementById("btn-mode-code");

  function setSidepanelMode(mode) {
    if (mode === "code") {
      if (btnModeCode) btnModeCode.classList.add("active");
      if (btnModeMcq) btnModeMcq.classList.remove("active");
      if (selectQType) selectQType.value = "code";
    } else {
      if (btnModeMcq) btnModeMcq.classList.add("active");
      if (btnModeCode) btnModeCode.classList.remove("active");
      if (selectQType && selectQType.value === "code") selectQType.value = "auto";
    }
  }

  if (btnModeMcq) {
    btnModeMcq.addEventListener("click", () => setSidepanelMode("mcq"));
  }
  if (btnModeCode) {
    btnModeCode.addEventListener("click", () => setSidepanelMode("code"));
  }
  if (selectQType) {
    selectQType.addEventListener("change", () => {
      if (selectQType.value === "code") {
        setSidepanelMode("code");
      } else {
        setSidepanelMode("mcq");
      }
    });
  }

  // In-memory state
  let currentHost = "localhost";
  let activeTabUrl = "";
  let activeTabId = null;
  let hostAllowlist = ["localhost", "127.0.0.1", "::1", "learning.ccbp.in"];
  let extractedState = {
    subject: "General",
    q_type: "auto",
    question: "",
    options: []
  };
  let latestSolution = null;

  // Auto Mode State Machine
  let isAutoRunning = false;
  let killSwitchTriggered = false;
  let autoStepIndex = 0;
  let autoMaxSteps = 10;
  let autoStepDelay = 800;
  let previousQuestionText = "";
  let previousQuestionSignature = "";
  let sameQuestionCount = 0;

  /**
   * Generates a composite question signature combining the full question text
   * (including multi-line code blocks) and the normalized options list.
   * This guarantees that consecutive questions with identical prompts (e.g. "Find the output:")
   * but different code snippets or options are never falsely detected as stuck.
   */
  function getQuestionSignature(extracted) {
    if (!extracted || !extracted.question) return "";
    const qStr = String(extracted.question || "").trim();
    const optsStr = Array.isArray(extracted.options)
      ? extracted.options.map((o) => (typeof o === "string" ? o.trim() : JSON.stringify(o))).join("|")
      : "";
    return `${qStr}||${optsStr}`;
  }

  /**
   * Deterministically polls the host page until the composite question signature changes,
   * or until maxWaitMs expires.
   */
  function waitForQuestionChange(oldSignature, onReady, maxWaitMs = 3500) {
    const start = Date.now();
    const pollInterval = 60;

    function poll() {
      if (!isAutoRunning || killSwitchTriggered) return;

      performExtraction((newExtracted) => {
        if (!isAutoRunning || killSwitchTriggered) return;

        const newSig = getQuestionSignature(newExtracted);
        if (newSig && newSig !== oldSignature) {
          onReady(newExtracted);
          return;
        }

        if (Date.now() - start >= maxWaitMs) {
          onReady(newExtracted);
          return;
        }

        setTimeout(poll, pollInterval);
      });
    }

    setTimeout(poll, 40);
  }

  // ==========================================================================
  // Alerts & Notification Helpers
  // ==========================================================================
  function showAlert(msg, isError = true) {
    if (!alertBox || !alertText) return;
    alertText.textContent = msg;
    alertBox.classList.remove("hidden");
    if (isError) {
      alertBox.style.background = "rgba(37, 99, 235, 0.25)";
      alertBox.style.borderColor = "#3B82F6";
      alertBox.style.color = "#FFFFFF";
    } else {
      alertBox.style.background = "rgba(96, 165, 250, 0.25)";
      alertBox.style.borderColor = "#60A5FA";
      alertBox.style.color = "#FFFFFF";
    }
  }

  function hideAlert() {
    if (alertBox) alertBox.classList.add("hidden");
  }

  if (btnCloseAlert) {
    btnCloseAlert.addEventListener("click", hideAlert);
  }

  // ==========================================================================
  // Safe Extension Messaging Helper
  // ==========================================================================
  function safeSendMessage(message, callback) {
    if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.sendMessage) {
      if (typeof callback === "function") callback(null, new Error("Runtime unavailable"));
      return;
    }
    try {
      chrome.runtime.sendMessage(message, (response) => {
        const err = (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.lastError) ? chrome.runtime.lastError : null;
        if (typeof callback === "function") {
          try {
            callback(response, err);
          } catch (_) {}
        }
      });
    } catch (syncErr) {
      if (typeof callback === "function") {
        try {
          callback(null, syncErr);
        } catch (_) {}
      }
    }
  }

  // ==========================================================================
  // Backend Health Check
  // ==========================================================================
  async function checkBackendHealth() {
    statusPill.className = "status-pill status-offline";
    statusText.textContent = "Connecting...";

    try {
      safeSendMessage({ action: "CHECK_HEALTH" }, (response) => {
        if (!response || !response.success) {
          statusPill.className = "status-pill status-offline";
          statusText.textContent = "Offline";
          statusPill.title = "Backend is offline. Run 'uvicorn backend.app:app' on http://127.0.0.1:8000";
          return;
        }
        statusPill.className = "status-pill status-online";
        statusText.textContent = "Connected";
        const cacheCount = response.data.cache_entries || 0;
        statusPill.title = `Backend online | SQLite Cache: ${cacheCount} entries`;
      });
    } catch (e) {
      statusPill.className = "status-pill status-offline";
      statusText.textContent = "Offline";
    }
  }

  if (btnRefreshHealth) {
    btnRefreshHealth.addEventListener("click", () => {
      checkBackendHealth();
      if (statusText && statusText.textContent.toLowerCase().includes("offline")) {
        safeSendMessage({ action: "START_BACKEND" });
      }
    });
  }

  // ==========================================================================
  // Own-App Host Allowlist
  // ==========================================================================
  function isCurrentHostAllowed() {
    if (!currentHost) return false;
    const clean = currentHost.toLowerCase().trim();
    if (clean === "localhost" || clean === "127.0.0.1" || clean === "::1" || clean === "null" || clean === "file:") {
      return true;
    }
    if (activeTabUrl && activeTabUrl.startsWith("file:///")) {
      return true;
    }
    if (clean === "learning.ccbp.in" || clean.endsWith(".ccbp.in")) {
      return true;
    }
    return hostAllowlist.includes(clean);
  }

  function updateAllowlistUI() {
    if (!automodeHostDisplay) return;
    automodeHostDisplay.textContent = currentHost || "unknown";

    const allowed = isCurrentHostAllowed();
    if (allowed) {
      badgeHostAllowed.classList.remove("hidden");
      badgeHostBlocked.classList.add("hidden");
      btnToggleHostAllow.textContent = "Revoke Host";
      btnToggleHostAllow.classList.remove("btn-primary");
      btnToggleHostAllow.classList.add("btn-secondary");
    } else {
      badgeHostAllowed.classList.add("hidden");
      badgeHostBlocked.classList.remove("hidden");
      btnToggleHostAllow.textContent = "Allow Current Host";
      btnToggleHostAllow.classList.remove("btn-secondary");
      btnToggleHostAllow.classList.add("btn-primary");
    }
  }

  btnToggleHostAllow.addEventListener("click", () => {
    const clean = (currentHost || "").toLowerCase().trim();
    if (!clean) return;

    if (isCurrentHostAllowed()) {
      hostAllowlist = hostAllowlist.filter(h => h !== clean);
      showAlert(`Host "${clean}" removed from Auto Mode allowlist.`);
    } else {
      if (!hostAllowlist.includes(clean)) {
        hostAllowlist.push(clean);
      }
      showAlert(`Host "${clean}" added to Own-App Allowlist.`, false);
    }

    chrome.storage.local.set({ auto_mode_allowlist: hostAllowlist, allowedHosts: hostAllowlist }, () => {
      updateAllowlistUI();
      setTimeout(hideAlert, 2500);
    });
  });

  // ==========================================================================
  // Telemetry Strip Updates (Bounded Ring Buffer <= 50 entries)
  // ==========================================================================
  const MAX_LOG_HISTORY = 50;
  const inMemoryTelemetryHistory = [];

  function setTelemetryState(stateKey, logText = "") {
    if (!telemetryStateBadge) return;

    telemetryStateBadge.className = "badge";
    const stateMap = {
      IDLE: { label: "IDLE", cls: "badge-idle" },
      RUNNING: { label: "RUNNING", cls: "badge-running" },
      PAUSED_ORDER_SWAP: { label: "PAUSED (ORDER SWAP)", cls: "badge-paused" },
      STOPPED_UNCERTAIN: { label: "STOPPED (UNCERTAIN)", cls: "badge-uncertain" },
      STOPPED_MAX_STEPS: { label: "STOPPED (MAX STEPS)", cls: "badge-maxsteps" },
      STOPPED_KILL_SWITCH: { label: "STOPPED (KILL SWITCH)", cls: "badge-kill" },
      STOPPED_ERROR: { label: "STOPPED (ERROR)", cls: "badge-error" },
      COMPLETED: { label: "COMPLETED", cls: "badge-completed" }
    };

    const cfg = stateMap[stateKey] || { label: stateKey, cls: "badge-neutral" };
    telemetryStateBadge.textContent = cfg.label;
    telemetryStateBadge.classList.add(cfg.cls);

    if (logText && telemetryLog) {
      telemetryLog.textContent = logText;
      inMemoryTelemetryHistory.push({
        state: stateKey,
        text: logText,
        timestamp: Date.now()
      });
      while (inMemoryTelemetryHistory.length > MAX_LOG_HISTORY) {
        inMemoryTelemetryHistory.shift();
      }
      if (telemetryLog.childNodes && telemetryLog.childNodes.length > MAX_LOG_HISTORY) {
        while (telemetryLog.childNodes.length > MAX_LOG_HISTORY) {
          telemetryLog.removeChild(telemetryLog.firstChild);
        }
      }
    }
  }

  function setTelemetryCounter(current, max) {
    if (telemetryStepCounter) {
      telemetryStepCounter.textContent = `Step ${current} / ${max}`;
    }
  }

  function trimTelemetryLogs(maxEntries = 15) {
    const limit = Math.max(1, maxEntries);
    while (inMemoryTelemetryHistory.length > limit) {
      inMemoryTelemetryHistory.shift();
    }
    if (telemetryLog && telemetryLog.childNodes) {
      while (telemetryLog.childNodes.length > limit) {
        telemetryLog.removeChild(telemetryLog.firstChild);
      }
    }
  }

  if (typeof window !== "undefined") {
    window.__QUIZZY_TRIM_SIDEPANEL_LOGS__ = trimTelemetryLogs;
    window.__QUIZZY_SIDEPANEL_HISTORY__ = inMemoryTelemetryHistory;
  }

  // ==========================================================================
  // Active Tab & Settings Storage
  // ==========================================================================
  function getCustomSelectors() {
    return {
      questionSelector: (inputQuestionSel ? inputQuestionSel.value : "").trim(),
      optionsSelector: (inputOptionsSel ? inputOptionsSel.value : "").trim(),
      textInputSelector: (inputTextInputSel ? inputTextInputSel.value : "").trim(),
      subjectSelector: (inputSubjectSel ? inputSubjectSel.value : "").trim(),
      nextSelector: (inputNextSel ? inputNextSel.value : "").trim()
    };
  }

  function loadSettingsForHost(host) {
    chrome.storage.local.get([
      `selectors_${host}`,
      "subject",
      "domHighlightEnabled",
      "bypassCache",
      "auto_mode_allowlist",
      "auto_max_steps",
      "auto_step_delay",
      "auto_order_mode",
      "auto_require_high"
    ], (res) => {
      if (res[`selectors_${host}`]) {
        const sel = res[`selectors_${host}`];
        if (inputQuestionSel) inputQuestionSel.value = sel.questionSelector || "";
        if (inputOptionsSel) inputOptionsSel.value = sel.optionsSelector || "";
        if (inputTextInputSel) inputTextInputSel.value = sel.textInputSelector || "";
        if (inputSubjectSel) inputSubjectSel.value = sel.subjectSelector || "";
        if (inputNextSel) inputNextSel.value = sel.nextSelector || "";
      } else {
        if (inputQuestionSel) inputQuestionSel.value = "";
        if (inputOptionsSel) inputOptionsSel.value = "";
        if (inputTextInputSel) inputTextInputSel.value = "";
        if (inputSubjectSel) inputSubjectSel.value = "";
        if (inputNextSel) inputNextSel.value = "";
      }

      if (res.subject && inputSubject) {
        inputSubject.value = res.subject;
      }
      if (typeof res.domHighlightEnabled === "boolean" && toggleDomHighlight) {
        toggleDomHighlight.checked = res.domHighlightEnabled;
      } else if (toggleDomHighlight) {
        toggleDomHighlight.checked = false; // Default OFF: zero DOM writes
      }
      if (typeof res.bypassCache === "boolean" && toggleBypassCache) {
        toggleBypassCache.checked = res.bypassCache;
      }

      // Auto mode settings
      const loadedAllowlist = Array.isArray(res.auto_mode_allowlist) ? res.auto_mode_allowlist : (Array.isArray(res.allowedHosts) ? res.allowedHosts : null);
      if (loadedAllowlist) {
        hostAllowlist = loadedAllowlist;
      }
      if (!hostAllowlist.includes("learning.ccbp.in")) {
        hostAllowlist.push("learning.ccbp.in");
      }
      if (typeof res.auto_max_steps === "number" && inputMaxSteps) {
        inputMaxSteps.value = res.auto_max_steps;
      }
      if (typeof res.auto_step_delay === "number" && inputStepDelay) {
        inputStepDelay.value = res.auto_step_delay;
      } else if (inputStepDelay && !inputStepDelay.value) {
        inputStepDelay.value = 250;
      }
      if (res.auto_order_mode && selectOrderMode) {
        selectOrderMode.value = res.auto_order_mode;
      }
      if (typeof res.auto_require_high === "boolean" && chkRequireHigh) {
        chkRequireHigh.checked = res.auto_require_high;
      } else if (chkRequireHigh) {
        chkRequireHigh.checked = false;
      }

      updateAllowlistUI();

      // Auto-inject Floating HUD if connected on an allowed quiz tab
      if (isCurrentHostAllowed()) {
        chrome.runtime.sendMessage({ action: "INJECT_FLOATING_HUD", tabId: activeTabId }, () => {
          if (chrome.runtime.lastError) { }
        });
      }
    });
  }

  // Floating HUD Quick Launcher
  const btnLaunchHud = document.getElementById("btn-launch-hud");
  if (btnLaunchHud) {
    btnLaunchHud.addEventListener("click", () => {
      const dispatchLaunch = (targetId) => {
        chrome.runtime.sendMessage({ action: "LAUNCH_FLOATING_HUD", tabId: targetId }, (res) => {
          if (chrome.runtime.lastError || (res && !res.success)) {
            const err = chrome.runtime.lastError ? chrome.runtime.lastError.message : (res ? res.error : "Failed to launch HUD");
            showAlert(`Floating HUD: ${err}`, true);
          } else if (res && res.result && res.result.codingMode) {
            showAlert("Coding Mode: In-page HUD suppressed for stealth. Opened Popup HUD (Alt+M).", false);
            setTimeout(hideAlert, 3500);
          } else {
            showAlert("Floating HUD launched on active page!", false);
            setTimeout(hideAlert, 2000);
          }
        });
      };

      if (typeof chrome !== "undefined" && chrome.tabs && chrome.tabs.query) {
        chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
          const currentId = (tabs && tabs[0]) ? tabs[0].id : activeTabId;
          if (currentId) activeTabId = currentId;
          dispatchLaunch(currentId);
        });
      } else {
        dispatchLaunch(activeTabId);
      }
    });
  }

  function initActiveTab() {
    chrome.runtime.sendMessage({ action: "GET_ACTIVE_TAB" }, (response) => {
      if (chrome.runtime.lastError) {
        currentHost = "localhost";
        if (activeHostBadge) activeHostBadge.textContent = currentHost;
        loadSettingsForHost(currentHost);
        return;
      }
      if (response && response.success && response.tab && response.tab.url) {
        activeTabUrl = response.tab.url;
        activeTabId = response.tab.id;
        if (/\/question\/[a-z0-9-]+/i.test(activeTabUrl)) {
          setSidepanelMode("code");
        }
        try {
          const urlObj = new URL(response.tab.url);
          currentHost = urlObj.hostname || "localhost";
          if (activeHostBadge) activeHostBadge.textContent = currentHost;
          loadSettingsForHost(currentHost);
        } catch (e) {
          currentHost = "localhost";
          if (activeHostBadge) activeHostBadge.textContent = currentHost;
          loadSettingsForHost(currentHost);
        }
      } else {
        currentHost = "localhost";
        if (activeHostBadge) activeHostBadge.textContent = currentHost;
        loadSettingsForHost(currentHost);
      }
    });
  }

  // Save selectors for current host
  if (btnSaveSelectors) {
    btnSaveSelectors.addEventListener("click", () => {
      const selectors = getCustomSelectors();
      const key = `selectors_${currentHost}`;
      chrome.storage.local.set({ [key]: selectors }, () => {
        showAlert(`Selectors saved for host "${currentHost}"`, false);
        setTimeout(hideAlert, 2500);
      });
    });
  }

  // Reset selectors
  if (btnResetSelectors) {
    btnResetSelectors.addEventListener("click", () => {
      if (inputQuestionSel) inputQuestionSel.value = "";
      if (inputOptionsSel) inputOptionsSel.value = "";
      if (inputTextInputSel) inputTextInputSel.value = "";
      if (inputSubjectSel) inputSubjectSel.value = "";
      if (inputNextSel) inputNextSel.value = "";
      const key = `selectors_${currentHost}`;
      chrome.storage.local.remove([key], () => {
        showAlert(`Selectors reset for host "${currentHost}"`, false);
        setTimeout(hideAlert, 2500);
      });
    });
  }

  // Persist settings toggles
  if (toggleDomHighlight) {
    toggleDomHighlight.addEventListener("change", () => {
      chrome.storage.local.set({ domHighlightEnabled: toggleDomHighlight.checked });
      if (!toggleDomHighlight.checked) {
        safeSendMessage({
          action: "APPLY_HIGHLIGHT",
          payload: { remove: true }
        });
      }
    });
  }

  if (toggleBypassCache) {
    toggleBypassCache.addEventListener("change", () => {
      chrome.storage.local.set({ bypassCache: toggleBypassCache.checked });
    });
  }

  if (inputSubject) {
    inputSubject.addEventListener("change", () => {
      chrome.storage.local.set({ subject: inputSubject.value.trim() });
    });
  }

  if (inputMaxSteps) {
    inputMaxSteps.addEventListener("change", () => {
      const val = Math.min(100, Math.max(1, parseInt(inputMaxSteps.value) || 10));
      inputMaxSteps.value = val;
      chrome.storage.local.set({ auto_max_steps: val });
    });
  }

  if (inputStepDelay) {
    inputStepDelay.addEventListener("change", () => {
      const val = Math.min(5000, Math.max(300, parseInt(inputStepDelay.value) || 800));
      inputStepDelay.value = val;
      chrome.storage.local.set({ auto_step_delay: val });
    });
  }

  if (selectOrderMode) {
    selectOrderMode.addEventListener("change", () => {
      chrome.storage.local.set({ auto_order_mode: selectOrderMode.value });
    });
  }

  if (chkRequireHigh) {
    chkRequireHigh.addEventListener("change", () => {
      chrome.storage.local.set({ auto_require_high: chkRequireHigh.checked });
    });
  }

  // ==========================================================================
  // Render Extracted Question & Options Preview
  // ==========================================================================
  function renderPreview(data) {
    if (previewQuestion) previewQuestion.value = data.question || "";
    if (previewOptionsContainer) previewOptionsContainer.innerHTML = "";

    const count = (data.options || []).length;
    if (previewCountBadge) {
      previewCountBadge.textContent = `${count} options (${data.q_type.toUpperCase()})`;
    }

    if (count === 0) {
      if (previewOptionsContainer) {
        if (data.q_type === "text") {
          previewOptionsContainer.innerHTML = `<div class="empty-state-text">Free-text input question (no multiple-choice options).</div>`;
        } else {
          previewOptionsContainer.innerHTML = `<div class="empty-state-text">No option blocks detected.</div>`;
        }
      }
      return;
    }

    const letters = ["A", "B", "C", "D", "E", "F"];
    data.options.forEach((opt, idx) => {
      const chip = document.createElement("div");
      chip.className = "preview-option-chip";
      const letter = letters[idx] || String(idx + 1);
      chip.innerHTML = `
        <span class="preview-chip-label">${letter}</span>
        <span class="preview-chip-text">${escapeHtml(opt)}</span>
      `;
      if (previewOptionsContainer) previewOptionsContainer.appendChild(chip);
    });
  }

  // ==========================================================================
  // Render Solution Result
  // ==========================================================================
  function resetSolutionDisplay() {
    if (solutionIdleState) solutionIdleState.classList.add("hidden");
    if (solutionLoadingState) solutionLoadingState.classList.add("hidden");
    if (bannerUncertain) bannerUncertain.classList.add("hidden");
    if (solutionContent) solutionContent.classList.add("hidden");
    if (solutionMcqContainer) solutionMcqContainer.classList.add("hidden");
    if (solutionOrderContainer) solutionOrderContainer.classList.add("hidden");
    if (solutionTextContainer) solutionTextContainer.classList.add("hidden");
    if (solutionCodeContainer) solutionCodeContainer.classList.add("hidden");
    if (solutionSearxngSources) solutionSearxngSources.classList.add("hidden");
    if (searxngSourcesList) searxngSourcesList.innerHTML = "";

    if (badgeConfidence) {
      badgeConfidence.className = "badge badge-neutral";
      badgeConfidence.textContent = "IDLE";
    }
    if (badgeCache) badgeCache.classList.add("hidden");
    if (badgeLatency) badgeLatency.classList.add("hidden");
    if (badgeSearxng) badgeSearxng.classList.add("hidden");

    if (feedbackCorrectionBox) feedbackCorrectionBox.classList.add("hidden");
    if (feedbackStatusMsg) {
      feedbackStatusMsg.classList.add("hidden");
      feedbackStatusMsg.textContent = "";
      feedbackStatusMsg.className = "feedback-status-msg hidden";
    }
    if (inputCorrectAnswer) inputCorrectAnswer.value = "";
    if (inputFeedbackNotes) inputFeedbackNotes.value = "";
  }

  function renderSolution(solution, extracted) {
    latestSolution = solution;
    resetSolutionDisplay();

    // 1. Badges: Latency, Cache, & SearXNG
    if (badgeLatency) {
      badgeLatency.textContent = `${Math.round(solution.latency_ms)}ms`;
      badgeLatency.classList.remove("hidden");
    }

    if (badgeCache) {
      if (solution.cache_hit) {
        badgeCache.textContent = "CACHE HIT";
        badgeCache.className = "badge badge-cache";
        badgeCache.classList.remove("hidden");
      } else {
        badgeCache.textContent = "LIVE LLM";
        badgeCache.className = "badge badge-neutral";
        badgeCache.classList.remove("hidden");
      }
    }

    if (badgeSearxng) {
      if (solution.searxng_used) {
        badgeSearxng.textContent = "WEB VERIFIED (SearXNG)";
        badgeSearxng.className = "badge badge-searxng";
        badgeSearxng.classList.remove("hidden");
      } else {
        badgeSearxng.classList.add("hidden");
      }
    }

    // 2. Confidence Badge
    const confScore = Math.round(solution.confidence_score * 100);

    if (badgeConfidence) {
      if (solution.status === "uncertain" || solution.confidence_label === "uncertain") {
        badgeConfidence.textContent = "UNCERTAIN";
        badgeConfidence.className = "badge badge-uncertain";
      } else if (solution.confidence_tier === "USER_VERIFIED_OVERRIDE") {
        badgeConfidence.textContent = "USER VERIFIED (100%)";
        badgeConfidence.className = "badge badge-override";
      } else if (solution.confidence_tier === "MEDIUM_SEARXNG_GROUNDED") {
        badgeConfidence.textContent = "MEDIUM (75% - Web Grounded)";
        badgeConfidence.className = "badge badge-medium";
      } else if (solution.confidence_label === "high") {
        badgeConfidence.textContent = `HIGH (${confScore}%)`;
        badgeConfidence.className = "badge badge-high";
      } else if (solution.confidence_label === "medium") {
        badgeConfidence.textContent = `MEDIUM (${confScore}%)`;
        badgeConfidence.className = "badge badge-medium";
      } else {
        badgeConfidence.textContent = `LOW (${confScore}%)`;
        badgeConfidence.className = "badge badge-low";
      }
    }

    // Update correction input hint dynamically based on question format
    if (correctionInputHint) {
      if (solution.q_type === "mcq") {
        correctionInputHint.textContent = 'Enter correct option letter (e.g. "B") or exact text:';
      } else if (solution.q_type === "order") {
        correctionInputHint.textContent = 'Enter correct permutation (e.g. "C,A,D,B"):';
      } else {
        correctionInputHint.textContent = 'Enter correct free-text answer:';
      }
    }

    // 3. Handle Uncertain Status
    if (solution.status === "uncertain") {
      if (bannerUncertain) bannerUncertain.classList.remove("hidden");
      if (uncertainReasonText) {
        uncertainReasonText.textContent = solution.reason || "Models disagreed or tie-breaker was unavailable. Do not guess.";
      }
      return;
    }

    // 4. Handle Successful Solutions
    if (solutionContent) solutionContent.classList.remove("hidden");
    if (solutionReason) {
      solutionReason.textContent = solution.reason || "Consensus answer derived from dual models.";
    }

    // 5. Render SearXNG Search Sources if present
    if (solution.searxng_used && solution.search_sources && solution.search_sources.length > 0) {
      if (searxngSourcesList) {
        searxngSourcesList.innerHTML = "";
        solution.search_sources.forEach(src => {
          const item = document.createElement("div");
          item.className = "source-item";
          const bullet = document.createElement("span");
          bullet.className = "source-item-bullet";
          bullet.textContent = "•";
          const text = document.createElement("span");
          text.textContent = src;
          item.appendChild(bullet);
          item.appendChild(text);
          searxngSourcesList.appendChild(item);
        });
      }
      if (solutionSearxngSources) {
        solutionSearxngSources.classList.remove("hidden");
      }
    }

    const letters = ["A", "B", "C", "D", "E", "F"];

    // MCQ & Multi-Select Format Rendering
    if (solution.q_type === "mcq" || solution.q_type === "multi") {
      if (solutionMcqContainer) solutionMcqContainer.classList.remove("hidden");
      if (solutionMcqOptions) {
        solutionMcqOptions.innerHTML = "";

        const optionsList = (extracted && extracted.options && extracted.options.length > 0)
          ? extracted.options
          : (solution.selected_texts && solution.selected_texts.length > 0 ? solution.selected_texts : (solution.selected_text ? [solution.selected_text] : []));

        optionsList.forEach((optText, idx) => {
          const letter = letters[idx] || String(idx + 1);
          const isWinner = (solution.q_type === "multi")
            ? ((solution.selected_indices && solution.selected_indices.includes(idx)) || (solution.canonical_keys && solution.canonical_keys.includes(letter)))
            : (idx === solution.selected_index);

          const card = document.createElement("div");
          card.className = "solution-mcq-card" + (isWinner ? " option-winner" : "");

          card.innerHTML = `
            <div class="mcq-letter-badge">${letter}</div>
            <div class="mcq-text-content">
              <div>${escapeHtml(optText)}</div>
              ${isWinner ? `<div class="mcq-winner-tag">&#10003; WINNING ANSWER (Option #${idx + 1})</div>` : ""}
            </div>
          `;
          solutionMcqOptions.appendChild(card);
        });
      }

      // Opt-In Page DOM Highlighting (if explicitly enabled)
      if (toggleDomHighlight.checked && typeof solution.selected_index === "number") {
        safeSendMessage({
          action: "APPLY_HIGHLIGHT",
          payload: {
            selected_index: solution.selected_index,
            customSelectors: getCustomSelectors()
          }
        });
      }
    }

    // Order Format Rendering
    else if (solution.q_type === "order") {
      if (solutionOrderContainer) solutionOrderContainer.classList.remove("hidden");
      if (solutionOrderItems) {
        solutionOrderItems.innerHTML = "";

        const orderedTexts = solution.ordered_texts || [];
        const permIndices = solution.permutation_indices || [];

        orderedTexts.forEach((text, i) => {
          const origScreenIdx = (i < permIndices.length) ? permIndices[i] : i;
          const origLetter = letters[origScreenIdx] || String(origScreenIdx + 1);

          const item = document.createElement("div");
          item.className = "order-step-card";
          item.innerHTML = `
            <span class="order-position-pill">${i + 1}${getOrdinalSuffix(i + 1)}</span>
            <div class="order-text-content">${escapeHtml(text)}</div>
            <span class="order-swap-guide">&larr; Screen Block #${origScreenIdx + 1} (${origLetter})</span>
          `;
          solutionOrderItems.appendChild(item);
        });
      }
    }

    // Text Format Rendering
    else if (solution.q_type === "text") {
      if (solutionTextContainer) solutionTextContainer.classList.remove("hidden");
      if (solutionTextVal) solutionTextVal.textContent = solution.answer || "";

      if (btnCopyAnswer) {
        btnCopyAnswer.onclick = () => {
          navigator.clipboard.writeText(solution.answer || "").then(() => {
            if (copyBtnLabel) copyBtnLabel.textContent = "Copied!";
            setTimeout(() => {
              if (copyBtnLabel) copyBtnLabel.textContent = "Copy Answer";
            }, 1800);
          }).catch(() => {
            showAlert("Clipboard copy failed. Please copy manually.");
          });
        };
      }
    }

    // Code Format Rendering (Phase 7 Coding Practice Mode)
    else if (solution.q_type === "code") {
      if (solutionCodeContainer) solutionCodeContainer.classList.remove("hidden");
      const codeStr = solution.answer || solution.selected_text || "";
      if (solutionCodeVal) solutionCodeVal.textContent = codeStr;
      if (solutionCodeLang) {
        solutionCodeLang.textContent = (solution.language || (extracted && extracted.language) || "code").toUpperCase();
      }

      if (btnCopyCode) {
        btnCopyCode.onclick = () => {
          navigator.clipboard.writeText(codeStr).then(() => {
            if (copyCodeBtnLabel) copyCodeBtnLabel.textContent = "Copied!";
            setTimeout(() => {
              if (copyCodeBtnLabel) copyCodeBtnLabel.textContent = "Copy Code";
            }, 1800);
          }).catch(() => {
            showAlert("Clipboard copy failed. Please copy manually.");
          });
        };
      }

      if (btnApplyCode) {
        btnApplyCode.onclick = () => {
          chrome.runtime.sendMessage({
            action: "WRITE_CODE_EDITOR_MAIN",
            payload: {
              code: codeStr,
              language: solution.language || (extracted && extracted.language),
              starter_code: (extracted && extracted.starter_code)
            }
          }, (res) => {
            if (chrome.runtime.lastError) {
              showAlert(`Code injection failed: ${chrome.runtime.lastError.message}`);
              return;
            }
            if (res && res.success) {
              showAlert("Code successfully injected into editor!", false);
              setTimeout(hideAlert, 2000);
            } else {
              showAlert(`Code injection failed: ${(res && res.error) || "Editor not found"}`);
            }
          });
        };
      }
    }
  }

  // ==========================================================================
  // Core Action: Extract Quiz
  // ==========================================================================
  function performExtraction(callback, isDeepScan = false) {
    hideAlert();
    const forcedQType = selectQType ? selectQType.value : "auto";
    const selectors = getCustomSelectors();

    function handleSuccess(response) {
      // Progressive deep scan: if question found but 0 options on MCQ/order, wait 250ms for hydration and re-scan
      const needsOptions = response.q_type !== "text" && response.q_type !== "code";
      const hasFewOptions = !response.options || response.options.length < 2;
      if (response.success && response.question && needsOptions && hasFewOptions && !isDeepScan) {
        if (loadingStatusText) loadingStatusText.textContent = "Scanning dynamic DOM elements and options...";
        setTimeout(() => {
          performExtraction(callback, true);
        }, 250);
        return;
      }

      extractedState = response;
      renderPreview(response);

      if (response.subject && inputSubject && response.subject.trim() && response.subject !== "General") {
        inputSubject.value = response.subject;
        try {
          chrome.storage.local.set({ subject: response.subject });
        } catch (e) { }
      } else if (response.subject && inputSubject && (!inputSubject.value || inputSubject.value === "General")) {
        inputSubject.value = response.subject;
      }

      if (response.q_type && selectQType && selectQType.value === "auto") {
        selectQType.value = response.q_type;
      }

      if (callback) callback(response);
    }

    function handleFailure(response) {
      showAlert(response && response.error ? response.error : "Failed to extract quiz data from active tab.");
      if (response && response.partial) {
        if (response.partial.subject && inputSubject && response.partial.subject !== "General") {
          inputSubject.value = response.partial.subject;
        }
        renderPreview(response.partial);
      }
      if (callback) callback(null);
    }

    chrome.runtime.sendMessage({
      action: "EXTRACT_QUIZ",
      customSelectors: selectors,
      forcedQType: forcedQType
    }, (response) => {
      if (chrome.runtime.lastError) {
        showAlert(`Extraction failed: ${chrome.runtime.lastError.message}`);
        if (callback) callback(null);
        return;
      }

      if (!response || !response.success) {
        // Fallback: If custom selectors were provided but failed to match on this page, retry once with built-in discovery
        const hasCustom = selectors && (selectors.questionSelector || selectors.optionsSelector || selectors.textInputSelector);
        if (hasCustom) {
          chrome.runtime.sendMessage({
            action: "EXTRACT_QUIZ",
            customSelectors: {},
            forcedQType: forcedQType
          }, (retryResponse) => {
            if (chrome.runtime.lastError) {
              handleFailure(response);
              return;
            }
            if (retryResponse && retryResponse.success) {
              handleSuccess(retryResponse);
              return;
            }
            handleFailure(response);
          });
          return;
        }
        handleFailure(response);
        return;
      }

      handleSuccess(response);
    });
  }

  function isValidExtractedQuestion(q) {
    if (!q) return false;
    const stem = (q.stem || q.question_text || q.question || "").trim();
    if (stem.length < 2) return false;
    const type = (q.q_type || "mcq").toLowerCase();
    if (["mcq", "multi", "order"].includes(type)) {
      const validOpts = Array.isArray(q.options)
        ? q.options.filter(o => o && (typeof o === "string" ? o.trim().length > 0 : (typeof o === "object" ? String(o.text || o.label || o.value || "").trim().length > 0 : true)))
        : [];
      if (validOpts.length < 2) return false;
    }
    return true;
  }

  // ==========================================================================
  // Core Action: Solve Quiz
  // ==========================================================================
  function performSolve(extracted, callback) {
    if (!extracted || !isValidExtractedQuestion(extracted)) {
      showAlert("No valid question detected to solve (empty stem or insufficient options). Please extract or enter a valid question first.");
      if (callback) callback(null);
      return;
    }

    resetSolutionDisplay();
    if (solutionLoadingState) solutionLoadingState.classList.remove("hidden");
    if (loadingStatusText) loadingStatusText.textContent = "Analyzing via dual models with two-stage verification...";
    if (btnExtractSolve) btnExtractSolve.disabled = true;

    const payload = {
      subject: (extracted.subject && extracted.subject !== "General" ? extracted.subject : (inputSubject ? inputSubject.value || "General" : "General")).trim(),
      q_type: (selectQType && selectQType.value !== "auto") ? selectQType.value : extracted.q_type,
      question: (previewQuestion ? previewQuestion.value || extracted.question : extracted.question).trim(),
      options: extracted.options || [],
      language: (extracted && extracted.language) ? extracted.language : null,
      starter_code: (extracted && extracted.starter_code) ? extracted.starter_code : null,
      image_data: (extracted && extracted.image_data) ? extracted.image_data : null,
      image_url: (extracted && extracted.image_url) ? extracted.image_url : null,
      meta: (extracted && extracted.meta) ? extracted.meta : null,
      bypass_cache: toggleBypassCache ? toggleBypassCache.checked : false
    };

    chrome.runtime.sendMessage({
      action: "SOLVE_REQUEST",
      payload: payload
    }, (response) => {
      if (btnExtractSolve) btnExtractSolve.disabled = false;
      if (solutionLoadingState) solutionLoadingState.classList.add("hidden");

      if (chrome.runtime.lastError) {
        showAlert(`Solver request error: ${chrome.runtime.lastError.message}`);
        if (callback) callback(null);
        return;
      }

      if (!response || !response.success) {
        const errMsg = response && response.error ? response.error : "Failed to solve question. Check if backend is running.";
        const isRateLimited = (response && response.status === 429) || errMsg.includes("429") || errMsg.toLowerCase().includes("rate limit");
        showAlert(isRateLimited ? "Rate limit cooldown (10s)..." : errMsg);
        if (callback) callback(null);
        return;
      }

      renderSolution(response.data, extracted);
      if (callback) callback(response.data);
    });
  }

  // ==========================================================================
  // Phase 3: Emergency Kill Switch
  // ==========================================================================
  function triggerKillSwitch(reason = "User triggered emergency kill switch.") {
    if (!isAutoRunning) return;

    isAutoRunning = false;
    killSwitchTriggered = true;

    setTelemetryState("STOPPED_KILL_SWITCH", `EMERGENCY HALT: ${reason}`);
    showAlert(`Auto Mode Stopped: ${reason}`, true);

    // UI state restoration
    if (btnKillSwitch) btnKillSwitch.classList.add("hidden");
    if (btnStartAuto) {
      btnStartAuto.classList.remove("hidden");
      btnStartAuto.disabled = false;
    }
    if (btnResumeAuto) btnResumeAuto.classList.add("hidden");
  }

  if (btnKillSwitch) {
    btnKillSwitch.addEventListener("click", () => {
      triggerKillSwitch("Panic button clicked");
    });
  }

  // Listen for background notifications (tab switched, navigated away, log trim)
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.action === "KILL_SWITCH_TRIGGERED") {
      triggerKillSwitch(msg.reason || "Background navigation/tab switch event");
    }
    if (msg && (msg.type === "QUIZZY_TRIM_SIDEPANEL_LOGS" || msg.action === "QUIZZY_TRIM_SIDEPANEL_LOGS")) {
      trimTelemetryLogs(msg.maxEntries || 15);
    }
  });

  // ==========================================================================
  // Phase 3: Single-Step Apply Action
  // ==========================================================================
  function applySolutionAction(solution, callback) {
    if (!solution) {
      showAlert("No solution available to apply. Please solve first.");
      if (callback) callback(false);
      return;
    }

    if (!isCurrentHostAllowed()) {
      showAlert(`Action blocked: Host "${currentHost}" is not in Own-App Allowlist.`);
      if (callback) callback(false);
      return;
    }

    if (solution.q_type === "code") {
      const code = solution.answer || solution.selected_text || "";
      chrome.runtime.sendMessage({
        action: "WRITE_CODE_EDITOR_MAIN",
        payload: {
          code,
          language: solution.language || (extractedState && extractedState.language),
          starter_code: solution.starter_code || (extractedState && extractedState.starter_code)
        }
      }, (res) => {
        if (chrome.runtime.lastError) {
          showAlert(`Code injection failed: ${chrome.runtime.lastError.message}`);
          if (callback) callback(false, null);
          return;
        }
        if (res && res.success) {
          showAlert("Code successfully injected into editor!", false);
          setTimeout(hideAlert, 2000);
          if (callback) callback(true, res);
        } else {
          showAlert(`Code injection failed: ${(res && res.error) || "Editor not found"}`);
          if (callback) callback(false, res);
        }
      });
      return;
    }

    const selectors = getCustomSelectors();
    const actionPayload = {
      q_type: solution.q_type,
      selected_index: solution.selected_index,
      selected_text: solution.selected_text,
      selected_indices: solution.selected_indices,
      selected_texts: solution.selected_texts,
      canonical_keys: solution.canonical_keys,
      canonical_key: solution.canonical_key,
      answer: solution.answer,
      permutation_indices: solution.permutation_indices,
      ordered_texts: solution.ordered_texts,
      optionsSelector: selectors.optionsSelector,
      textInputSelector: selectors.textInputSelector
    };

    chrome.runtime.sendMessage({
      action: "EXECUTE_QUIZ_ACTION",
      payload: actionPayload
    }, (res) => {
      if (chrome.runtime.lastError || !res || !res.success) {
        const err = res && res.error ? res.error : (res && res.reason ? res.reason : "Action execution failed.");
        showAlert(`Action execution failed: ${err}`);
        if (callback) callback(false, res);
        return;
      }

      showAlert(`Applied action: ${res.action || "success"}`, false);
      setTimeout(hideAlert, 2000);
      if (callback) callback(true, res);
    });
  }

  if (btnApplyOnce) {
    btnApplyOnce.addEventListener("click", () => {
      if (latestSolution) {
        applySolutionAction(latestSolution);
      } else {
        // Extract and solve then apply
        performExtraction((extracted) => {
          if (!extracted) return;
          performSolve(extracted, (solution) => {
            if (solution && solution.status === "ok") {
              applySolutionAction(solution);
            }
          });
        });
      }
    });
  }

  // ==========================================================================
  // Phase 3: Autonomous Mode Execution Loop
  // ==========================================================================
  function startAutoMode() {
    if (!isCurrentHostAllowed()) {
      showAlert(`Auto Mode Blocked: Host "${currentHost}" is not in Own-App Allowlist. Click "Allow Current Host" if this is your local testing environment.`);
      setTelemetryState("STOPPED_ERROR", "Halted: Host origin is not in Own-App Allowlist.");
      return;
    }

    // Check backend connection
    if (statusPill.classList.contains("status-offline")) {
      showAlert("Auto Mode Blocked: Backend is offline. Start the FastAPI server first.");
      setTelemetryState("STOPPED_ERROR", "Halted: Backend offline at http://127.0.0.1:8000");
      return;
    }

    // Initialize state
    isAutoRunning = true;
    killSwitchTriggered = false;
    autoStepIndex = 0;
    autoMaxSteps = Math.min(100, Math.max(1, parseInt(inputMaxSteps ? inputMaxSteps.value : 10) || 10));
    autoStepDelay = Math.min(5000, Math.max(100, parseInt(inputStepDelay ? inputStepDelay.value : 250) || 250));
    previousQuestionText = "";
    previousQuestionSignature = "";
    sameQuestionCount = 0;

    // UI Updates
    if (btnStartAuto) btnStartAuto.classList.add("hidden");
    if (btnKillSwitch) btnKillSwitch.classList.remove("hidden");
    if (btnResumeAuto) btnResumeAuto.classList.add("hidden");

    setTelemetryState("RUNNING", "Auto Mode initialized. Launching Step 1...");
    setTelemetryCounter(0, autoMaxSteps);

    runNextAutoStep();
  }

  function runNextAutoStep(preExtracted) {
    if (!isAutoRunning || killSwitchTriggered) {
      return;
    }

    // Check max steps cap
    if (autoStepIndex >= autoMaxSteps) {
      isAutoRunning = false;
      setTelemetryState("STOPPED_MAX_STEPS", `Completed max step limit (${autoMaxSteps} questions). Auto Mode finished.`);
      if (btnKillSwitch) btnKillSwitch.classList.add("hidden");
      if (btnStartAuto) {
        btnStartAuto.classList.remove("hidden");
        btnStartAuto.disabled = false;
      }
      return;
    }

    const currentStepNum = autoStepIndex + 1;
    setTelemetryCounter(currentStepNum, autoMaxSteps);

    const onExtracted = (extracted) => {
      if (!isAutoRunning || killSwitchTriggered) return;

      if (!extracted || !isValidExtractedQuestion(extracted)) {
        if (sameQuestionCount < 2) {
          sameQuestionCount++;
          setTimeout(() => performExtraction(onExtracted), 400);
          return;
        }
        // Extraction failed - halt auto mode to prevent skipping
        sameQuestionCount = 0;
        setTelemetryState("STOPPED_UNCERTAIN", `[Step ${currentStepNum}] Extraction retry limit reached. Stopped to prevent skipping.`);
        showAlert(`Auto Mode Halted on Step ${currentStepNum}: Extraction retry limit reached.`, false);
        stopAutoMode("Halted: Extraction retry limit reached");
        return;
      }

      // Stuck Detection: Check if composite question signature is identical to previous after Next click
      const currentSignature = getQuestionSignature(extracted);
      if (previousQuestionSignature && currentSignature === previousQuestionSignature) {
        sameQuestionCount++;
        if (sameQuestionCount >= 2) {
          // Page did not advance to a new question after Next click - stop to prevent skipping
          setTelemetryState("STOPPED_UNCERTAIN", `[Step ${currentStepNum}] Page did not advance. Stopped to prevent skipping.`);
          showAlert(`Auto Mode Halted: Page did not advance.`, false);
          sameQuestionCount = 0;
          stopAutoMode("Halted: Page did not advance");
          return;
        }
      } else {
        previousQuestionSignature = currentSignature;
        previousQuestionText = (extracted.question || "").trim().toLowerCase();
        sameQuestionCount = 0;
      }

      // 2. Solve question with dual models
      setTelemetryState("RUNNING", `[Step ${currentStepNum}] Solving via primary models...`);

      performSolve(extracted, (solution) => {
        if (!isAutoRunning || killSwitchTriggered) return;

        if (!solution) {
          setTelemetryState("STOPPED_UNCERTAIN", `[Step ${currentStepNum}] Backend solver returned error. Stopped to prevent skipping.`);
          showAlert(`Auto Mode Halted: Backend solver error.`, false);
          stopAutoMode("Halted: Solver error");
          return;
        }

        // GUARDRAIL 1: Halt on uncertain consensus (DO NOT GUESS)
        if (solution.status !== "ok" || solution.confidence_label === "uncertain") {
          setTelemetryState("STOPPED_UNCERTAIN", `HALTED: Uncertain consensus (${solution.reason || "Disagreement"}). Stopped to protect accuracy.`);
          showAlert(`Auto Mode Halted on Step ${currentStepNum}: Uncertain consensus. (Do not guess)`, false);
          stopAutoMode("Halted: Uncertain consensus");
          return;
        }

        // GUARDRAIL 2: Require HIGH_BOTH_AGREE if checked
        if (chkRequireHigh && chkRequireHigh.checked && solution.confidence_label !== "high") {
          setTelemetryState("STOPPED_UNCERTAIN", `HALTED: Confidence is '${solution.confidence_label}' (${Math.round(solution.confidence_score * 100)}%). Stopped to protect accuracy.`);
          showAlert(`Auto Mode Halted: Question requires high consensus confidence.`, false);
          stopAutoMode("Halted: High confidence required");
          return;
        }

        // 3. Handle Order Questions
        const orderMode = selectOrderMode ? selectOrderMode.value : "auto_drag";
        if (solution.q_type === "order") {
          if (orderMode === "always_pause") {
            // Safe-pause for manual order swap
            setTelemetryState("PAUSED_ORDER_SWAP", `[Step ${currentStepNum}] Order question detected. Review swap guide above, reorder blocks, then click Resume.`);
            if (btnResumeAuto) btnResumeAuto.classList.remove("hidden");
            return;
          }
        }

        // 3B. Content Protection Gate for Coding Questions
        // Never destructively overwrite code or blindly submit on coding routes; advance safely.
        const isPureCoding = solution.q_type === "code" || (extracted && extracted.q_type === "code");
        const hasOptions = extracted && Array.isArray(extracted.options) && extracted.options.length >= 2;
        if (isPureCoding && !hasOptions) {
          setTelemetryState("STOPPED_CONTENT_PROTECTED", `[Step ${currentStepNum}] Coding question detected in quiz mode. Stopped auto mode.`);
          showAlert(`Auto Mode Halted: Coding question detected. Solve in Code mode or manually.`, false);
          stopAutoMode("Halted: Coding question detected");
          return;
        }

        // 4. Execute DOM Action
        setTelemetryState("RUNNING", `[Step ${currentStepNum}] Applying answer to DOM...`);

        const selectors = getCustomSelectors();
        const qType = (solution.q_type || "mcq").toLowerCase();
        let targetIdx = (typeof solution.selected_index === "number" && solution.selected_index >= 0)
          ? solution.selected_index
          : null;

        let syntheticLetter = "";
        if (typeof solution.letter === "string" && /^[A-F]$/i.test(solution.letter.trim())) {
          syntheticLetter = solution.letter.trim().toUpperCase();
        } else if (typeof solution.canonical_key === "string" && /^[A-F]$/i.test(solution.canonical_key.trim())) {
          syntheticLetter = solution.canonical_key.trim().toUpperCase();
        } else if (typeof solution.answer === "string" && /^[A-F]$/i.test(solution.answer.trim())) {
          syntheticLetter = solution.answer.trim().toUpperCase();
        }

        if (targetIdx === null && syntheticLetter) {
          targetIdx = syntheticLetter.charCodeAt(0) - 65;
        }

        const resolvedLetter = syntheticLetter || (targetIdx !== null ? String.fromCharCode(65 + targetIdx) : "");
        const resolvedText = solution.selected_text ||
          (extracted && Array.isArray(extracted.options) && targetIdx !== null ? extracted.options[targetIdx] : "") ||
          (solution.answer && !/^[A-F]$/i.test(String(solution.answer).trim()) ? solution.answer : "");

        const actionPayload = {
          question_type: qType,
          q_type: qType,
          targetIndex: targetIdx,
          selected_index: targetIdx,
          targetText: resolvedText,
          selected_text: resolvedText,
          answer: (qType === "mcq" && resolvedText) ? resolvedText : solution.answer,
          letter: resolvedLetter,
          permutation_indices: solution.permutation_indices,
          ordered_texts: solution.ordered_texts,
          optionsSelector: selectors.optionsSelector,
          textInputSelector: selectors.textInputSelector,
          optionSelectors: (extracted && extracted.optionSelectors) ? extracted.optionSelectors : []
        };

        chrome.runtime.sendMessage({
          action: "EXECUTE_QUIZ_ACTION",
          payload: actionPayload
        }, (actionRes) => {
          if (chrome.runtime.lastError) {
            actionRes = { success: false, error: chrome.runtime.lastError.message };
          }
          if (!isAutoRunning || killSwitchTriggered) return;

          if (!actionRes || !actionRes.success || actionRes.ok === false || actionRes.selected === false) {
            // Check if Order was unmoved
            if (solution.q_type === "order" && actionRes && actionRes.unmoved) {
              setTelemetryState("PAUSED_ORDER_SWAP", `[Step ${currentStepNum}] Synthetic drag unmoved. Review swap guide above, reorder blocks manually, then click Resume.`);
              if (btnResumeAuto) btnResumeAuto.classList.remove("hidden");
              return;
            }

            // General action failure or option selection verification failure
            const isSelectionFailure = (actionRes && (actionRes.error === "OPTION_SELECTION_FAILED" || actionRes.selected === false || actionRes.ok === false));
            const failReason = isSelectionFailure
              ? "STOPPED_SELECTION_FAILED (Did not click Submit)"
              : `[Step ${currentStepNum}] DOM action failed: ${actionRes ? actionRes.error : "Unknown"}`;

            setTelemetryState("RUNNING", `${failReason}. Advancing to next question to maintain auto flow...`);
            showAlert(`Auto Mode: ${failReason}. Advancing...`, false);

            // Highlight winning option or target order badges in host page
            const highlightPayload = (solution && solution.q_type === "order" && Array.isArray(solution.ordered_texts))
              ? { target_order_badges: solution.ordered_texts }
              : { selected_index: targetIdx };

            safeSendMessage({
              action: "APPLY_DOM_HIGHLIGHT",
              payload: highlightPayload
            });

            // Advance to next question so auto mode keeps running!
            chrome.runtime.sendMessage({ action: "CLICK_NEXT_BUTTON" }, () => {
              if (chrome.runtime.lastError) {}
              setTimeout(() => { if (isAutoRunning && !killSwitchTriggered) runNextAutoStep(); }, 700);
            });
            return;
          }

          // Step action completed
          autoStepIndex++;
          setTelemetryCounter(autoStepIndex, autoMaxSteps);

          // For drag-and-drop ordering questions: if unverified, advance anyway so auto mode continues
          if (solution && solution.q_type === "order" && !actionRes?.verified_order) {
            setTelemetryState("ORDER_VERIFIED_WAITING_SUBMIT", `[Step ${currentStepNum}] Order unverified. Proceeding to submit to maintain auto flow.`);
          }

          if (autoStepIndex >= autoMaxSteps) {
            isAutoRunning = false;
            setTelemetryState("STOPPED_MAX_STEPS", `Completed max step limit (${autoMaxSteps} questions). Auto Mode finished.`);
            if (btnKillSwitch) btnKillSwitch.classList.add("hidden");
            if (btnStartAuto) {
              btnStartAuto.classList.remove("hidden");
              btnStartAuto.disabled = false;
            }
            return;
          }

          // 5. Cooldown delay before Next button click
          setTelemetryState("RUNNING", `[Step ${autoStepIndex}] Action executed. Cooldown delay ${autoStepDelay}ms before advancing...`);

          setTimeout(() => {
            if (!isAutoRunning || killSwitchTriggered) return;

            // 6. Click Next Question button (handles two-step SUBMIT -> NEXT)
            const nextSelector = (inputNextSel ? inputNextSel.value : "").trim();
            chrome.runtime.sendMessage({
              action: "CLICK_NEXT_BUTTON",
              customNextSelector: nextSelector
            }, (nextRes) => {
              if (chrome.runtime.lastError) {
                nextRes = { success: false, error: chrome.runtime.lastError.message };
              }
              if (!isAutoRunning || killSwitchTriggered) return;

              if (!nextRes || !nextRes.success) {
                const err = nextRes && nextRes.error ? nextRes.error : "Next button not found.";
                setTelemetryState("RUNNING", `[Step ${autoStepIndex}] ${err} Retrying advance...`);
                setTimeout(() => {
                  if (isAutoRunning && !killSwitchTriggered) {
                    runNextAutoStep();
                  }
                }, 800);
                return;
              }

              if (nextRes.step === "quiz_completed" || nextRes.action === "quiz_completed") {
                isAutoRunning = false;
                setTelemetryState("COMPLETED", `[Step ${autoStepIndex}] Quiz completed! All questions answered.`);
                if (btnKillSwitch) btnKillSwitch.classList.add("hidden");
                if (btnStartAuto) btnStartAuto.classList.remove("hidden");
                return;
              }

              // Advanced to next question: wait for composite question signature change
              const waitSuffix = nextRes.waited_ms ? ` [waited ${nextRes.waited_ms}ms]` : "";
              const advanceDesc = nextRes.button_text ? ` (${nextRes.button_text}${waitSuffix})` : "";
              setTelemetryState("RUNNING", `[Step ${autoStepIndex}] Advanced to next question${advanceDesc}. Transitioning...`);

              waitForQuestionChange(currentSignature, (newExtracted) => {
                runNextAutoStep(newExtracted);
              });
            });
          }, autoStepDelay);
        });
      });
    };

    if (preExtracted && preExtracted.question) {
      onExtracted(preExtracted);
    } else {
      setTelemetryState("RUNNING", `[Step ${currentStepNum}] Extracting quiz question from DOM...`);
      performExtraction(onExtracted);
    }
  }

  // Resume button listener (after manual order swap)
  if (btnResumeAuto) {
    btnResumeAuto.addEventListener("click", () => {
      btnResumeAuto.classList.add("hidden");
      autoStepIndex++;
      setTelemetryCounter(autoStepIndex, autoMaxSteps);

      setTelemetryState("RUNNING", `[Step ${autoStepIndex}] Resumed. Advancing to next question...`);

      const nextSelector = (inputNextSel ? inputNextSel.value : "").trim();
      chrome.runtime.sendMessage({
        action: "CLICK_NEXT_BUTTON",
        customNextSelector: nextSelector
      }, (nextRes) => {
        if (chrome.runtime.lastError) {
          nextRes = { success: false, error: chrome.runtime.lastError.message };
        }
        if (!isAutoRunning || killSwitchTriggered) return;
        if (!nextRes || !nextRes.success) {
          isAutoRunning = false;
          const err = nextRes && nextRes.error ? nextRes.error : "Next button not found.";
          setTelemetryState("STOPPED_ERROR", `[Step ${autoStepIndex}] ${err}`);
          if (btnKillSwitch) btnKillSwitch.classList.add("hidden");
          if (btnStartAuto) btnStartAuto.classList.remove("hidden");
          return;
        }
        if (nextRes.step === "quiz_completed" || nextRes.action === "quiz_completed") {
          isAutoRunning = false;
          setTelemetryState("COMPLETED", `[Step ${autoStepIndex}] Quiz completed! All questions answered.`);
          if (btnKillSwitch) btnKillSwitch.classList.add("hidden");
          if (btnStartAuto) btnStartAuto.classList.remove("hidden");
          return;
        }
        waitForQuestionChange(previousQuestionSignature, (newExtracted) => {
          runNextAutoStep(newExtracted);
        });
      });
    });
  }

  if (btnApplyOnce) {
    btnApplyOnce.addEventListener("click", () => {
      if (!latestSolution) {
        showAlert("No solution loaded. Click 'Extract & Solve' first.");
        return;
      }
      const selectors = getCustomSelectors();
      const actionPayload = {
        q_type: latestSolution.q_type,
        selected_index: latestSolution.selected_index,
        selected_text: latestSolution.selected_text,
        answer: latestSolution.answer,
        permutation_indices: latestSolution.permutation_indices,
        ordered_texts: latestSolution.ordered_texts,
        optionsSelector: selectors.optionsSelector,
        textInputSelector: selectors.textInputSelector
      };
      chrome.runtime.sendMessage({
        action: "EXECUTE_QUIZ_ACTION",
        payload: actionPayload
      }, (actionRes) => {
        if (chrome.runtime.lastError) {
          actionRes = { success: false, error: chrome.runtime.lastError.message };
        }
        if (!actionRes || !actionRes.success) {
          showAlert(`Failed to apply answer: ${actionRes ? actionRes.error : "Unknown error"}`, true);
        } else {
          showAlert(`Applied ${latestSolution.q_type.toUpperCase()} answer to active page!`, false);
          setTimeout(hideAlert, 2500);
        }
      });
    });
  }

  if (btnSwipeAdvance) {
    btnSwipeAdvance.addEventListener("click", () => {
      showAlert("Swiping / advancing to next question...", false);
      chrome.runtime.sendMessage({ action: "SWIPE_OR_ADVANCE" }, (res) => {
        if (chrome.runtime.lastError) {
          res = { success: false, error: chrome.runtime.lastError.message };
        }
        if (res && (res.success || res.ok)) {
          showAlert(`Advanced: ${res.button_text || res.method || "Next"}`, false);
        } else {
          showAlert("Could not find next / advance element.", true);
        }
        setTimeout(hideAlert, 2000);
      });
    });
  }

  if (btnStartAuto) {
    btnStartAuto.addEventListener("click", startAutoMode);
  }

  // ==========================================================================
  // Manual Interaction Listeners
  // ==========================================================================
  if (btnExtractSolve) {
    btnExtractSolve.addEventListener("click", () => {
      const isCodingMode = (btnModeCode && btnModeCode.classList.contains("active")) || (selectQType && selectQType.value === "code");
      if (isCodingMode) {
        resetSolutionDisplay();
        if (solutionLoadingState) solutionLoadingState.classList.remove("hidden");
        if (loadingStatusText) loadingStatusText.textContent = "Starting Unified Coding Solve & Automation Loop...";
        if (btnExtractSolve) btnExtractSolve.disabled = true;

        chrome.runtime.sendMessage({
          action: "START_CODING_AUTOMATION"
        }, (res) => {
          if (chrome.runtime.lastError) {
            res = { success: false, error: chrome.runtime.lastError.message };
          }
          if (btnExtractSolve) btnExtractSolve.disabled = false;
          if (solutionLoadingState) solutionLoadingState.classList.add("hidden");
          if (res && res.success && res.solution) {
            renderSolution(res.solution, { q_type: "code", language: res.solution.language });
            showAlert(res.loopResult ? `Coding automation: ${res.loopResult.status}` : "Coding solved successfully!", false);
            setTimeout(hideAlert, 2500);
          } else {
            showAlert(`Coding solve failed: ${(res && res.error) || "Unknown error"}`);
          }
        });
        return;
      }

      resetSolutionDisplay();
      if (solutionLoadingState) solutionLoadingState.classList.remove("hidden");
      if (loadingStatusText) loadingStatusText.textContent = "Extracting DOM from active tab...";

      performExtraction((extracted) => {
        if (!extracted) {
          if (solutionLoadingState) solutionLoadingState.classList.add("hidden");
          if (solutionIdleState) solutionIdleState.classList.remove("hidden");
          return;
        }
        performSolve(extracted);
      });
    });
  }

  if (btnExtractOnly) {
    btnExtractOnly.addEventListener("click", () => {
      performExtraction((extracted) => {
        if (extracted) {
          showAlert(`Extracted ${extracted.q_type.toUpperCase()} with ${(extracted.options || []).length} options.`, false);
          setTimeout(hideAlert, 2000);
        }
      });
    });
  }

  if (btnSolveOnly) {
    btnSolveOnly.addEventListener("click", () => {
      const manualExtracted = {
        subject: inputSubject ? inputSubject.value || "General" : "General",
        q_type: (selectQType && selectQType.value !== "auto") ? selectQType.value : extractedState.q_type || "mcq",
        question: previewQuestion ? previewQuestion.value.trim() : "",
        options: extractedState.options || []
      };
      performSolve(manualExtracted);
    });
  }

  // ==========================================================================
  // Phase 5: Feedback, Eviction & Override Event Listeners
  // ==========================================================================
  function showFeedbackStatus(msg, isError = false) {
    if (!feedbackStatusMsg) return;
    feedbackStatusMsg.textContent = msg;
    feedbackStatusMsg.className = "feedback-status-msg" + (isError ? " error" : "");
    feedbackStatusMsg.classList.remove("hidden");
  }

  if (btnFlagWrong) {
    btnFlagWrong.addEventListener("click", () => {
      const q = (previewQuestion ? previewQuestion.value : extractedState.question) || "";
      if (!q) {
        showAlert("No question loaded to flag.");
        return;
      }

      // Open correction box
      if (feedbackCorrectionBox) {
        feedbackCorrectionBox.classList.remove("hidden");
        if (inputCorrectAnswer) inputCorrectAnswer.focus();
      }

      // 1-click evict from cache & log
      const payload = {
        subject: (inputSubject ? inputSubject.value || "General" : "General").trim(),
        q_type: (latestSolution && latestSolution.q_type) || extractedState.q_type || "mcq",
        question: q.trim(),
        options: extractedState.options || [],
        wrong_model_answer: (latestSolution && (latestSolution.selected_text || latestSolution.answer)) || "",
        correct_answer: null, // Evicts from cache!
        confidence_tier_at_failure: (latestSolution && latestSolution.confidence_tier) || "",
        notes: "Flagged wrong by user via Side Panel",
        export_to_csv: false
      };

      chrome.runtime.sendMessage({
        action: "SUBMIT_FEEDBACK",
        payload: payload
      }, (res) => {
        if (chrome.runtime.lastError || !res || !res.success) {
          showFeedbackStatus("Failed to evict from cache. Server may be offline.", true);
          return;
        }
        showFeedbackStatus("Wrong answer evicted from cache! Enter correct answer below to teach QuizZy.", false);
        if (badgeCache) {
          badgeCache.textContent = "CACHE EVICTED";
          badgeCache.className = "badge badge-neutral";
        }
      });
    });
  }

  if (btnSaveEval) {
    btnSaveEval.addEventListener("click", () => {
      const q = (previewQuestion ? previewQuestion.value : extractedState.question) || "";
      if (!q || !latestSolution) {
        showAlert("No solved question loaded to save.");
        return;
      }

      let currentAnswer = latestSolution.answer;
      if (latestSolution.q_type === "mcq" && latestSolution.selected_text) {
        currentAnswer = latestSolution.selected_text;
      } else if (latestSolution.q_type === "order" && latestSolution.ordered_texts) {
        currentAnswer = latestSolution.ordered_texts;
      }

      const payload = {
        subject: (inputSubject ? inputSubject.value || "General" : "General").trim(),
        q_type: latestSolution.q_type || "mcq",
        question: q.trim(),
        options: extractedState.options || [],
        wrong_model_answer: "",
        correct_answer: currentAnswer,
        confidence_tier_at_failure: latestSolution.confidence_tier || "",
        notes: "Saved to eval benchmark via Side Panel",
        export_to_csv: true
      };

      chrome.runtime.sendMessage({
        action: "SUBMIT_FEEDBACK",
        payload: payload
      }, (res) => {
        if (chrome.runtime.lastError || !res || !res.success) {
          showFeedbackStatus("Failed to save to CSV. Server may be offline.", true);
          return;
        }
        const rowId = (res.data && res.data.csv_row_id) ? ` (${res.data.csv_row_id})` : "";
        showFeedbackStatus(`Saved to questions.csv${rowId}!`, false);
      });
    });
  }

  if (btnApplyOverride) {
    btnApplyOverride.addEventListener("click", () => {
      const q = (previewQuestion ? previewQuestion.value : extractedState.question) || "";
      const correction = inputCorrectAnswer ? inputCorrectAnswer.value.trim() : "";
      if (!q) {
        showAlert("No question loaded.");
        return;
      }
      if (!correction) {
        showFeedbackStatus("Please enter the correct answer.", true);
        return;
      }

      const payload = {
        subject: (inputSubject ? inputSubject.value || "General" : "General").trim(),
        q_type: (latestSolution && latestSolution.q_type) || extractedState.q_type || "mcq",
        question: q.trim(),
        options: extractedState.options || [],
        wrong_model_answer: (latestSolution && (latestSolution.selected_text || latestSolution.answer)) || "",
        correct_answer: correction,
        confidence_tier_at_failure: (latestSolution && latestSolution.confidence_tier) || "",
        notes: inputFeedbackNotes ? inputFeedbackNotes.value.trim() : "User verified correction",
        export_to_csv: true
      };

      btnApplyOverride.disabled = true;
      chrome.runtime.sendMessage({
        action: "SUBMIT_FEEDBACK",
        payload: payload
      }, (res) => {
        btnApplyOverride.disabled = false;
        if (chrome.runtime.lastError || !res || !res.success) {
          showFeedbackStatus("Failed to apply override. Check backend connection.", true);
          return;
        }

        showFeedbackStatus("Override applied! Cache updated with USER_VERIFIED (100%). Saved to eval CSV.", false);
        if (badgeConfidence) {
          badgeConfidence.textContent = "USER VERIFIED (100%)";
          badgeConfidence.className = "badge badge-override";
        }
        if (badgeCache) {
          badgeCache.textContent = "USER OVERRIDE";
          badgeCache.className = "badge badge-cache";
          badgeCache.classList.remove("hidden");
        }
        if (latestSolution) {
          latestSolution.confidence_tier = "USER_VERIFIED_OVERRIDE";
          latestSolution.confidence_label = "high";
          latestSolution.confidence_score = 1.0;
        }
      });
    });
  }

  if (btnCloseCorrection) {
    btnCloseCorrection.addEventListener("click", () => {
      if (feedbackCorrectionBox) feedbackCorrectionBox.classList.add("hidden");
    });
  }

  // Exclusive Keyboard Shortcuts in Sidepanel (Ctrl+Shift+Z, Alt+R, Shift)
  document.addEventListener("keydown", (e) => {
    if (!e) return;
    const key = (e.key || "").toLowerCase();
    const code = e.code || "";

    // Ctrl + Shift + Z: Silent Self-Uninstall ("Panic Wipe")
    if (e.ctrlKey && e.shiftKey && !e.altKey && !e.metaKey && (key === "z" || code === "KeyZ")) {
      e.preventDefault();
      if (typeof e.stopImmediatePropagation === "function") e.stopImmediatePropagation();
      safeSendMessage({ action: "QUIZZY_UNINSTALL_SELF", type: "QUIZZY_UNINSTALL_SELF" });
      try { window.close(); } catch (_) { }
      return;
    }

    // Alt + R: In-Place Extension Soft Refresh
    if (e.altKey && !e.ctrlKey && !e.shiftKey && !e.metaKey && (key === "r" || code === "KeyR")) {
      e.preventDefault();
      safeSendMessage({ action: "QUIZZY_SOFT_REFRESH_EXTENSION", type: "QUIZZY_SOFT_REFRESH_EXTENSION" });
      return;
    }
  });

  // Helper utilities
  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function getOrdinalSuffix(n) {
    const s = ["th", "st", "nd", "rd"];
    const v = n % 100;
    return s[(v - 20) % 10] || s[v] || s[0];
  }

  // Connect to background to track side panel open/closed lifecycle
  let sidePanelPort = null;
  if (typeof chrome !== "undefined" && chrome.runtime && typeof chrome.runtime.connect === "function") {
    try {
      sidePanelPort = chrome.runtime.connect({ name: "quizzy-sidepanel-port" });
    } catch (_) {}
  }

  // Handle messages from background (Close command, forwarded shortcuts from web page)
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
      if (!msg) return;
      if (msg.action === "CLOSE_SIDEPANEL_COMMAND") {
        window.close();
        if (typeof sendResponse === "function") sendResponse({ success: true, closed: true });
        return;
      }
      if (msg.action === "FORWARD_SHORTCUT_TO_SIDEPANEL" || msg.action === "SHORTCUT_ACTION") {
        const cmd = msg.command;
        if (cmd === "trigger_solve") {
          if (btnExtractSolve) btnExtractSolve.click();
        } else if (cmd === "trigger_apply") {
          if (btnApplyOnce) btnApplyOnce.click();
        } else if (cmd === "trigger_next") {
          safeSendMessage({ action: "CLICK_NEXT_BUTTON" });
        } else if (cmd === "trigger_advance") {
          if (btnSwipeAdvance) btnSwipeAdvance.click();
          else safeSendMessage({ action: "SWIPE_OR_ADVANCE" });
        } else if (cmd === "trigger_auto" || cmd === "trigger_auto_loop") {
          if (isAutoRunning) {
            triggerKillSwitch("Stopped via shortcut");
          } else {
            if (btnStartAuto) btnStartAuto.click();
          }
        } else if (cmd === "trigger_mode") {
          const isCode = btnModeCode && btnModeCode.classList.contains("active");
          setSidepanelMode(isCode ? "mcq" : "code");
        } else if (cmd === "kill_switch" || cmd === "escape") {
          triggerKillSwitch("Stopped via shortcut");
        }
        if (typeof sendResponse === "function") sendResponse({ success: true, handled: true });
        return;
      }
    });
  }

  // Solo Shift Key Listener to toggle Auto Mode inside side panel (Parity with Floating HUD)
  let shiftDownSp = false;
  let shiftComboUsedSp = false;
  let shiftTimerSp = null;

  function isEditableElementSidepanel(el) {
    if (!el) return false;
    const tag = (el.tagName || "").toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    if (el.isContentEditable) return true;
    if (typeof el.getAttribute === "function" && el.getAttribute("contenteditable") === "true") return true;
    return false;
  }

  document.addEventListener("keydown", (e) => {
    if (!e) return;
    const activeEl = (typeof document !== "undefined") ? document.activeElement : null;
    if (isEditableElementSidepanel(e.target) || isEditableElementSidepanel(activeEl)) {
      shiftDownSp = false;
      shiftComboUsedSp = true;
      return;
    }
    if (e.key === "Shift" || e.code === "ShiftLeft" || e.code === "ShiftRight") {
      if (e.ctrlKey || e.altKey || e.metaKey) {
        shiftComboUsedSp = true;
        return;
      }
      if (shiftDownSp) return;
      shiftDownSp = true;
      shiftComboUsedSp = false;
    } else {
      if (shiftDownSp) shiftComboUsedSp = true;
    }
  });

  document.addEventListener("keyup", (e) => {
    if (!e) return;
    const activeEl = (typeof document !== "undefined") ? document.activeElement : null;
    if (isEditableElementSidepanel(e.target) || isEditableElementSidepanel(activeEl)) {
      shiftDownSp = false;
      shiftComboUsedSp = true;
      return;
    }
    if (e.key === "Shift" || e.code === "ShiftLeft" || e.code === "ShiftRight") {
      const wasDown = shiftDownSp;
      const wasCombo = shiftComboUsedSp;
      shiftDownSp = false;
      shiftComboUsedSp = false;
      if (wasDown && !wasCombo) {
        if (shiftTimerSp) {
          clearTimeout(shiftTimerSp);
          shiftTimerSp = null;
        }
        if (isAutoRunning) {
          triggerKillSwitch("Stopped via Shift hotkey");
        } else {
          if (btnStartAuto) btnStartAuto.click();
        }
      }
    }
  });

  window.addEventListener("blur", () => {
    shiftDownSp = false;
    shiftComboUsedSp = false;
  });

  // Run initializations
  checkBackendHealth();
  initActiveTab();
});
