// Roma Aeterna — WebSocket client and UI controller

/** Base URL for REST API (same host as page, or http://127.0.0.1:8000 when page is file://). Override: localStorage eternal_cities_api_base */
function eternalCitiesApiBase() {
    try {
        const custom = localStorage.getItem("eternal_cities_api_base");
        if (custom && String(custom).trim()) {
            return String(custom).trim().replace(/\/$/, "");
        }
    } catch (e) {
        /* ignore */
    }
    const proto = window.location.protocol;
    if (proto === "http:" || proto === "https:") {
        return window.location.origin;
    }
    return "http://127.0.0.1:8000";
}

function apiUrl(path) {
    const p = path.startsWith("/") ? path : `/${path}`;
    return eternalCitiesApiBase() + p;
}

function eternalCitiesWsUrl() {
    try {
        const base = eternalCitiesApiBase();
        const u = new URL(base.includes("://") ? base : `http://${base}`);
        const wsProto = u.protocol === "https:" ? "wss:" : "ws:";
        return `${wsProto}//${u.host}/ws`;
    } catch (e) {
        return "ws://127.0.0.1:8000/ws";
    }
}

let ws = null;
let renderer = null;
/** Grid dimensions from last world_state (mini-map, UI). */
let worldGridWidth = 80;
let worldGridHeight = 80;
/** One automatic resume per full page load when server sends suggest_auto_resume (reconnect after pause). */
let autoResumeOnceThisPageAttempted = false;
let reconnectDelay = 1000;
/** Single scheduled reconnect from ws.onclose — cleared on manual connect() or AI reconnect. */
let wsReconnectTimer = null;

function clearWebSocketReconnectTimer() {
    if (wsReconnectTimer != null) {
        clearTimeout(wsReconnectTimer);
        wsReconnectTimer = null;
    }
}

/** If true, open WebSocket only after "Continue" or after BEGIN / reset (saved session on reload). */
let pendingResumeOnOpen = false;
let pendingStartOnOpen = null;
let pendingResetOnOpen = false;
/** True while the city overlay shows "Continue current session" and the user has not chosen yet. */
let awaitingSessionChoice = false;

let totalStructures = 0;
let builtStructures = 0;
let runStartedAtMs = null;
let runLengthInterval = null;

/** Stable run start for UI clock across refresh if server omits started_at_s (localStorage; cleared on reset). */
const RUN_START_LOCAL_KEY = "eternal_cities_run_started_ms";

function applyRunStartFromScenario(msg) {
    if (typeof msg.started_at_s === "number" && Number.isFinite(msg.started_at_s)) {
        runStartedAtMs = Math.floor(msg.started_at_s * 1000);
        try {
            localStorage.setItem(RUN_START_LOCAL_KEY, String(runStartedAtMs));
        } catch (e) {
            /* ignore */
        }
        return;
    }
    let ms = null;
    try {
        const s = localStorage.getItem(RUN_START_LOCAL_KEY);
        if (s) ms = parseInt(s, 10);
    } catch (e) {
        /* ignore */
    }
    if (ms != null && !Number.isNaN(ms)) {
        runStartedAtMs = ms;
    } else {
        runStartedAtMs = Date.now();
        try {
            localStorage.setItem(RUN_START_LOCAL_KEY, String(runStartedAtMs));
        } catch (e) {
            /* ignore */
        }
    }
}

/** Last REST outcome for AI Settings panel (GET /api/llm-settings). */
let llmSettingsRestStatusLine = null;

function formatWebSocketReadyState() {
    if (!ws) return "WebSocket: (none)";
    switch (ws.readyState) {
        case WebSocket.CONNECTING:
            return "WebSocket: connecting…";
        case WebSocket.OPEN:
            return "WebSocket: connected";
        case WebSocket.CLOSING:
            return "WebSocket: closing…";
        default:
            return "WebSocket: disconnected";
    }
}

function updateAiSettingsConnectionStatus() {
    const el = document.getElementById("llm-settings-connection-status");
    const overlay = document.getElementById("llm-settings-overlay");
    if (!el) return;
    if (!overlay || !overlay.classList.contains("visible")) {
        el.textContent = "";
        el.className = "llm-settings-connection-status";
        return;
    }
    const rest =
        llmSettingsRestStatusLine ||
        "REST: not verified yet — open loaded or click Reconnect";
    const line = `${rest} · ${formatWebSocketReadyState()}`;
    el.textContent = line;
    el.className = "llm-settings-connection-status";
    if (!ws || ws.readyState === WebSocket.CLOSED) {
        el.classList.add("llm-conn-bad");
    } else if (ws.readyState !== WebSocket.OPEN) {
        el.classList.add("llm-conn-warn");
    } else if (llmSettingsRestStatusLine && llmSettingsRestStatusLine.startsWith("REST: error")) {
        el.classList.add("llm-conn-bad");
    }
}

async function fetchJsonWithTimeout(url, timeoutMs) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const r = await fetch(url, { signal: controller.signal });
        const text = await r.text();
        if (!r.ok) {
            throw new Error(`HTTP ${r.status}: ${text.slice(0, 160)}`);
        }
        try {
            return JSON.parse(text);
        } catch (e) {
            throw new Error("Server did not return JSON (is this the Eternal Cities server?)");
        }
    } finally {
        clearTimeout(timeoutId);
    }
}

async function reconnectAiSettings() {
    try {
        const rows = document.getElementById("llm-settings-rows");
        if (rows) {
            rows.innerHTML =
                '<p class="llm-settings-loading">Reconnecting… testing API…</p>';
        }
    } catch (e) {
        /* ignore */
    }

    reconnectDelay = 1000;
    clearWebSocketReconnectTimer();
    const continueSection = document.getElementById("select-continue-section");
    const skipWsForSessionChoice = continueSection && continueSection.hidden === false;
    try {
        if (!skipWsForSessionChoice) {
            if (!ws || ws.readyState === WebSocket.CLOSED) {
                connect();
            } else {
                try {
                    ws.onclose = null;
                } catch (e) {
                    /* ignore */
                }
                try {
                    ws.close();
                } catch (e) {
                    /* ignore */
                }
                setConnectionStatus(false);
                setTimeout(() => connect(), 50);
            }
        }
    } catch (e) {
        console.error("Reconnect failed:", e);
    }

    // Test API response explicitly, then render settings on success.
    try {
        const msg = await fetchJsonWithTimeout(apiUrl("/api/llm-settings"), 4000);
        llmSettingsRestStatusLine = "REST: OK (GET /api/llm-settings)";
        renderLlmSettings(msg);
        updateAiSettingsConnectionStatus();
        [100, 400, 1000].forEach((ms) => {
            setTimeout(updateAiSettingsConnectionStatus, ms);
        });
    } catch (e) {
        const message = e && e.message ? e.message : "Unknown error";
        llmSettingsRestStatusLine = `REST: error — ${message}`;
        updateAiSettingsConnectionStatus();
        const rows = document.getElementById("llm-settings-rows");
        if (rows) {
            rows.innerHTML = `<p class="llm-settings-error">Reconnect failed: ${escapeHtml(message)}</p>`;
        }
        // Do not open the global build-paused modal — that implies the sim stopped; only AI Settings failed.
        // Still attempt the existing fallback flow (WS request) if possible.
        try {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "get_llm_settings" }));
            }
        } catch (err) {
            /* ignore */
        }
    }
}

function connect() {
    clearWebSocketReconnectTimer();
    if (ws && ws.readyState !== WebSocket.CLOSED) {
        try {
            ws.onclose = null;
        } catch (e) {
            /* ignore */
        }
        try {
            ws.close();
        } catch (e) {
            /* ignore */
        }
    }
    ws = new WebSocket(eternalCitiesWsUrl());

    ws.onopen = () => {
        console.log("Connected to Roma Aeterna");
        setConnectionStatus(true);
        reconnectDelay = 1000;
        updateAiSettingsConnectionStatus();
        if (pendingResetOnOpen) {
            pendingResetOnOpen = false;
            try {
                ws.send(JSON.stringify({ type: "reset" }));
            } catch (e) {
                console.error("reset send failed:", e);
            }
        } else if (pendingStartOnOpen) {
            const p = pendingStartOnOpen;
            pendingStartOnOpen = null;
            try {
                ws.send(JSON.stringify({
                    type: "start",
                    city: p.city,
                    year: p.year,
                }));
            } catch (e) {
                console.error("start send failed:", e);
            }
            document.getElementById("select-overlay").classList.add("hidden");
        } else if (pendingResumeOnOpen) {
            pendingResumeOnOpen = false;
            try {
                ws.send(JSON.stringify({ type: "resume" }));
            } catch (e) {
                console.error("resume send failed:", e);
            }
            document.getElementById("select-overlay").classList.add("hidden");
        }
    };

    ws.onclose = () => {
        console.log("Disconnected, reconnecting...");
        setConnectionStatus(false);
        updateAiSettingsConnectionStatus();
        clearWebSocketReconnectTimer();
        wsReconnectTimer = setTimeout(() => {
            wsReconnectTimer = null;
            connect();
        }, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 10000);
    };

    ws.onerror = (err) => {
        console.error("WebSocket error:", err);
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
    };
}

function handleMessage(msg) {
    switch (msg.type) {
        case "world_state":
            if (typeof msg.width === "number" && msg.width > 0) worldGridWidth = msg.width;
            if (typeof msg.height === "number" && msg.height > 0) worldGridHeight = msg.height;
            renderer.init(msg);
            updateTimeline(msg.period, msg.year);
            break;

        case "scenario":
            document.title = `${msg.city} — Eternal Cities`;
            const sub = document.getElementById("subtitle");
            if (sub) sub.textContent = `${msg.city}, ${msg.period} — AI agents reconstruct this city in real time`;
            applyRunStartFromScenario(msg);
            startRunLengthTicker();
            updateTimeline(msg.period, msg.year);
            if (!awaitingSessionChoice) {
                document.getElementById("select-overlay").classList.add("hidden");
            }
            break;

        case "token_usage":
            applyTokenUsageToHeader(msg.by_ui_agent);
            break;

        case "tile_update":
            renderer.updateTiles(msg.tiles);
            if (msg.period) updateTimeline(msg.period, msg.year);
            builtStructures++;
            updateProgressBar();
            // Auto-fly camera to new building
            if (msg.tiles && msg.tiles.length > 0 && renderer.flyTo) {
                const t = msg.tiles[0];
                const S = 14; // TILE_SIZE (must match renderer3d.js)
                renderer.flyTo((t.x + 0.5) * S, (t.y + 0.5) * S);
            }
            hideLoading();
            break;

        case "chat":
            appendChat(msg);
            break;

        case "typing":
            showTyping(msg.sender, msg.partial);
            break;

        case "phase":
            appendPhaseAnnouncement(msg);
            updateDistrict(msg.district);
            hideLoading();
            break;

        case "timeline":
            updateTimeline(msg.period, msg.year);
            break;

        case "tile_detail":
            showTileDetail(msg.tile);
            break;

        case "agent_status":
            setAgentStatus(msg.agent, msg.status);
            if (msg.status !== "thinking") hideLoading();
            break;

        case "loading":
            showLoading(msg.agent, msg.message);
            setAgentStatus(msg.agent, "thinking");
            break;

        case "master_plan":
            updateMasterPlan(msg.plan);
            totalStructures += msg.plan.length;
            updateProgressBar();
            break;

        case "placement_warnings":
            if (msg.warnings && msg.warnings.length) {
                const district = msg.district || "district";
                const preview = msg.warnings.slice(0, 6).join(" · ");
                appendSystemMessage(`Placement check (${district}, ${msg.count || msg.warnings.length}): ${preview}`);
            }
            break;

        case "map_description":
            setMapDescription(msg.description);
            break;

        case "map_image":
            setMapImage(msg.url, msg.source);
            break;

        case "complete":
            appendSystemMessage("Roma Aeterna is complete. Glory to the Empire!");
            resetAllAgentStatus();
            break;

        case "paused":
            showPausedOverlay(msg);
            resetAllAgentStatus();
            if (msg.suggest_auto_resume === true && !autoResumeOnceThisPageAttempted) {
                autoResumeOnceThisPageAttempted = true;
                setTimeout(() => {
                    if (!ws || ws.readyState !== WebSocket.OPEN) return;
                    ws.send(JSON.stringify({ type: "resume" }));
                    dismissPausedOverlay();
                }, 400);
            }
            break;

        case "llm_settings":
            renderLlmSettings(msg);
            break;

        case "llm_settings_saved":
            appendSystemMessage("AI backend settings saved for next run.");
            closeLlmSettingsOverlay();
            break;
    }
}

function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
}

async function requestLlmSettingsFromServer() {
    const rows = document.getElementById("llm-settings-rows");
    const fileHint =
        window.location.protocol === "file:"
            ? " Open the app at <strong>http://127.0.0.1:8000</strong> after starting the server (<code>python main.py</code>), or set <code>localStorage.eternal_cities_api_base</code> to your server URL."
            : "";
    try {
        const r = await fetch(apiUrl("/api/llm-settings"));
        const text = await r.text();
        if (!r.ok) {
            const staleHint =
                r.status === 404
                    ? " Restart the server (python main.py) so it loads the latest API routes."
                    : "";
            throw new Error(`HTTP ${r.status}: ${text.slice(0, 120)}${staleHint}`);
        }
        let msg;
        try {
            msg = JSON.parse(text);
        } catch (parseErr) {
            throw new Error("Server did not return JSON (is this the Eternal Cities server?)");
        }
        llmSettingsRestStatusLine = "REST: OK (GET /api/llm-settings)";
        renderLlmSettings(msg);
        updateAiSettingsConnectionStatus();
    } catch (e) {
        console.error("LLM settings load failed:", e);
        const detailMsg = e && e.message ? e.message : "Unknown error";
        llmSettingsRestStatusLine = `REST: error — ${detailMsg}`;
        updateAiSettingsConnectionStatus();
        if (rows) {
            const detail = escapeHtml(detailMsg);
            rows.innerHTML = `<p class="llm-settings-error">Could not load AI settings: ${detail}${fileHint ? `. ${fileHint}` : ""}</p>`;
        }
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "get_llm_settings" }));
        }
    }
}

function toggleLlmSettingsOverlay() {
    const el = document.getElementById("llm-settings-overlay");
    if (!el) return;
    const open = el.classList.toggle("visible");
    el.setAttribute("aria-hidden", open ? "false" : "true");
    if (open) {
        const rows = document.getElementById("llm-settings-rows");
        if (rows) {
            rows.innerHTML =
                '<p class="llm-settings-loading">Loading AI settings from the server…</p>';
        }
        updateAiSettingsConnectionStatus();
        void requestLlmSettingsFromServer();
    }
}

function closeLlmSettingsOverlay() {
    const el = document.getElementById("llm-settings-overlay");
    if (!el) return;
    el.classList.remove("visible");
    el.setAttribute("aria-hidden", "true");
}

function llmProviderDisplayName(providerRaw) {
    const p = (providerRaw || "claude_cli").toLowerCase();
    return p === "openai_compatible" || p === "openai" || p === "chatgpt"
        ? "OpenAI-compatible API"
        : "Claude CLI";
}

function formatTokenUsageLine(usageEntry) {
    if (!usageEntry) return "Tokens: —";
    const last = usageEntry.last || null;
    const tot = usageEntry.total || null;
    if (!last && !tot) return "Tokens: —";
    const lastTotal = last && typeof last.total_tokens === "number" ? last.total_tokens : null;
    const lastPrompt = last && typeof last.prompt_tokens === "number" ? last.prompt_tokens : null;
    const lastCompletion = last && typeof last.completion_tokens === "number" ? last.completion_tokens : null;
    const exact = !!(last && last.exact);
    const totalTotal = tot && typeof tot.total_tokens === "number" ? tot.total_tokens : null;
    const parts = [];
    if (lastTotal != null) {
        const suffix = exact ? "" : " (est)";
        if (lastPrompt != null && lastCompletion != null) {
            parts.push(`Last: ${lastTotal}${suffix} (p${lastPrompt} + c${lastCompletion})`);
        } else {
            parts.push(`Last: ${lastTotal}${suffix}`);
        }
    }
    if (totalTotal != null) parts.push(`Session: ${totalTotal}`);
    return `Tokens: ${parts.join(" · ") || "—"}`;
}

function fillClaudeModelSelect(fieldset, currentModel, choices) {
    const sel = fieldset.querySelector(".llm-claude-model-select");
    const custom = fieldset.querySelector(".llm-claude-model-custom");
    if (!sel || !custom) return;
    const list = Array.isArray(choices) && choices.length ? choices : ["haiku", "sonnet", "opus"];
    sel.innerHTML = "";
    list.forEach((m) => {
        const o = document.createElement("option");
        o.value = m;
        o.textContent = m;
        sel.appendChild(o);
    });
    if (currentModel && !list.includes(currentModel)) {
        const o = document.createElement("option");
        o.value = currentModel;
        o.textContent = `${currentModel} (saved)`;
        sel.appendChild(o);
    }
    const oOther = document.createElement("option");
    oOther.value = "__custom__";
    oOther.textContent = "Other…";
    sel.appendChild(oOther);

    const applySelection = () => {
        if (list.includes(currentModel)) {
            sel.value = currentModel;
        } else if (currentModel) {
            sel.value = currentModel;
        } else {
            sel.value = list[0] || "haiku";
        }
    };
    applySelection();

    const syncCustom = () => {
        const useCustom = sel.value === "__custom__";
        custom.style.display = useCustom ? "block" : "none";
        if (useCustom) custom.value = "";
    };
    sel.addEventListener("change", syncCustom);
    syncCustom();
}

function renderLlmSettings(msg) {
    const container = document.getElementById("llm-settings-rows");
    if (!container) return;
    if (!msg.agents || Object.keys(msg.agents).length === 0) {
        container.innerHTML =
            '<p class="llm-settings-error">Could not load AI settings. Check the server connection and try again.</p>';
        return;
    }
    const labels = msg.labels || {};
    const tokenUsage = msg.token_usage || {};
    const claudeCliModels = msg.claude_cli_models || [];
    container.innerHTML = "";
    for (const [key, spec] of Object.entries(msg.agents)) {
        const label = labels[key] || key;
        const prov = spec.provider || "claude_cli";
        const provSelect = prov === "openai_compatible" ? "openai_compatible" : "claude_cli";
        const model = spec.model || "";
        const baseUrl = spec.openai_base_url || "";
        const hasKey = !!spec.has_openai_api_key;
        const currentBaseDisplay = baseUrl.trim() ? escapeHtml(baseUrl) : "—";
        const currentKeyDisplay = hasKey ? "Saved on server (hidden)" : "Not set";
        const showCurrentOpenAi = provSelect === "openai_compatible";
        const fieldset = document.createElement("fieldset");
        fieldset.className = "llm-agent-row";
        fieldset.dataset.agentKey = key;
        const tokensLine = formatTokenUsageLine(tokenUsage[key]);
        fieldset.innerHTML = `
            <legend class="llm-legend">${escapeHtml(label)}</legend>
            <div class="llm-two-col">
                <div class="llm-current-block">
                    <div class="llm-col-title">Current</div>
                    <dl class="llm-current-dl">
                        <dt>Provider</dt><dd class="llm-current-provider">${escapeHtml(llmProviderDisplayName(prov))}</dd>
                        <dt>Model</dt><dd class="llm-current-model">${escapeHtml(model || "—")}</dd>
                        <dt>Usage</dt><dd class="llm-current-usage">${escapeHtml(tokensLine)}</dd>
                        <dt class="llm-current-openai-only">API base URL</dt>
                        <dd class="llm-current-openai-only">${currentBaseDisplay}</dd>
                        <dt class="llm-current-openai-only">API key</dt>
                        <dd class="llm-current-openai-only">${escapeHtml(currentKeyDisplay)}</dd>
                    </dl>
                </div>
                <div class="llm-new-block">
                    <div class="llm-col-title">New</div>
                    <div class="llm-field-grid">
                        <label class="llm-label">
                            <span class="llm-label-text">Provider</span>
                            <select class="llm-provider">
                                <option value="claude_cli">Claude CLI</option>
                                <option value="openai_compatible">OpenAI-compatible API</option>
                            </select>
                        </label>
                        <label class="llm-label llm-claude-model-wrap">
                            <span class="llm-label-text">Model</span>
                            <div class="llm-claude-model-controls">
                                <select class="llm-claude-model-select" aria-label="Claude model"></select>
                                <input type="text" class="llm-claude-model-custom" placeholder="Custom model id" autocomplete="off" />
                            </div>
                        </label>
                        <label class="llm-label llm-openai-model-wrap">
                            <span class="llm-label-text">Model id</span>
                            <input type="text" class="llm-model-openai" placeholder="e.g. gpt-4o-mini" />
                        </label>
                        <div class="llm-openai-fields">
                            <label class="llm-label">
                                <span class="llm-label-text">Base URL (optional)</span>
                                <input type="text" class="llm-openai-base" placeholder="https://api.openai.com/v1" />
                            </label>
                            <label class="llm-label">
                                <span class="llm-label-text">API key (blank = keep current)</span>
                                <input type="password" class="llm-openai-key" autocomplete="off" placeholder="${hasKey ? "Leave blank to keep saved key" : ""}" />
                            </label>
                        </div>
                    </div>
                </div>
            </div>`;
        container.appendChild(fieldset);
        const newBlock = fieldset.querySelector(".llm-new-block");
        const sel = newBlock.querySelector(".llm-provider");
        sel.value = provSelect;
        newBlock.querySelector(".llm-openai-base").value = baseUrl;
        newBlock.querySelector(".llm-model-openai").value = model;
        fillClaudeModelSelect(fieldset, model, claudeCliModels);
        sel.addEventListener("change", () => updateLlmRowOpenAiVisibility(fieldset));
        updateLlmRowOpenAiVisibility(fieldset);
        fieldset.querySelectorAll(".llm-current-openai-only").forEach((el) => {
            el.style.display = showCurrentOpenAi ? "" : "none";
        });
    }
}

function updateLlmRowOpenAiVisibility(fieldset) {
    const newBlock = fieldset.querySelector(".llm-new-block");
    if (!newBlock) return;
    const prov = newBlock.querySelector(".llm-provider").value;
    const wrap = newBlock.querySelector(".llm-openai-fields");
    if (wrap) wrap.style.display = prov === "openai_compatible" ? "block" : "none";
    const claudeModelWrap = newBlock.querySelector(".llm-claude-model-wrap");
    const openaiModelWrap = newBlock.querySelector(".llm-openai-model-wrap");
    if (claudeModelWrap) claudeModelWrap.style.display = prov === "claude_cli" ? "" : "none";
    if (openaiModelWrap) openaiModelWrap.style.display = prov === "openai_compatible" ? "" : "none";
}

async function saveLlmSettingsFromForm() {
    const overrides = {};
    const rows = document.querySelectorAll(".llm-agent-row");
    for (const row of rows) {
        const key = row.dataset.agentKey;
        if (!key) continue;
        const newBlock = row.querySelector(".llm-new-block");
        if (!newBlock) continue;
        const provider = newBlock.querySelector(".llm-provider").value;
        let model = "";
        if (provider === "claude_cli") {
            const csel = newBlock.querySelector(".llm-claude-model-select");
            const ccustom = newBlock.querySelector(".llm-claude-model-custom");
            if (csel && csel.value === "__custom__") {
                model = ccustom ? ccustom.value.trim() : "";
                if (!model) {
                    window.alert("Enter a custom model id, or pick a model from the list.");
                    return;
                }
            } else if (csel) {
                model = csel.value.trim();
            }
        } else {
            const minp = newBlock.querySelector(".llm-model-openai");
            model = minp ? minp.value.trim() : "";
        }
        const patch = { provider, model };
        if (provider === "openai_compatible") {
            const base = newBlock.querySelector(".llm-openai-base").value.trim();
            const apiKey = newBlock.querySelector(".llm-openai-key").value;
            if (base) patch.openai_base_url = base;
            if (apiKey) patch.openai_api_key = apiKey;
        }
        overrides[key] = patch;
    }
    try {
        const r = await fetch(apiUrl("/api/llm-settings"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ overrides }),
        });
        if (r.ok) {
            appendSystemMessage("AI backend settings saved for next run.");
            closeLlmSettingsOverlay();
            return;
        }
    } catch (e) {
        console.error("LLM settings save failed:", e);
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "save_llm_settings", overrides }));
    }
}

const PAUSED_TITLES = {
    rate_limit: "Rate limit",
    api_error: "API error",
    bad_model_output: "Invalid model output",
    network: "Connection problem",
    cli_missing: "CLI not found",
    unknown: "Build paused",
};

function showPausedOverlay(msg) {
    const overlay = document.getElementById("paused-overlay");
    const titleEl = document.getElementById("paused-title");
    const bodyEl = document.getElementById("paused-body");
    const detailEl = document.getElementById("paused-detail");
    if (!overlay || !titleEl || !bodyEl || !detailEl) return;

    const reasonKey = msg.reason && PAUSED_TITLES[msg.reason] ? msg.reason : "unknown";
    titleEl.textContent = PAUSED_TITLES[reasonKey] || PAUSED_TITLES.unknown;
    bodyEl.textContent = msg.summary || "The build stopped. You can try again when the issue is resolved.";

    const detailText = (msg.detail || "").trim();
    if (detailText) {
        detailEl.textContent = detailText;
        detailEl.hidden = false;
    } else {
        detailEl.textContent = "";
        detailEl.hidden = true;
    }

    overlay.classList.add("visible");
    overlay.setAttribute("aria-hidden", "false");
}

function dismissPausedOverlay() {
    const overlay = document.getElementById("paused-overlay");
    if (!overlay) return;
    overlay.classList.remove("visible");
    overlay.setAttribute("aria-hidden", "true");
}

function resumeBuildAfterPause() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resume" }));
    }
    dismissPausedOverlay();
}

// --- Agent Status & Timers ---

const agentTimers = {};

function formatThinkingDuration(elapsedMs) {
    const s = elapsedMs / 1000;
    if (s < 60) return `${s.toFixed(1)}s`;
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}m ${sec}s`;
}

function setAgentStatus(agent, status) {
    const el = document.getElementById(`status-${agent}`);
    if (!el) return;

    el.classList.remove("thinking", "speaking", "idle");
    el.classList.add(status);

    const stateEl = el.querySelector(".agent-state");
    if (stateEl) {
        stateEl.textContent = status === "thinking" ? "thinking..." :
                              status === "speaking" ? "speaking" : "idle";
    }

    const timerEl = el.querySelector(".agent-timer");
    if (status === "thinking") {
        if (agentTimers[agent]) {
            clearInterval(agentTimers[agent].interval);
        }
        const start = Date.now();
        agentTimers[agent] = { start, interval: null };
        const tick = () => {
            if (!timerEl || !agentTimers[agent]) return;
            timerEl.textContent = formatThinkingDuration(Date.now() - agentTimers[agent].start);
        };
        tick();
        agentTimers[agent].interval = setInterval(tick, 100);
    } else {
        if (agentTimers[agent]) {
            clearInterval(agentTimers[agent].interval);
            delete agentTimers[agent];
        }
        if (timerEl) timerEl.textContent = "";
    }
}

function resetAllAgentStatus() {
    for (const agent of ["imperator", "cartographus", "urbanista", "faber", "civis"]) {
        setAgentStatus(agent, "idle");
    }
}

// --- Chat ---

function appendChat(msg) {
    const container = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = `chat-msg ${msg.sender}`;

    if (msg.msg_type === "fact_check" && msg.approved === false) {
        div.classList.add("rejected");
    } else if (msg.msg_type === "fact_check") {
        div.classList.add("fact-check");
    }

    const name = AGENT_NAMES[msg.sender] || msg.sender;
    div.innerHTML = `
        <div class="sender">${name}</div>
        <div class="content">${escapeHtml(msg.content)}</div>
        <div class="meta">${msg.msg_type || ""}</div>
    `;

    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    hideTyping();

    // Log it
    addLog(msg.sender, msg.msg_type || "chat", msg.content);
}

function appendPhaseAnnouncement(msg) {
    const container = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = "chat-msg phase-announce";
    div.innerHTML = `
        <div class="content">--- Building ${escapeHtml(msg.district)} ---<br>${escapeHtml(msg.description || "")}</div>
    `;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function appendSystemMessage(text) {
    const container = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = "chat-msg phase-announce";
    div.innerHTML = `<div class="content">${escapeHtml(text)}</div>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// --- Typing indicator ---

let typingTimeout = null;

function showTyping(sender, partial) {
    const el = document.getElementById("typing-indicator");
    const name = AGENT_NAMES[sender] || sender;
    el.innerHTML = `<strong>${name}</strong> is thinking<span class="typing-dots"><span>.</span><span>.</span><span>.</span></span>`;
    el.classList.add("active");

    clearTimeout(typingTimeout);
    typingTimeout = setTimeout(hideTyping, 5000);
}

function hideTyping() {
    const el = document.getElementById("typing-indicator");
    el.classList.remove("active");
    clearTimeout(typingTimeout);
}

// --- Timeline ---

function updateTimeline(period, year) {
    const periodEl = document.getElementById("timeline-period");
    const yearEl = document.getElementById("timeline-year");
    const periodWrap = document.getElementById("timeline-period-wrap");
    const yearWrap = document.getElementById("timeline-year-wrap");
    const p = period == null ? "" : String(period).trim();
    const hasPeriod = Boolean(p);
    if (periodEl) periodEl.textContent = hasPeriod ? p : "—";
    if (periodWrap) periodWrap.style.display = hasPeriod ? "" : "none";

    const hasYear = year !== null && year !== undefined && year !== "";
    let yearOk = false;
    if (hasYear) {
        const n = Number(year);
        yearOk = !Number.isNaN(n);
        if (yearEl) yearEl.textContent = yearOk ? formatYear(n) : "—";
    } else if (yearEl) {
        yearEl.textContent = "—";
    }
    if (yearWrap) yearWrap.style.display = hasYear && yearOk ? "" : "none";
}

function formatDurationHms(ms) {
    const totalSeconds = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = totalSeconds % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    return `${m}:${String(s).padStart(2, "0")}`;
}

function updateRunLength() {
    const el = document.getElementById("timeline-run-length");
    if (!el) return;
    if (!runStartedAtMs) {
        el.textContent = "—";
        return;
    }
    el.textContent = formatDurationHms(Date.now() - runStartedAtMs);
}

function startRunLengthTicker() {
    if (runLengthInterval) {
        clearInterval(runLengthInterval);
        runLengthInterval = null;
    }
    const runWrap = document.getElementById("timeline-run-wrap");
    if (runWrap) runWrap.style.display = "";
    updateRunLength();
    runLengthInterval = setInterval(updateRunLength, 1000);
}

function formatTokShort(n) {
    const x = Number(n);
    if (!Number.isFinite(x) || x < 1) return "";
    if (x >= 1e6) return `${(x / 1e6).toFixed(1)}M`;
    if (x >= 1e3) return `${(x / 1e3).toFixed(1)}k`;
    return String(Math.round(x));
}

function applyTokenUsageToHeader(byUi) {
    const data = byUi || {};
    const ids = ["cartographus", "urbanista"];
    for (const id of ids) {
        const el = document.getElementById(`agent-tokens-${id}`);
        if (!el) continue;
        const row = data[id];
        const tot = row && row.total_tokens != null ? row.total_tokens : 0;
        const s = formatTokShort(tot);
        el.textContent = s ? `${s} tok` : "";
    }
}

const CHAT_WIDTH_STORAGE_KEY = "eternal_cities_chat_sidebar_width";
const CHAT_COLLAPSED_STORAGE_KEY = "eternal_cities_chat_collapsed";

function initChatPanelLayout() {
    const main = document.getElementById("main-layout");
    const split = document.getElementById("chat-split");
    const handle = document.getElementById("chat-resize-handle");
    const collapseBtn = document.getElementById("chat-collapse-btn");
    const expandFab = document.getElementById("chat-expand-fab");
    if (!main || !split) return;

    const readStoredWidthPx = () => {
        try {
            const w = parseInt(localStorage.getItem(CHAT_WIDTH_STORAGE_KEY), 10);
            if (!Number.isNaN(w)) return Math.min(720, Math.max(280, w));
        } catch (e) {
            /* ignore */
        }
        return 420;
    };

    let lastSidebarWidthPx = readStoredWidthPx();

    const applySidebarWidthToDom = (wPx) => {
        const clamped = Math.min(720, Math.max(280, wPx));
        lastSidebarWidthPx = clamped;
        document.documentElement.style.setProperty("--chat-sidebar-width", `${clamped}px`);
        try {
            localStorage.setItem(CHAT_WIDTH_STORAGE_KEY, String(clamped));
        } catch (e) {
            /* ignore */
        }
    };

    const setChatCollapsed = (collapsed) => {
        main.classList.toggle("chat-collapsed", collapsed);
        if (expandFab) expandFab.hidden = !collapsed;
        if (collapseBtn) {
            collapseBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
            collapseBtn.title = collapsed ? "Expand chat" : "Collapse chat";
            collapseBtn.textContent = "‹";
        }
        if (collapsed) {
            document.documentElement.style.setProperty("--chat-sidebar-width", "0px");
        } else {
            applySidebarWidthToDom(lastSidebarWidthPx);
        }
        try {
            localStorage.setItem(CHAT_COLLAPSED_STORAGE_KEY, collapsed ? "1" : "0");
        } catch (e) {
            /* ignore */
        }
    };

    try {
        if (localStorage.getItem(CHAT_COLLAPSED_STORAGE_KEY) === "1") {
            setChatCollapsed(true);
        } else {
            applySidebarWidthToDom(lastSidebarWidthPx);
        }
    } catch (e) {
        applySidebarWidthToDom(lastSidebarWidthPx);
    }

    let dragging = false;
    let startPointerX = 0;
    let startWidthPx = 0;

    handle?.addEventListener("mousedown", (e) => {
        if (main.classList.contains("chat-collapsed")) return;
        e.preventDefault();
        dragging = true;
        startPointerX = e.clientX;
        startWidthPx = split.getBoundingClientRect().width;
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
    });

    document.addEventListener("mousemove", (e) => {
        if (!dragging) return;
        const delta = startPointerX - e.clientX;
        applySidebarWidthToDom(startWidthPx + delta);
    });

    document.addEventListener("mouseup", () => {
        if (!dragging) return;
        dragging = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
    });

    collapseBtn?.addEventListener("click", () => setChatCollapsed(true));
    expandFab?.addEventListener("click", () => setChatCollapsed(false));
}

function updateDistrict(district) {
    const el = document.getElementById("timeline-district");
    if (el) el.textContent = district || "";
}

function updateStatus(status) {
    const el = document.getElementById("timeline-status");
    if (el) el.textContent = status || "";
}

function formatYear(year) {
    if (year < 0) return `${Math.abs(year)} BC`;
    return `${year}`;
}

function updateProgressBar() {
    let bar = document.getElementById("progress-bar");
    if (!bar) {
        bar = document.createElement("div");
        bar.id = "progress-bar";
        bar.style.cssText = "position:fixed;top:0;left:0;height:3px;background:linear-gradient(90deg,#c9a84c,#ffd700);z-index:999;transition:width 0.5s ease;";
        document.body.appendChild(bar);
    }
    const pct = totalStructures > 0 ? (builtStructures / totalStructures * 100) : 0;
    bar.style.width = pct + "%";

    let label = document.getElementById("progress-label");
    if (!label) {
        label = document.createElement("div");
        label.id = "progress-label";
        label.style.cssText = "position:fixed;top:4px;left:50%;transform:translateX(-50%);color:#c9a84c;font-size:0.7rem;z-index:999;font-family:inherit;letter-spacing:1px;";
        document.body.appendChild(label);
    }
    label.textContent = totalStructures > 0 ? `${builtStructures} / ${totalStructures} structures` : "";
}

// --- Tile detail popup ---

function showTileDetail(tile) {
    const el = document.getElementById("tile-detail");
    if (!tile || tile.terrain === "empty") {
        el.classList.remove("visible");
        return;
    }

    let html = `<button type="button" class="popup-close-btn" onclick="closeTileDetail()" aria-label="Close">&times;</button>`;
    html += `<h3>${tile.icon || ""} ${tile.building_name || tile.terrain}</h3>`;
    if (tile.description) html += `<p>${escapeHtml(tile.description)}</p>`;
    if (tile.period) html += `<p style="color:#8a7e6b;font-size:0.8rem;">Period: ${tile.period}</p>`;
    if (tile.historical_note) html += `<p class="historical-note">${escapeHtml(tile.historical_note)}</p>`;
    if (tile.scene) html += `<p class="scene">${escapeHtml(tile.scene)}</p>`;

    el.innerHTML = html;
    el.classList.add("visible");
}

function closeTileDetail() {
    document.getElementById("tile-detail").classList.remove("visible");
}

// --- Connection status ---

function setConnectionStatus(connected) {
    const el = document.getElementById("connection-status");
    if (!el) return;
    el.classList.remove("connected", "disconnected");
    el.classList.add(connected ? "connected" : "disconnected");
    el.textContent = connected ? "Connected" : "Reconnecting...";
    updateAiSettingsConnectionStatus();
}

// --- Reload / restart / timeline (keeps planning caches unless full RESET) ---

function reloadClientCode() {
    try {
        const u = new URL(window.location.href);
        u.searchParams.set("_ec", String(Date.now()));
        window.location.href = u.toString();
    } catch (e) {
        window.location.reload();
    }
}

function restartServerViaWebSocket() {
    return new Promise((resolve, reject) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            reject(new Error("WebSocket not connected"));
            return;
        }
        const timeoutId = setTimeout(() => {
            ws.removeEventListener("message", handler);
            reject(new Error("Timed out waiting for restart_server_result"));
        }, 12000);
        function handler(event) {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === "restart_server_result") {
                    clearTimeout(timeoutId);
                    ws.removeEventListener("message", handler);
                    resolve(msg);
                }
            } catch (e) {
                /* ignore */
            }
        }
        ws.addEventListener("message", handler);
        try {
            ws.send(JSON.stringify({ type: "restart_server" }));
        } catch (e) {
            clearTimeout(timeoutId);
            ws.removeEventListener("message", handler);
            reject(e);
        }
    });
}

async function restartServerFromUi() {
    if (
        !confirm(
            "Restart the Python server?\n\n" +
                "The current world is saved first. District and survey caches on disk are kept.\n" +
                "Requires ETERNAL_CITIES_RELOAD=1 when you started the server."
        )
    ) {
        return;
    }
    let j = null;
    try {
        const r = await fetch(apiUrl("/api/restart-server"), { method: "POST" });
        if (r.status === 404) {
            j = await restartServerViaWebSocket();
        } else {
            try {
                j = await r.json();
            } catch (e) {
                j = {};
            }
            if (!r.ok || !j.ok) {
                const hint = j.hint ? `\n\n${j.hint}` : "";
                alert((j.error || `HTTP ${r.status}`) + hint);
                return;
            }
        }
    } catch (e) {
        try {
            j = await restartServerViaWebSocket();
        } catch (e2) {
            alert((e.message || String(e)) + "\n\n" + (e2.message || String(e2)));
            return;
        }
    }
    if (!j || !j.ok) {
        const hint = j && j.hint ? `\n\n${j.hint}` : "";
        alert((j && j.error ? j.error : "Restart failed") + hint);
        return;
    }
    appendSystemMessage("Restarting server — WebSocket will reconnect when the process is back.");
}

function resetTimelineViaWebSocket() {
    return new Promise((resolve, reject) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            reject(new Error("WebSocket not connected"));
            return;
        }
        const timeoutId = setTimeout(() => {
            ws.removeEventListener("message", handler);
            reject(new Error("Timed out waiting for reset_timeline_result"));
        }, 12000);
        function handler(event) {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === "reset_timeline_result") {
                    clearTimeout(timeoutId);
                    ws.removeEventListener("message", handler);
                    resolve(msg);
                }
            } catch (e) {
                /* ignore */
            }
        }
        ws.addEventListener("message", handler);
        try {
            ws.send(JSON.stringify({ type: "reset_timeline" }));
        } catch (e) {
            clearTimeout(timeoutId);
            ws.removeEventListener("message", handler);
            reject(e);
        }
    });
}

async function resetTimelineFromUi() {
    if (
        !confirm(
            "Reset the run timer only?\n\n" +
                "The map, chat history, and Cartographus planning files on disk are not deleted."
        )
    ) {
        return;
    }
    let j = null;
    try {
        const r = await fetch(apiUrl("/api/reset-timeline"), { method: "POST" });
        if (r.status === 404) {
            j = await resetTimelineViaWebSocket();
        } else {
            try {
                j = await r.json();
            } catch (e) {
                j = {};
            }
            if (!r.ok || !j.ok) {
                alert(j.error || `HTTP ${r.status}`);
                return;
            }
        }
    } catch (e) {
        try {
            j = await resetTimelineViaWebSocket();
        } catch (e2) {
            alert((e.message || String(e)) + "\n\n" + (e2.message || String(e2)));
            return;
        }
    }
    if (!j || !j.ok) {
        alert((j && j.error) || "Reset timeline failed");
        return;
    }
    if (typeof j.started_at_s === "number" && Number.isFinite(j.started_at_s)) {
        applyRunStartFromScenario({ started_at_s: j.started_at_s });
        startRunLengthTicker();
    }
    appendSystemMessage("Run clock reset.");
}

// --- Reset ---

function resetWorld() {
    if (
        !confirm(
            "Full reset: delete saved world, district cache, and survey cache, and return to city selection?\n\n" +
                "Use “Restart server” or “Reload code” if you only want to refresh code without losing planning."
        )
    ) {
        return;
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "reset" }));
    } else {
        pendingResetOnOpen = true;
        connect();
    }
    autoResumeOnceThisPageAttempted = false;
    // Clear local state
    document.getElementById("chat-messages").innerHTML = "";
    currentMasterPlan = null;
    mapDescription = null;
    mapImageUrl = null;
    agentLogs.length = 0;
    totalStructures = 0;
    builtStructures = 0;
    runStartedAtMs = null;
    updateRunLength();
    const runWrap = document.getElementById("timeline-run-wrap");
    if (runWrap) runWrap.style.display = "none";
    if (runLengthInterval) {
        clearInterval(runLengthInterval);
        runLengthInterval = null;
    }
    applyTokenUsageToHeader({});
    updateTimeline("", null);
    try {
        localStorage.removeItem(RUN_START_LOCAL_KEY);
    } catch (e) {
        /* ignore */
    }

    // Show selection screen again
    selectedCity = null;
    selectedYear = null;
    document.querySelectorAll(".city-card").forEach(c => c.classList.remove("selected"));
    document.getElementById("year-control").classList.remove("active");
    document.getElementById("start-btn").disabled = true;
    const contSec = document.getElementById("select-continue-section");
    if (contSec) contSec.hidden = true;
    document.getElementById("select-overlay").classList.remove("hidden");
}

// --- Loading overlay ---

function showLoading(agent, message) {
    // Loading overlay removed — agent status bar handles this
}

function hideLoading() {
    // Loading overlay removed
}

// --- Map overlay ---

let currentMasterPlan = null;

function toggleMapOverlay() {
    const el = document.getElementById("map-overlay");
    el.classList.toggle("visible");
}

function closeMapOverlay() {
    document.getElementById("map-overlay").classList.remove("visible");
}

let mapDescription = null;
let mapImageUrl = null;
let mapImageSource = null;

function setMapDescription(desc) {
    mapDescription = desc;
    updateMapContent();
}

function setMapImage(url, source) {
    mapImageUrl = url;
    mapImageSource = source || "";
    updateMapContent();
}

function updateMasterPlan(plan) {
    currentMasterPlan = plan;
    updateMapContent();
}

function updateMapContent() {
    const content = document.getElementById("map-overlay-content");
    let html = "";

    // Real historical map image from web search
    if (mapImageUrl) {
        html += `<div style="text-align:center;margin-bottom:16px;">
            <img src="${escapeHtml(mapImageUrl)}"
                 style="width:100%;max-height:300px;object-fit:contain;border-radius:6px;border:1px solid #444;"
                 alt="Historical map" onerror="this.parentElement.style.display='none'">
            <p style="color:#666;font-size:0.7rem;margin-top:4px;">Source: ${escapeHtml(mapImageSource)}</p>
        </div>`;
    }

    // Cartographus's map description
    if (mapDescription) {
        html += `<div style="background:#1a2030;padding:12px;border-radius:6px;margin-bottom:16px;border:1px solid #e67e22;">
            <h4 style="color:#e67e22;margin-bottom:8px;font-size:0.85rem;">Cartographus's Survey Map</h4>
            <p style="color:#ddd;font-size:0.82rem;line-height:1.6;">${escapeHtml(mapDescription)}</p>
        </div>`;
    }

    // Interactive grid map — zoom and pan with mouse + toolbar
    if (currentMasterPlan && currentMasterPlan.length > 0) {
        html += `<div class="mini-map-wrap">`;
        html += `<div class="mini-map-toolbar" role="toolbar" aria-label="Map view controls">`;
        html += `<div class="mini-map-toolbar-row">`;
        html += `<button type="button" class="mini-map-tool-btn" title="Zoom in" aria-label="Zoom in" onclick="miniMapZoomIn()">+</button>`;
        html += `<button type="button" class="mini-map-tool-btn" title="Zoom out" aria-label="Zoom out" onclick="miniMapZoomOut()">−</button>`;
        html += `<button type="button" class="mini-map-tool-btn mini-map-tool-wide" title="Fit entire grid in view" onclick="miniMapFitView()">Fit</button>`;
        html += `<button type="button" class="mini-map-tool-btn" title="Pan north" aria-label="Pan north" onclick="miniMapPan(0,1)">↑</button>`;
        html += `<button type="button" class="mini-map-tool-btn" title="Pan west" aria-label="Pan west" onclick="miniMapPan(1,0)">←</button>`;
        html += `<button type="button" class="mini-map-tool-btn" title="Pan east" aria-label="Pan east" onclick="miniMapPan(-1,0)">→</button>`;
        html += `<button type="button" class="mini-map-tool-btn" title="Pan south" aria-label="Pan south" onclick="miniMapPan(0,-1)">↓</button>`;
        html += `</div>`;
        html += `</div>`;
        html += `<canvas id="mini-map" width="500" height="500" class="mini-map-canvas"></canvas>`;
        html += `<p class="mini-map-hint">Scroll wheel or +/− to zoom · drag to pan · arrows nudge · Fit shows the whole grid</p>`;
        html += `</div>`;
        html += `<p style="color:#e67e22;margin-bottom:8px;">${currentMasterPlan.length} structures planned:</p>`;
        for (const item of currentMasterPlan) {
            html += `<div class="plan-item">
                <span class="plan-name">${escapeHtml(item.name || "?")}</span>
                <span class="plan-type">${escapeHtml(item.building_type || "")}</span>
                <span style="color:#666;font-size:0.7rem;"> (${(item.tiles || []).length} tiles)</span>
                <div class="plan-desc">${escapeHtml(item.description || "")}</div>
                ${item.historical_note ? `<div class="plan-note">${escapeHtml(item.historical_note)}</div>` : ""}
            </div>`;
        }
    } else {
        html += "<p>The Cartographus is surveying...</p>";
    }

    content.innerHTML = html;

    // Draw mini map and attach zoom/pan controls
    if (currentMasterPlan) {
        setTimeout(() => { drawMiniMap(); setupMiniMapControls(); }, 50);
    }
}

// Mini-map state for zoom/pan
let miniMapZoom = 12;
let miniMapOffsetX = 0;
let miniMapOffsetY = 0;
let miniMapDragging = false;
let miniMapPrevMouse = { x: 0, y: 0 };

function setupMiniMapControls() {
    const canvas = document.getElementById("mini-map");
    if (!canvas) return;

    canvas.addEventListener("wheel", e => {
        e.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const mx = (e.clientX - rect.left) / rect.width * canvas.width;
        const my = (e.clientY - rect.top) / rect.height * canvas.height;

        // Zoom toward mouse position
        const oldZoom = miniMapZoom;
        miniMapZoom = Math.max(3, Math.min(40, miniMapZoom * (e.deltaY < 0 ? 1.15 : 0.87)));
        const ratio = miniMapZoom / oldZoom;
        miniMapOffsetX = mx - (mx - miniMapOffsetX) * ratio;
        miniMapOffsetY = my - (my - miniMapOffsetY) * ratio;
        drawMiniMap();
    }, { passive: false });

    canvas.addEventListener("mousedown", e => {
        miniMapDragging = true;
        miniMapPrevMouse = { x: e.clientX, y: e.clientY };
        canvas.style.cursor = "grabbing";
    });
    canvas.addEventListener("mousemove", e => {
        if (!miniMapDragging) return;
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        miniMapOffsetX += (e.clientX - miniMapPrevMouse.x) * scaleX;
        miniMapOffsetY += (e.clientY - miniMapPrevMouse.y) * scaleY;
        miniMapPrevMouse = { x: e.clientX, y: e.clientY };
        drawMiniMap();
    });
    canvas.addEventListener("mouseup", () => { miniMapDragging = false; canvas.style.cursor = "grab"; });
    canvas.addEventListener("mouseleave", () => { miniMapDragging = false; canvas.style.cursor = "grab"; });

    miniMapFitView();
}

function drawMiniMap() {
    const canvas = document.getElementById("mini-map");
    if (!canvas || !currentMasterPlan) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    const s = miniMapZoom;
    const ox = miniMapOffsetX, oy = miniMapOffsetY;

    // Background
    ctx.fillStyle = "#1a1a2e";
    ctx.fillRect(0, 0, W, H);

    // Grid lines
    ctx.strokeStyle = "#2a2a4a";
    ctx.lineWidth = 0.5;
    const gW = worldGridWidth;
    const gH = worldGridHeight;
    const gridMax = Math.max(gW, gH);
    for (let i = 0; i <= gridMax; i++) {
        const gx = i * s + ox, gy = i * s + oy;
        if (gx >= 0 && gx <= W) { ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, H); ctx.stroke(); }
        if (gy >= 0 && gy <= H) { ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(W, gy); ctx.stroke(); }
    }

    // Grid boundary
    ctx.strokeStyle = "#444";
    ctx.lineWidth = 1;
    ctx.strokeRect(ox, oy, gW * s, gH * s);

    const typeColors = {
        temple: "#ffd700", basilica: "#deb887", road: "#808080", forum: "#d4c67a",
        insula: "#cd853f", domus: "#d2691e", market: "#deb887", water: "#3498db",
        garden: "#27ae60", wall: "#5d4037", gate: "#8b7355", monument: "#e8e8e8",
        aqueduct: "#87ceeb", thermae: "#b0e0e6", circus: "#f4a460", amphitheater: "#daa520",
        bridge: "#a0a0a0", taberna: "#b8860b", warehouse: "#8b8378", grass: "#5a9a4a"
    };

    // Plot structure tiles
    for (const item of currentMasterPlan) {
        const color = typeColors[item.building_type] || "#d4a373";
        ctx.fillStyle = color;
        for (const t of (item.tiles || [])) {
            ctx.fillRect(t.x * s + ox, t.y * s + oy, s - 0.5, s - 0.5);
        }
    }

    // Labels — with collision detection and dark background for readability
    if (s >= 6) {
        const fontSize = Math.max(7, Math.min(14, s * 0.7));
        ctx.font = `bold ${fontSize}px sans-serif`;
        const placed = []; // [{x, y, w, h}] — occupied label regions

        for (const item of currentMasterPlan) {
            if (!item.tiles || item.tiles.length === 0) continue;

            // Find center tile for label placement
            let cx = 0, cy = 0;
            for (const t of item.tiles) { cx += t.x; cy += t.y; }
            cx /= item.tiles.length;
            cy /= item.tiles.length;

            const label = s >= 12 ? item.name : item.name.substring(0, 10);
            const textW = ctx.measureText(label).width;
            const textH = fontSize;
            const pad = 2;
            let lx = cx * s + ox + 2;
            let ly = cy * s + oy + s * 0.5;

            // Check for overlap and nudge down if colliding
            let attempts = 0;
            while (attempts < 5) {
                const rect = { x: lx - pad, y: ly - textH - pad, w: textW + pad * 2, h: textH + pad * 2 };
                const overlaps = placed.some(p =>
                    rect.x < p.x + p.w && rect.x + rect.w > p.x &&
                    rect.y < p.y + p.h && rect.y + rect.h > p.y
                );
                if (!overlaps) break;
                ly += textH + pad * 2;
                attempts++;
            }
            if (attempts >= 5) continue; // skip label if no room

            // Dark background pill
            const bgX = lx - pad, bgY = ly - textH;
            ctx.fillStyle = "rgba(10, 12, 24, 0.85)";
            ctx.beginPath();
            ctx.roundRect(bgX, bgY, textW + pad * 2, textH + pad * 2, 3);
            ctx.fill();

            // Text
            ctx.fillStyle = "#fff";
            ctx.fillText(label, lx, ly);

            placed.push({ x: bgX, y: bgY, w: textW + pad * 2, h: textH + pad * 2 });
        }
    }
}

/** Zoom mini-map toward canvas center (same math as scroll wheel). */
function miniMapApplyZoomAtCenter(factor) {
    const canvas = document.getElementById("mini-map");
    if (!canvas) return;
    const W = canvas.width;
    const H = canvas.height;
    const mx = W / 2;
    const my = H / 2;
    const oldZoom = miniMapZoom;
    miniMapZoom = Math.max(3, Math.min(40, miniMapZoom * factor));
    const ratio = miniMapZoom / oldZoom;
    miniMapOffsetX = mx - (mx - miniMapOffsetX) * ratio;
    miniMapOffsetY = my - (my - miniMapOffsetY) * ratio;
    drawMiniMap();
}

function miniMapZoomIn() {
    miniMapApplyZoomAtCenter(1.15);
}

function miniMapZoomOut() {
    miniMapApplyZoomAtCenter(0.87);
}

/** Fit the full city grid into the canvas with a small margin. */
function miniMapFitView() {
    const canvas = document.getElementById("mini-map");
    if (!canvas || !currentMasterPlan) return;
    const W = canvas.width;
    const H = canvas.height;
    const gW = worldGridWidth;
    const gH = worldGridHeight;
    const pad = 0.92;
    const s = Math.min(W / gW, H / gH) * pad;
    miniMapZoom = Math.max(3, Math.min(40, s));
    miniMapOffsetX = (W - gW * miniMapZoom) / 2;
    miniMapOffsetY = (H - gH * miniMapZoom) / 2;
    drawMiniMap();
}

/**
 * Nudge the view (same math as drag-pan on the canvas).
 * dx, dy ∈ { -1, 0, 1 } for one step along that axis.
 */
function miniMapPan(dx, dy) {
    const canvas = document.getElementById("mini-map");
    if (!canvas) return;
    const step = Math.max(24, Math.round(canvas.width * 0.08));
    miniMapOffsetX += dx * step;
    miniMapOffsetY += dy * step;
    drawMiniMap();
}

// --- Log viewer ---

const agentLogs = [];

function addLog(sender, type, content) {
    const time = new Date().toLocaleTimeString();
    agentLogs.push({ time, sender, type, content });
    // Keep last 200
    if (agentLogs.length > 200) agentLogs.shift();
    updateLogView();
}

function updateLogView() {
    const el = document.getElementById("log-content");
    if (!el || !document.getElementById("log-overlay").classList.contains("visible")) return;
    el.innerHTML = agentLogs.map(l =>
        `<div class="log-entry">
            <span style="color:#666">${l.time}</span>
            <span style="color:${AGENT_COLORS[l.sender] || '#888'}">[${l.sender}]</span>
            <span style="color:#555">(${l.type})</span>
            <span style="color:#aaa">${escapeHtml(l.content.substring(0, 200))}</span>
        </div>`
    ).join("");
    el.scrollTop = el.scrollHeight;
}

function toggleLogOverlay() {
    const el = document.getElementById("log-overlay");
    el.classList.toggle("visible");
    if (el.classList.contains("visible")) updateLogView();
}

function closeLogOverlay() {
    document.getElementById("log-overlay").classList.remove("visible");
}

// --- Utility ---

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// --- City Selection Screen ---

let cities = [];
let selectedCity = null;
let selectedYear = null;

async function loadCities() {
    const res = await fetch(apiUrl("/api/cities"));
    cities = await res.json();
    renderCityGrid();
}

function renderCityGrid() {
    const grid = document.getElementById("city-grid");
    grid.innerHTML = "";
    for (const city of cities) {
        const card = document.createElement("div");
        card.className = "city-card";
        card.dataset.name = city.name;
        const minLabel = formatYear(city.year_min);
        const maxLabel = formatYear(Math.min(city.year_max, 2024));
        card.innerHTML = `
            <div class="city-name">${escapeHtml(city.name)}</div>
            <div class="city-years">${minLabel} — ${maxLabel}</div>
            <div class="city-desc">${escapeHtml(city.description)}</div>
        `;
        card.addEventListener("click", () => selectCity(city));
        grid.appendChild(card);
    }
}

function selectCity(city) {
    selectedCity = city;
    // Highlight selected card
    document.querySelectorAll(".city-card").forEach(c => c.classList.remove("selected"));
    document.querySelector(`.city-card[data-name="${city.name}"]`).classList.add("selected");

    // Enable year slider
    const yearControl = document.getElementById("year-control");
    yearControl.classList.add("active");
    const slider = document.getElementById("year-slider");
    slider.min = city.year_min;
    slider.max = Math.min(city.year_max, 2024);
    // Default to middle of range
    const mid = Math.round((city.year_min + Math.min(city.year_max, 2024)) / 2);
    slider.value = mid;
    updateYearDisplay(mid);

    document.getElementById("year-min-label").textContent = formatYear(city.year_min);
    document.getElementById("year-max-label").textContent = formatYear(Math.min(city.year_max, 2024));

    // Enable start button
    document.getElementById("start-btn").disabled = false;
}

function updateYearDisplay(year) {
    selectedYear = parseInt(year);
    document.getElementById("year-display").textContent = formatYear(selectedYear);
}

function startReconstruction() {
    if (!selectedCity || selectedYear === null) return;
    awaitingSessionChoice = false;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        pendingStartOnOpen = { city: selectedCity.name, year: selectedYear };
        connect();
        return;
    }

    ws.send(JSON.stringify({
        type: "start",
        city: selectedCity.name,
        year: selectedYear,
    }));

    document.getElementById("select-overlay").classList.add("hidden");
}

function continueCurrentSession() {
    awaitingSessionChoice = false;
    document.getElementById("select-continue-section").hidden = true;
    pendingResumeOnOpen = true;
    connect();
}

async function initEternalCitiesSession() {
    awaitingSessionChoice = false;
    await loadCities();
    try {
        const snap = await fetchJsonWithTimeout(apiUrl("/api/session"), 6000);
        if (snap && snap.has_active_scenario) {
            awaitingSessionChoice = true;
            const lead = document.getElementById("select-continue-lead");
            if (lead) {
                const y = typeof snap.year === "number" ? formatYear(snap.year) : "—";
                const loc = snap.city || "saved city";
                const per = snap.period ? ` · ${snap.period}` : "";
                lead.textContent = `Continue your saved session: ${loc}${per} (year ${y}).`;
            }
            document.getElementById("select-continue-section").hidden = false;
            return;
        }
    } catch (e) {
        console.warn("GET /api/session failed:", e);
    }
    connect();
}

// --- Init ---

document.addEventListener("DOMContentLoaded", () => {
    const container = document.getElementById("world-container");
    renderer = new WorldRenderer(container);

    window.addEventListener("world-render-error", (e) => {
        const d = e.detail || {};
        console.error("[WorldRenderer]", d.error, d.tile, d.key);
        appendSystemMessage(`Render error: ${d.error}`);
    });

    // Tile click -> request detail from server
    renderer.renderer3d.domElement.addEventListener("tileclick", (e) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "tile_info",
                x: e.detail.x,
                y: e.detail.y,
            }));
        }
    });

    // Close tile detail on Escape
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeTileDetail();
    });

    // Year slider
    document.getElementById("year-slider").addEventListener("input", (e) => {
        updateYearDisplay(e.target.value);
    });

    // Start button
    document.getElementById("start-btn").addEventListener("click", startReconstruction);

    const continueBtn = document.getElementById("select-continue-btn");
    if (continueBtn) continueBtn.addEventListener("click", continueCurrentSession);

    const FLOATING_TOOLS_POS_KEY = "eternal_cities_floating_tools_pos";
    const FLOATING_CAMERA_POS_KEY = "eternal_cities_floating_camera_pos";
    const FLOATING_TOOLS_MIN_WIDTH_PX = 180;

    // Make all panels draggable by their header/background
    function makeDraggable(el, handleSelector) {
        if (!el) return;
        let dragging = false, offX = 0, offY = 0;
        const handle = handleSelector ? el.querySelector(handleSelector) : el;
        if (!handle) return;
        handle.style.cursor = "grab";
        handle.addEventListener("mousedown", e => {
            if (e.target.tagName === "BUTTON" || e.target.classList.contains("nav-btn") ||
                e.target.classList.contains("close-btn") || e.target.classList.contains("popup-close-btn")) return;
            dragging = true;
            offX = e.clientX - el.getBoundingClientRect().left;
            offY = e.clientY - el.getBoundingClientRect().top;
            handle.style.cursor = "grabbing";
            e.preventDefault();
        });
        document.addEventListener("mousemove", e => {
            if (!dragging) return;
            el.style.left = (e.clientX - offX) + "px";
            el.style.top = (e.clientY - offY) + "px";
            el.style.bottom = "auto";
            el.style.right = "auto";
        });
        document.addEventListener("mouseup", () => {
            if (dragging) { dragging = false; handle.style.cursor = "grab"; }
        });
    }

    function initFloatingHudPanel(panelId, handleId, positionStorageKey) {
        const panel = document.getElementById(panelId);
        const handle = document.getElementById(handleId);
        if (!panel || !handle) return;
        panel.style.position = "fixed";
        try {
            const raw = localStorage.getItem(positionStorageKey);
            if (raw) {
                const p = JSON.parse(raw);
                if (Number.isFinite(p.left) && Number.isFinite(p.top)) {
                    panel.style.left = `${p.left}px`;
                    panel.style.top = `${p.top}px`;
                    panel.style.bottom = "auto";
                    panel.style.right = "auto";
                }
            }
        } catch (e) {
            /* ignore */
        }
        let dragging = false;
        let offX = 0;
        let offY = 0;
        handle.addEventListener("mousedown", (e) => {
            dragging = true;
            const r = panel.getBoundingClientRect();
            offX = e.clientX - r.left;
            offY = e.clientY - r.top;
            handle.style.cursor = "grabbing";
            e.preventDefault();
        });
        document.addEventListener("mousemove", (e) => {
            if (!dragging) return;
            panel.style.left = `${e.clientX - offX}px`;
            panel.style.top = `${e.clientY - offY}px`;
            panel.style.bottom = "auto";
            panel.style.right = "auto";
        });
        document.addEventListener("mouseup", () => {
            if (!dragging) return;
            dragging = false;
            handle.style.cursor = "grab";
            try {
                const r = panel.getBoundingClientRect();
                localStorage.setItem(positionStorageKey, JSON.stringify({ left: r.left, top: r.top }));
            } catch (err) {
                /* ignore */
            }
        });
    }

    function floatingToolsMaxWidthPx() {
        const raw = getComputedStyle(document.documentElement).getPropertyValue("--chat-sidebar-width").trim();
        let sidebar = 420;
        if (raw.endsWith("px")) {
            const n = parseFloat(raw);
            if (!Number.isNaN(n)) sidebar = n;
        }
        return Math.max(FLOATING_TOOLS_MIN_WIDTH_PX, window.innerWidth - sidebar - 32);
    }

    function clampFloatingToolsWidthPx(w) {
        const lo = FLOATING_TOOLS_MIN_WIDTH_PX;
        const hi = floatingToolsMaxWidthPx();
        return Math.min(hi, Math.max(lo, w));
    }

    function initFloatingToolsPanel() {
        const panel = document.getElementById("floating-tools-panel");
        const handle = document.getElementById("floating-tools-handle");
        const resizeEl = document.getElementById("floating-tools-resize");
        if (!panel || !handle) return;
        panel.style.position = "fixed";

        function saveToolsPanelLayout() {
            try {
                const r = panel.getBoundingClientRect();
                localStorage.setItem(
                    FLOATING_TOOLS_POS_KEY,
                    JSON.stringify({
                        left: r.left,
                        top: r.top,
                        width: clampFloatingToolsWidthPx(r.width),
                    })
                );
            } catch (err) {
                /* ignore */
            }
        }

        try {
            const raw = localStorage.getItem(FLOATING_TOOLS_POS_KEY);
            if (raw) {
                const p = JSON.parse(raw);
                if (Number.isFinite(p.left) && Number.isFinite(p.top)) {
                    panel.style.left = `${p.left}px`;
                    panel.style.top = `${p.top}px`;
                    panel.style.bottom = "auto";
                    panel.style.right = "auto";
                }
                if (Number.isFinite(p.width)) {
                    panel.style.width = `${clampFloatingToolsWidthPx(p.width)}px`;
                }
            }
        } catch (e) {
            /* ignore */
        }

        let dragging = false;
        let offX = 0;
        let offY = 0;
        handle.addEventListener("mousedown", (e) => {
            dragging = true;
            const r = panel.getBoundingClientRect();
            offX = e.clientX - r.left;
            offY = e.clientY - r.top;
            handle.style.cursor = "grabbing";
            e.preventDefault();
        });
        document.addEventListener("mousemove", (e) => {
            if (!dragging) return;
            panel.style.left = `${e.clientX - offX}px`;
            panel.style.top = `${e.clientY - offY}px`;
            panel.style.bottom = "auto";
            panel.style.right = "auto";
        });
        document.addEventListener("mouseup", () => {
            if (!dragging) return;
            dragging = false;
            handle.style.cursor = "grab";
            saveToolsPanelLayout();
        });

        let resizing = false;
        let resizeStartX = 0;
        let resizeStartWidth = 0;
        if (resizeEl) {
            resizeEl.addEventListener("pointerdown", (e) => {
                e.preventDefault();
                e.stopPropagation();
                resizing = true;
                resizeStartX = e.clientX;
                resizeStartWidth = panel.getBoundingClientRect().width;
                try {
                    resizeEl.setPointerCapture(e.pointerId);
                } catch (err) {
                    /* ignore */
                }
            });
            resizeEl.addEventListener("pointermove", (e) => {
                if (!resizing) return;
                const nextW = clampFloatingToolsWidthPx(resizeStartWidth + (e.clientX - resizeStartX));
                panel.style.width = `${nextW}px`;
            });
            const endResize = (e) => {
                if (!resizing) return;
                resizing = false;
                try {
                    resizeEl.releasePointerCapture(e.pointerId);
                } catch (err) {
                    /* ignore */
                }
                saveToolsPanelLayout();
            };
            resizeEl.addEventListener("pointerup", endResize);
            resizeEl.addEventListener("pointercancel", endResize);
        }

        window.addEventListener(
            "resize",
            () => {
                const w = panel.getBoundingClientRect().width;
                const clamped = clampFloatingToolsWidthPx(w);
                if (Math.abs(clamped - w) > 0.5) {
                    panel.style.width = `${clamped}px`;
                    saveToolsPanelLayout();
                }
            },
            { passive: true }
        );
    }

    initFloatingToolsPanel();
    initFloatingHudPanel("floating-camera-panel", "floating-camera-handle", FLOATING_CAMERA_POS_KEY);
    makeDraggable(document.getElementById("map-overlay"), ".map-overlay-header");
    makeDraggable(document.getElementById("log-overlay"), ".map-overlay-header");
    makeDraggable(document.getElementById("tile-detail"));

    initChatPanelLayout();

    // Wire up nav buttons via data attributes (avoids onclick/drag conflicts)
    document.querySelectorAll(".nav-btn[data-action]").forEach(btn => {
        btn.addEventListener("click", e => {
            e.stopPropagation();
            if (!renderer || renderer._failed) return;
            const action = btn.dataset.action;
            if (action === "pan") renderer.panCamera(+btn.dataset.x, +btn.dataset.z);
            else if (action === "orbit") renderer.orbitCamera(+btn.dataset.a, +btn.dataset.p);
            else if (action === "zoom") renderer.zoomCamera(+btn.dataset.f);
        });
    });

    void initEternalCitiesSession();

    // BFCache restore (back/forward): WebSocket is dead; reconnect once without stacking timers.
    window.addEventListener("pageshow", (ev) => {
        if (!ev.persisted) return;
        clearWebSocketReconnectTimer();
        reconnectDelay = 1000;
        connect();
    });
});
