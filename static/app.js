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
            break;

        case "timeline":
            updateTimeline(msg.period, msg.year);
            break;

        case "tile_detail":
            showTileDetail(msg.tile);
            break;

        case "complete":
            appendSystemMessage("Roma Aeterna is complete. Glory to the Empire!");
            break;
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

// --- Utility ---

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

// --- Init ---

document.addEventListener("DOMContentLoaded", () => {
    const canvas = document.getElementById("world-canvas");
    renderer = new WorldRenderer(canvas);

    // Tile click -> request detail from server
    canvas.addEventListener("tileclick", (e) => {
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
