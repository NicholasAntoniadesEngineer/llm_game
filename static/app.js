// Roma Aeterna — WebSocket client and UI controller

let ws = null;
let renderer = null;
let reconnectDelay = 1000;
let totalStructures = 0;
let builtStructures = 0;

function connect() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        console.log("Connected to Roma Aeterna");
        setConnectionStatus(true);
        reconnectDelay = 1000;
    };

    ws.onclose = () => {
        console.log("Disconnected, reconnecting...");
        setConnectionStatus(false);
        setTimeout(connect, reconnectDelay);
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
            renderer.init(msg);
            updateTimeline(msg.period, msg.year);
            break;

        case "scenario":
            document.title = `${msg.city} — Eternal Cities`;
            const sub = document.getElementById("subtitle");
            if (sub) sub.textContent = `${msg.city}, ${msg.period} — AI agents reconstruct this city in real time`;
            // Hide selection overlay (reconnecting to active session)
            document.getElementById("select-overlay").classList.add("hidden");
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
            break;
    }
}

const PAUSED_TITLES = {
    rate_limit: "Rate limit",
    api_error: "API error",
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
        // Start timer
        agentTimers[agent] = { start: Date.now(), interval: null };
        if (timerEl) timerEl.textContent = "0s";
        agentTimers[agent].interval = setInterval(() => {
            const elapsed = Math.floor((Date.now() - agentTimers[agent].start) / 1000);
            if (timerEl) timerEl.textContent = elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m${elapsed % 60}s`;
        }, 1000);
    } else {
        // Stop timer
        if (agentTimers[agent]) {
            clearInterval(agentTimers[agent].interval);
            delete agentTimers[agent];
        }
        if (timerEl) timerEl.textContent = "";
    }
}

function resetAllAgentStatus() {
    for (const agent of ["imperator", "cartographus", "urbanista", "historicus", "faber", "civis"]) {
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
    if (periodEl) periodEl.textContent = period || "";
    if (yearEl) yearEl.textContent = year ? formatYear(year) : "";
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

    let html = `<button class="close-btn" onclick="closeTileDetail()">&times;</button>`;
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
    if (connected) {
        el.className = "connection-status connected";
        el.textContent = "Connected";
    } else {
        el.className = "connection-status disconnected";
        el.textContent = "Reconnecting...";
    }
}

// --- Reset ---

function resetWorld() {
    if (!confirm("Reset the world and rebuild from scratch?")) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "reset" }));
    }
    // Clear local state
    document.getElementById("chat-messages").innerHTML = "";
    currentMasterPlan = null;
    mapDescription = null;
    mapImageUrl = null;
    agentLogs.length = 0;
    totalStructures = 0;
    builtStructures = 0;

    // Show selection screen again
    selectedCity = null;
    selectedYear = null;
    document.querySelectorAll(".city-card").forEach(c => c.classList.remove("selected"));
    document.getElementById("year-control").classList.remove("active");
    document.getElementById("start-btn").disabled = true;
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

    // Interactive grid map — zoom and pan with mouse
    if (currentMasterPlan && currentMasterPlan.length > 0) {
        html += `<canvas id="mini-map" width="500" height="500" style="border:1px solid #333;border-radius:4px;margin-bottom:12px;cursor:grab;width:100%;"></canvas>`;
        html += `<p style="color:#888;font-size:0.7rem;margin-bottom:4px;">Scroll to zoom, drag to pan</p>`;
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
    for (let i = 0; i <= 40; i++) {
        const gx = i * s + ox, gy = i * s + oy;
        if (gx >= 0 && gx <= W) { ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, H); ctx.stroke(); }
        if (gy >= 0 && gy <= H) { ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(W, gy); ctx.stroke(); }
    }

    // Grid boundary
    ctx.strokeStyle = "#444";
    ctx.lineWidth = 1;
    ctx.strokeRect(ox, oy, 40 * s, 40 * s);

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
    const res = await fetch("/api/cities");
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
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    ws.send(JSON.stringify({
        type: "start",
        city: selectedCity.name,
        year: selectedYear,
    }));

    // Hide selection overlay
    document.getElementById("select-overlay").classList.add("hidden");
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

    // Make all panels draggable by their header/background
    function makeDraggable(el, handleSelector) {
        if (!el) return;
        let dragging = false, offX = 0, offY = 0;
        const handle = handleSelector ? el.querySelector(handleSelector) : el;
        if (!handle) return;
        handle.style.cursor = "grab";
        handle.addEventListener("mousedown", e => {
            if (e.target.tagName === "BUTTON" || e.target.classList.contains("nav-btn") ||
                e.target.classList.contains("close-btn")) return;
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

    makeDraggable(document.getElementById("nav-controls"));
    makeDraggable(document.getElementById("map-overlay"), ".map-overlay-header");
    makeDraggable(document.getElementById("log-overlay"), ".map-overlay-header");
    makeDraggable(document.getElementById("tile-detail"));

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

    // Load cities for selection screen, then connect
    loadCities();
    connect();
});
