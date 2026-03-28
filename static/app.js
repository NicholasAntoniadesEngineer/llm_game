// Roma Aeterna — WebSocket client and UI controller

let ws = null;
let renderer = null;
let reconnectDelay = 1000;

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

        case "tile_update":
            renderer.updateTiles(msg.tiles);
            if (msg.period) updateTimeline(msg.period, msg.year);
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
            break;

        case "loading":
            showLoading(msg.agent, msg.message);
            break;

        case "master_plan":
            updateMasterPlan(msg.plan);
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
    }
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
    return `${year} AD`;
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
    showLoading("imperator", "Resetting world...");
}

// --- Loading overlay ---

function showLoading(agent, message) {
    const overlay = document.getElementById("loading-overlay");
    overlay.classList.add("visible");
    document.getElementById("loading-agent").textContent = (AGENT_NAMES[agent] || agent).toUpperCase();
    document.getElementById("loading-message").textContent = message || "Working...";
}

function hideLoading() {
    document.getElementById("loading-overlay").classList.remove("visible");
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

// --- Init ---

document.addEventListener("DOMContentLoaded", () => {
    const container = document.getElementById("world-container");
    renderer = new WorldRenderer(container);

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

    connect();
});
