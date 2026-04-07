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
/** Grid dimensions and origin from last world_state (mini-map, UI). */
let worldGridWidth = 80;
let worldGridHeight = 80;
let worldMinX = 0;
let worldMinY = 0;
/** One automatic resume per full page load when server sends suggest_auto_resume (reconnect after pause). */
let autoResumeOnceThisPageAttempted = false;
let reconnectDelay = 1000;
/** Single scheduled reconnect from ws.onclose — cleared on manual connect() or AI reconnect. */
let wsReconnectTimer = null;
/** While the user must pick Continue vs new city, do not auto-reconnect (no socket yet). */
const WS_PERIODIC_RECONNECT_MS = 15000;
let wsPeriodicReconnectIntervalId = null;

function clearWebSocketReconnectTimer() {
    if (wsReconnectTimer != null) {
        clearTimeout(wsReconnectTimer);
        wsReconnectTimer = null;
    }
}

/** Connection log buffer — kept in memory, appended to run log download. */
const _wsLogBuffer = [];
const _WS_LOG_MAX = 200;

function wsLog(seq, msg) {
    const ts = new Date().toISOString().slice(11, 23);
    const line = `[ws#${seq}] ${ts} ${msg}`;
    console.log(line);
    _wsLogBuffer.push(line);
    if (_wsLogBuffer.length > _WS_LOG_MAX) _wsLogBuffer.shift();
}

/** Get connection log as text (for diagnostics / log download). */
function getWsLogText() {
    return _wsLogBuffer.join("\n");
}

/** Belt-and-suspenders: if onclose/backoff misses a dead socket, retry periodically. */
function ensureWebSocketPeriodicReconnect() {
    if (wsPeriodicReconnectIntervalId != null) return;
    wsPeriodicReconnectIntervalId = setInterval(() => {
        try {
            if (awaitingSessionChoice) return;
            if (ws && ws.readyState === WebSocket.OPEN) return;
            if (ws && ws.readyState === WebSocket.CONNECTING) return;
            if (ws && ws.readyState === WebSocket.CLOSING) return;
            wsLog(0, `periodic reconnect fired (readyState=${ws ? ws.readyState : "null"})`);
            reconnectDelay = 1000;
            clearWebSocketReconnectTimer();
            connect();
        } catch (e) {
            console.warn("Periodic WebSocket reconnect failed:", e);
        }
    }, WS_PERIODIC_RECONNECT_MS);
}

/** If true, open WebSocket only after "Continue" or after BEGIN / reset (saved session on reload). */
let pendingResumeOnOpen = false;
let pendingStartOnOpen = null;
let pendingResetOnOpen = false;
/** True while the city overlay shows "Continue current session" and the user has not chosen yet. */
let awaitingSessionChoice = false;

let totalStructures = 0;
let builtStructures = 0;
let totalTilesPlaced = 0;
let lastBuildProgressAt = null;
let buildProgressTimes = [];
let runStartedAtMs = null;
let runLengthInterval = null;
let isPaused = false;
/** Track last manual camera interaction — flyTo respects user control. */
let lastManualCameraMs = 0;

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
            // connect() already handles closing the previous socket cleanly;
            // calling it directly avoids a 50ms window where ws.onmessage is null
            // and incoming messages (tile updates, chat) are silently dropped.
            connect();
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

/** Connection sequence counter for log correlation. */
let wsConnectSeq = 0;

function connect() {
    clearWebSocketReconnectTimer();
    const seq = ++wsConnectSeq;
    const url = eternalCitiesWsUrl();
    wsLog(seq, `connect() called — url=${url}`);

    if (ws && ws.readyState !== WebSocket.CLOSED) {
        wsLog(seq, `closing previous socket (readyState=${ws.readyState})`);
        try {
            ws.onmessage = null;
            ws.onclose = null;
            ws.onerror = null;
        } catch (e) {
            /* ignore */
        }
        try {
            ws.close();
        } catch (e) {
            /* ignore */
        }
    }
    ws = new WebSocket(url);

    ws.onopen = () => {
        wsLog(seq, "OPEN — connected");
        setConnectionStatus(true);
        reconnectDelay = 1000;
        updateAiSettingsConnectionStatus();
        // Only send pending actions on the FIRST connect (not on auto-reconnects).
        // Pending flags are one-shot: cleared after sending.
        if (pendingResetOnOpen) {
            pendingResetOnOpen = false;
            wsLog(seq, "sending pending reset (one-shot)");
            try {
                ws.send(JSON.stringify({ type: "reset" }));
            } catch (e) {
                console.error("reset send failed:", e);
            }
        } else if (pendingStartOnOpen) {
            const p = pendingStartOnOpen;
            pendingStartOnOpen = null;
            wsLog(seq, `sending pending start city=${p.city} year=${p.year} (one-shot)`);
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
            wsLog(seq, "sending pending resume (one-shot)");
            try {
                ws.send(JSON.stringify({ type: "resume" }));
            } catch (e) {
                console.error("resume send failed:", e);
            }
            document.getElementById("select-overlay").classList.add("hidden");
        } else {
            wsLog(seq, "reconnect — no pending actions (state-sync only)");
        }
    };

    ws.onclose = (ev) => {
        wsLog(seq, `CLOSE — code=${ev.code} reason=${ev.reason || "(none)"} wasClean=${ev.wasClean} nextDelay=${reconnectDelay}ms`);
        setConnectionStatus(false);
        updateAiSettingsConnectionStatus();
        clearWebSocketReconnectTimer();
        wsReconnectTimer = setTimeout(() => {
            wsReconnectTimer = null;
            wsLog(seq, `backoff timer fired — reconnecting (was ${reconnectDelay}ms)`);
            connect();
        }, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 10000);
    };

    ws.onerror = (err) => {
        wsLog(seq, `ERROR — ${err.type || err}`);
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleMessage(msg);
        } catch (e) {
            console.error("Failed to parse WebSocket message:", e, "raw:", String(event.data).slice(0, 200));
        }
    };

    // Keepalive ping every 30s to prevent browser/proxy idle disconnect
    if (window._wsKeepalive) clearInterval(window._wsKeepalive);
    window._wsKeepalive = setInterval(() => {
        try {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "ping" }));
            }
        } catch (e) { /* ignore */ }
    }, 30000);
}

function handleMessage(msg) {
    switch (msg.type) {
        case "world_state":
            if (typeof msg.width === "number" && msg.width > 0) worldGridWidth = msg.width;
            if (typeof msg.height === "number" && msg.height > 0) worldGridHeight = msg.height;
            if (typeof msg.min_x === "number") worldMinX = msg.min_x;
            if (typeof msg.min_y === "number") worldMinY = msg.min_y;
            // Don't clear chat — dedup in appendChat() handles replay duplicates.
            // Chat is only cleared on explicit reset (see "reset" send path).
            renderer.init(msg);
            // Populate minimap from initial world state tiles
            miniMapClear();
            if (Array.isArray(msg.tiles)) {
                miniMapAddTiles(msg.tiles);
            }
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
            updatePauseButton(false);
            showPauseButton();
            break;

        case "token_usage":
            applyTokenUsageToHeader(msg.by_ui_agent);
            applyTokenUsageSummary(msg.by_ui_agent);
            break;

        case "tile_update":
            if (!Array.isArray(msg.tiles)) break;
            renderer.updateTiles(msg.tiles);
            miniMapAddTiles(msg.tiles);
            drawMiniMap();
            if (msg.period) updateTimeline(msg.period, msg.year);
            builtStructures++;
            totalTilesPlaced += msg.tiles.length;
            updateProgressBar();
            if (msg.tiles[0] && msg.tiles[0].building_name) {
                const statusEl = document.getElementById("timeline-status");
                if (statusEl) statusEl.textContent = `Placed: ${msg.tiles[0].building_name} (${msg.tiles.length} tiles, ${totalTilesPlaced} total)`;
            }
            // Auto-fly camera to new building (skip if user is manually controlling)
            if (msg.tiles && msg.tiles.length > 0 && renderer.flyTo) {
                if (Date.now() - lastManualCameraMs > 8000) {
                    const t = msg.tiles[0];
                    renderer.flyTo((t.x + 0.5) * TILE_SIZE, (t.y + 0.5) * TILE_SIZE);
                }
            }
            hideLoading();
            updatePauseButton(false);
            break;

        case "build_progress":
            if (!msg.done || !msg.total) break;
            updateBuildProgress(msg);
            break;

        case "chat":
            appendChat(msg);
            break;

        case "typing":
            showTyping(msg.sender, msg.partial);
            break;

        case "phase":
            appendPhaseAnnouncement(msg);
            if (msg.wave && msg.index && msg.total_districts) {
                const genLabel = msg.generation != null ? ` [Gen ${msg.generation}]` : "";
                updateDistrict(`${msg.wave}: ${msg.district} (${msg.index}/${msg.total_districts})${genLabel}`);
            } else if (msg.index && msg.total_districts) {
                updateDistrict(`${msg.district} (${msg.index}/${msg.total_districts})`);
            } else {
                updateDistrict(msg.district);
            }
            hideLoading();
            break;

        case "timeline":
            updateTimeline(msg.period, msg.year);
            break;

        case "tile_detail":
            showTileDetail(msg.tile);
            break;

        case "agent_status":
            setAgentStatus(msg.agent, msg.status, msg.thinking_started_at_s);
            if (msg.status !== "thinking") hideLoading();
            break;

        case "loading":
            showLoading(msg.agent, msg.message);
            setAgentStatus(msg.agent, "thinking");
            break;

        case "master_plan": {
            if (!Array.isArray(msg.plan)) break;
            // Dedup: on reconnect replay, master_plan is re-sent; avoid inflating totalStructures.
            const mpHash = `master_plan|${msg.district || ""}|${msg.plan.length}`;
            if (!_chatSeenHashes.has(mpHash)) {
                _chatSeenHashes.add(mpHash);
                totalStructures += msg.plan.length;
            }
            updateMasterPlan(msg.plan);
            updateProgressBar();
            break;
        }

        case "placement_warnings": {
            if (msg.warnings && msg.warnings.length) {
                const district = msg.district || "district";
                const pwHash = `pw|${district}|${msg.count || msg.warnings.length}`;
                if (_chatSeenHashes.has(pwHash)) break;
                _chatSeenHashes.add(pwHash);
                const preview = msg.warnings.slice(0, 6).join(" · ");
                appendSystemMessage(`Placement check (${district}, ${msg.count || msg.warnings.length}): ${preview}`);
            }
            break;
        }

        case "map_description":
            setMapDescription(msg.description);
            break;

        case "map_image":
            setMapImage(msg.url, msg.source);
            break;

        case "complete":
            resetAllAgentStatus();
            hidePauseButton();
            if (!_chatSeenHashes.has("complete")) {
                _chatSeenHashes.add("complete");
                appendSystemMessage("Build complete! The city stands in its glory.");
                try {
                    if (Notification.permission === "granted") {
                        new Notification("Eternal Cities", { body: "City build complete!", icon: "/static/favicon.svg" });
                    } else if (Notification.permission !== "denied") {
                        Notification.requestPermission();
                    }
                } catch (e) { /* ignore */ }
            }
            document.title = "Done! — Eternal Cities";
            break;

        case "generation_complete":
            appendSystemMessage(`Generation ${msg.generation} complete — preparing expansion...`);
            document.title = `Gen ${msg.generation} — Eternal Cities`;
            break;

        case "expanding":
            appendSystemMessage(`Expanding city — generation ${msg.generation}...`);
            document.title = `Expanding Gen ${msg.generation} — Eternal Cities`;
            break;

        case "paused":
            showPausedOverlay(msg);
            resetAllAgentStatus();
            updatePauseButton(true);
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

function updateBuildProgress(msg) {
    const now = performance.now();
    if (lastBuildProgressAt !== null) {
        buildProgressTimes.push(now - lastBuildProgressAt);
    }
    lastBuildProgressAt = now;

    const done = msg.done;
    const total = msg.total;
    const name = msg.structure || "structure";
    const btype = msg.building_type || "";
    const typeEmoji = {
        temple: "\u26ea", basilica: "\ud83c\udfdb\ufe0f", insula: "\ud83c\udfe0", domus: "\ud83c\udfe1",
        thermae: "\u2668\ufe0f", amphitheater: "\ud83c\udfdf\ufe0f", market: "\ud83c\udfea", gate: "\u26e9\ufe0f",
        monument: "\ud83d\uddff", wall: "\ud83e\uddf1", road: "\ud83d\udee3\ufe0f", water: "\ud83c\udf0a",
        garden: "\ud83c\udf3f", forum: "\u2696\ufe0f", bridge: "\ud83c\udf09", warehouse: "\ud83d\udce6",
    }[btype] || "";
    let text = `${typeEmoji} ${done}/${total}: ${name}`;

    if (buildProgressTimes.length >= 1 && done < total) {
        const avgMs = buildProgressTimes.reduce((a, b) => a + b, 0) / buildProgressTimes.length;
        const remaining = total - done;
        const etaMs = avgMs * remaining;
        const etaSec = Math.round(etaMs / 1000);
        let etaStr;
        if (etaSec < 60) {
            etaStr = `${etaSec}s`;
        } else {
            etaStr = `${Math.round(etaSec / 60)}m`;
        }
        text += ` (~${etaStr} left)`;
    }

    const el = document.getElementById("timeline-status");
    if (el) el.textContent = text;
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
    const s = Math.max(0, elapsedMs) / 1000;
    if (s < 60) return `${s.toFixed(1)}s`;
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}m ${sec}s`;
}

function setAgentStatus(agent, status, thinkingStartedAtS) {
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
        const start =
            typeof thinkingStartedAtS === "number" && Number.isFinite(thinkingStartedAtS)
                ? thinkingStartedAtS * 1000
                : Date.now();
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
    for (const agent of ["cartographus", "urbanista"]) {
        setAgentStatus(agent, "idle");
    }
}

// --- Pause / Resume button ---

function updatePauseButton(paused) {
    isPaused = paused;
    const btn = document.getElementById("pause-btn");
    if (!btn) return;
    if (paused) {
        btn.textContent = "Resume";
        btn.classList.add("paused");
        btn.title = "Resume build";
    } else {
        btn.textContent = "Pause";
        btn.classList.remove("paused");
        btn.title = "Pause build";
    }
}

function showPauseButton() {
    const btn = document.getElementById("pause-btn");
    if (btn) btn.classList.remove("hidden");
}

function hidePauseButton() {
    const btn = document.getElementById("pause-btn");
    if (btn) btn.classList.add("hidden");
}

// --- Chat (dedup across reconnect replays) ---

/** Set of content hashes for messages already in the DOM — prevents replay duplication. */
const _chatSeenHashes = new Set();
const _CHAT_SEEN_MAX = 600;

function _chatHash(msg) {
    const c = msg.content || "";
    // Use first 80 + last 40 chars + length to minimize collisions on long similar messages
    const snippet = c.length <= 120 ? c : c.slice(0, 80) + c.slice(-40);
    return `${msg.sender}|${msg.msg_type || ""}|${snippet}|${c.length}`;
}

function clearChatDedup() {
    _chatSeenHashes.clear();
}

function appendChat(msg) {
    const h = _chatHash(msg);
    if (_chatSeenHashes.has(h)) return;  // Already displayed — skip
    _chatSeenHashes.add(h);
    if (_chatSeenHashes.size > _CHAT_SEEN_MAX) {
        // Evict oldest 100 entries to keep the set bounded.
        const it = _chatSeenHashes.values();
        const toDelete = [];
        for (let i = 0; i < 100; i++) {
            const r = it.next();
            if (r.done) break;
            toDelete.push(r.value);
        }
        for (const k of toDelete) _chatSeenHashes.delete(k);
    }

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
    const h = `phase|${msg.district || ""}`;
    if (_chatSeenHashes.has(h)) return;
    _chatSeenHashes.add(h);

    const container = document.getElementById("chat-messages");
    const div = document.createElement("div");
    div.className = "chat-msg phase-announce";
    const progress = msg.index && msg.total_districts ? ` (${msg.index}/${msg.total_districts})` : "";
    let html = `<div class="content">--- Building ${escapeHtml(msg.district)}${progress} ---<br>${escapeHtml(msg.description || "")}`;
    if (msg.scenery_summary) {
        html += `<br><em style="color:#8a8a6b;font-size:0.85em">${escapeHtml(msg.scenery_summary)}</em>`;
    }
    html += `</div>`;
    div.innerHTML = html;
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

function _readTokenRow(byUi, key) {
    const row = byUi && byUi[key] ? byUi[key] : null;
    if (!row) return { prompt: 0, completion: 0, total: 0 };
    const prompt = typeof row.prompt_tokens === "number" ? row.prompt_tokens : 0;
    const completion = typeof row.completion_tokens === "number" ? row.completion_tokens : 0;
    const total = typeof row.total_tokens === "number" ? row.total_tokens : 0;
    return { prompt, completion, total };
}

function applyTokenUsageSummary(byUi) {
    const el = document.getElementById("token-usage-summary");
    const wrap = document.getElementById("token-usage-wrap");
    if (!el || !wrap) return;
    const c = _readTokenRow(byUi, "cartographus");
    const u = _readTokenRow(byUi, "urbanista");
    const total = c.total + u.total;
    const s = formatTokShort(total);
    el.textContent = s ? `${s} tok` : "—";
    const detail = [
        `Total: ${total}`,
        `Cartographus: ${c.total} (p${c.prompt} + c${c.completion})`,
        `Urbanista: ${u.total} (p${u.prompt} + c${u.completion})`,
    ].join(" | ");
    wrap.title = detail;
}

function applyTokenUsageToHeader(byUi) {
    const data = byUi || {};
    const ids = ["cartographus", "urbanista"];
    for (const id of ids) {
        const el = document.getElementById(`agent-tokens-${id}`);
        if (!el) continue;
        const row = _readTokenRow(data, id);
        const tot = row.total;
        const s = formatTokShort(tot);
        el.textContent = s ? `${s} tok` : "";
        el.title = tot
            ? `Session: ${tot} (p${row.prompt} + c${row.completion})`
            : "Session tokens";
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
    // Clear local state + dedup
    document.getElementById("chat-messages").innerHTML = "";
    clearChatDedup();
    miniMapClear();
    currentMasterPlan = null;
    mapDescription = null;
    mapImageUrl = null;
    agentLogs.length = 0;
    totalStructures = 0;
    builtStructures = 0;
    totalTilesPlaced = 0;
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
    if (el.classList.contains("visible")) {
        setTimeout(() => { drawMiniMap(); }, 60);
    }
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

/** All placed tiles accumulated across all districts (for minimap). */
let _miniMapTiles = [];

function miniMapAddTiles(tiles) {
    for (const t of tiles) {
        _miniMapTiles.push(t);
    }
}

function miniMapClear() {
    _miniMapTiles = [];
}

function drawMiniMap() {
    const canvas = document.getElementById("mini-map");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    const s = miniMapZoom;
    const ox = miniMapOffsetX, oy = miniMapOffsetY;

    // Background
    ctx.fillStyle = "#1a1a2e";
    ctx.fillRect(0, 0, W, H);

    // Grid boundary
    const gW = worldGridWidth;
    const gH = worldGridHeight;
    ctx.strokeStyle = "#2a2a4a";
    ctx.lineWidth = 0.5;
    ctx.strokeRect(ox, oy, gW * s, gH * s);

    const typeColors = {
        temple: "#ffd700", basilica: "#deb887", road: "#808080", forum: "#d4c67a",
        insula: "#cd853f", domus: "#d2691e", market: "#deb887", water: "#3498db",
        garden: "#27ae60", wall: "#5d4037", gate: "#8b7355", monument: "#e8e8e8",
        aqueduct: "#87ceeb", thermae: "#b0e0e6", circus: "#f4a460", amphitheater: "#daa520",
        bridge: "#a0a0a0", taberna: "#b8860b", warehouse: "#8b8378", grass: "#5a9a4a",
        building: "#d4a373",
    };

    // Draw ALL placed tiles from the world (accumulated across all districts)
    for (const t of _miniMapTiles) {
        const terrain = t.terrain || t.building_type || "building";
        ctx.fillStyle = t.color || typeColors[terrain] || "#d4a373";
        const px = t.x * s + ox;
        const py = t.y * s + oy;
        if (px + s < 0 || py + s < 0 || px > W || py > H) continue; // Off-screen cull
        ctx.fillRect(px, py, Math.max(1, s - 0.3), Math.max(1, s - 0.3));
    }

    // Also draw current master plan (survey preview — not yet built)
    if (currentMasterPlan) {
        for (const item of currentMasterPlan) {
            const color = typeColors[item.building_type] || "#d4a373";
            ctx.fillStyle = color;
            ctx.globalAlpha = 0.35; // Dimmer for planned-but-not-built
            for (const t of (item.tiles || [])) {
                ctx.fillRect(t.x * s + ox, t.y * s + oy, Math.max(1, s - 0.3), Math.max(1, s - 0.3));
            }
            ctx.globalAlpha = 1.0;
        }
    }

    // Labels for placed buildings (names from tile data)
    if (s >= 4) {
        const fontSize = Math.max(6, Math.min(12, s * 0.6));
        ctx.font = `${fontSize}px sans-serif`;
        ctx.fillStyle = "#ccc";
        const labeled = new Set();
        for (const t of _miniMapTiles) {
            const name = t.building_name;
            if (!name || labeled.has(name)) continue;
            labeled.add(name);
            const lx = t.x * s + ox + 2;
            const ly = t.y * s + oy + s * 0.5;
            if (lx > 0 && ly > 0 && lx < W && ly < H) {
                ctx.fillStyle = "rgba(10, 12, 24, 0.75)";
                const tw = ctx.measureText(name).width;
                ctx.fillRect(lx - 1, ly - fontSize, tw + 4, fontSize + 2);
                ctx.fillStyle = "#ddd";
                ctx.fillText(name, lx, ly);
            }
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

// escapeHtml is defined earlier (line ~555) with null/undefined guard; avoid duplicate.

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

    // Track manual camera interactions so auto-fly respects user control
    for (const evt of ["mousedown", "wheel", "touchstart"]) {
        container.addEventListener(evt, () => { lastManualCameraMs = Date.now(); }, { passive: true });
    }

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
    const FLOATING_CAMERA_MIN_WIDTH_PX = 200;

    // Make all panels draggable by their header/background
    function makeDraggable(el, handleSelector) {
        if (!el) return;
        let dragging = false, offX = 0, offY = 0;
        // Allow dragging from header OR anywhere on the panel (fallback if header is offscreen)
        const handle = handleSelector ? (el.querySelector(handleSelector) || el) : el;
        handle.style.cursor = "grab";
        // Also allow dragging from the panel itself (not just the header)
        const startDrag = (e) => {
            if (e.target.tagName === "BUTTON" || e.target.classList.contains("nav-btn") ||
                e.target.classList.contains("close-btn") || e.target.classList.contains("popup-close-btn") ||
                e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.target.tagName === "TEXTAREA") return;
            dragging = true;
            offX = e.clientX - el.getBoundingClientRect().left;
            offY = e.clientY - el.getBoundingClientRect().top;
            handle.style.cursor = "grabbing";
            e.preventDefault();
        };
        handle.addEventListener("mousedown", startDrag);
        el.addEventListener("mousedown", startDrag);
        document.addEventListener("mousemove", e => {
            if (!dragging) return;
            // Clamp to viewport bounds — prevent panels from going fully offscreen
            const maxX = window.innerWidth - 40;  // Keep at least 40px visible
            const maxY = window.innerHeight - 30;
            const x = Math.max(-el.offsetWidth + 60, Math.min(maxX, e.clientX - offX));
            const y = Math.max(0, Math.min(maxY, e.clientY - offY));
            el.style.left = x + "px";
            el.style.top = y + "px";
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

    function initFloatingCameraPanel() {
        const panel = document.getElementById("floating-camera-panel");
        const handle = document.getElementById("floating-camera-handle");
        const resizeEl = document.getElementById("floating-camera-resize");
        if (!panel || !handle) return;
        panel.style.position = "fixed";

        function saveCameraPanelLayout() {
            try {
                const r = panel.getBoundingClientRect();
                localStorage.setItem(
                    FLOATING_CAMERA_POS_KEY,
                    JSON.stringify({
                        left: r.left,
                        top: r.top,
                        width: clampFloatingCameraWidthPx(r.width),
                    })
                );
            } catch (err) {
                /* ignore */
            }
        }

        try {
            const raw = localStorage.getItem(FLOATING_CAMERA_POS_KEY);
            if (raw) {
                const p = JSON.parse(raw);
                if (Number.isFinite(p.left) && Number.isFinite(p.top)) {
                    panel.style.left = `${p.left}px`;
                    panel.style.top = `${p.top}px`;
                    panel.style.bottom = "auto";
                    panel.style.right = "auto";
                }
                if (Number.isFinite(p.width)) {
                    panel.style.width = `${clampFloatingCameraWidthPx(p.width)}px`;
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
            saveCameraPanelLayout();
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
                const nextW = clampFloatingCameraWidthPx(resizeStartWidth + (e.clientX - resizeStartX));
                panel.style.width = `${nextW}px`;
            });
            const endCameraResize = (e) => {
                if (!resizing) return;
                resizing = false;
                try {
                    resizeEl.releasePointerCapture(e.pointerId);
                } catch (err) {
                    /* ignore */
                }
                saveCameraPanelLayout();
            };
            resizeEl.addEventListener("pointerup", endCameraResize);
            resizeEl.addEventListener("pointercancel", endCameraResize);
        }

        window.addEventListener(
            "resize",
            () => {
                const w = panel.getBoundingClientRect().width;
                const clamped = clampFloatingCameraWidthPx(w);
                if (Math.abs(clamped - w) > 0.5) {
                    panel.style.width = `${clamped}px`;
                    saveCameraPanelLayout();
                }
            },
            { passive: true }
        );
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

    function clampFloatingCameraWidthPx(w) {
        const lo = FLOATING_CAMERA_MIN_WIDTH_PX;
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
    initFloatingCameraPanel();
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
            else if (action === "lift") renderer.liftCamera(+btn.dataset.dir);
            else if (action === "reset") renderer.resetCameraToMap();
        });
    });

    // Camera speed slider
    const speedSlider = document.getElementById("camera-speed-slider");
    const speedLabel = document.getElementById("camera-speed-label");
    if (speedSlider) {
        // Restore from localStorage
        const saved = localStorage.getItem("eternal_camera_speed");
        if (saved) {
            speedSlider.value = saved;
            if (renderer) renderer.cameraSpeedMultiplier = parseFloat(saved);
            if (speedLabel) speedLabel.textContent = saved + "x";
        }
        speedSlider.addEventListener("input", () => {
            const v = parseFloat(speedSlider.value);
            if (renderer) renderer.cameraSpeedMultiplier = v;
            if (speedLabel) speedLabel.textContent = v + "x";
            localStorage.setItem("eternal_camera_speed", String(v));
        });
    }

    // Time-of-day slider
    const todSlider = document.getElementById("time-of-day-slider");
    const todLabel = document.getElementById("time-of-day-label");
    if (todSlider) {
        const todNames = [
            "12am", "1am", "2am", "3am", "4am", "5am", "6am", "7am",
            "8am", "9am", "10am", "11am", "12pm", "1pm", "2pm", "3pm",
            "4pm", "5pm", "6pm", "7pm", "8pm", "9pm", "10pm", "11pm"
        ];
        todSlider.addEventListener("input", () => {
            const v = parseFloat(todSlider.value);
            if (renderer && renderer.setTimeOfDay) renderer.setTimeOfDay(v);
            const hour = Math.round(v * 24) % 24;
            if (todLabel) todLabel.textContent = todNames[hour];
        });
    }

    // Global keyboard shortcuts (ignored when typing in inputs)
    document.addEventListener("keydown", e => {
        const tag = (e.target.tagName || "").toLowerCase();
        if (tag === "input" || tag === "textarea" || tag === "select") return;
        switch (e.key) {
            case "P":
            case "p": {
                const pb = document.getElementById("pause-btn");
                if (pb) pb.click();
                break;
            }
            case "H":
            case "h": {
                const cb = document.getElementById("chat-collapse-btn");
                if (cb) cb.click();
                break;
            }
            case "L":
            case "l": {
                const lb = document.getElementById("run-log-btn");
                if (lb) lb.click();
                break;
            }
            case "Escape": {
                if (typeof dismissPausedOverlay === "function") dismissPausedOverlay();
                break;
            }
            case "0": {
                if (renderer && !renderer._failed) renderer.resetCameraToMap();
                break;
            }
        }
    });

    // Pause/Resume button
    const pauseBtn = document.getElementById("pause-btn");
    if (pauseBtn) {
        pauseBtn.addEventListener("click", () => {
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            if (isPaused) {
                ws.send(JSON.stringify({ type: "resume" }));
                updatePauseButton(false);
                dismissPausedOverlay();
            } else {
                ws.send(JSON.stringify({ type: "pause" }));
                updatePauseButton(true);
            }
        });
    }

    // Run log download button — merges server log + client WebSocket log
    const logBtn = document.getElementById("run-log-btn");
    if (logBtn) {
        logBtn.addEventListener("click", async () => {
            try {
                const resp = await fetch(apiUrl("/api/logs"));
                let serverLog = await resp.text();
                const clientLog = getWsLogText();
                if (clientLog) {
                    serverLog += "\n\n" + "=".repeat(72) + "\n";
                    serverLog += "  CLIENT WEBSOCKET LOG\n";
                    serverLog += "=".repeat(72) + "\n";
                    serverLog += clientLog + "\n";
                }
                const blob = new Blob([serverLog], { type: "text/plain" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = "eternal_cities_run.log";
                document.body.appendChild(a);
                a.click();
                a.remove();
                URL.revokeObjectURL(url);
            } catch (e) {
                console.error("Log download failed:", e);
                // Fallback: direct server download
                const a = document.createElement("a");
                a.href = apiUrl("/api/logs");
                a.download = "eternal_cities_run.log";
                document.body.appendChild(a);
                a.click();
                a.remove();
            }
        });
    }

    void initEternalCitiesSession();
    ensureWebSocketPeriodicReconnect();

    // BFCache restore (back/forward): WebSocket is dead; reconnect once without stacking timers.
    window.addEventListener("pageshow", (ev) => {
        if (!ev.persisted) return;
        clearWebSocketReconnectTimer();
        reconnectDelay = 1000;
        connect();
    });
});
