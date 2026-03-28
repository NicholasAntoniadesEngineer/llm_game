// Canvas grid renderer for the world map

class WorldRenderer {
    constructor(canvas) {
        this.canvas = canvas;
        this.ctx = canvas.getContext("2d");
        this.tileSize = 20;
        this.grid = null;
        this.width = 0;
        this.height = 0;
        this.hoveredTile = null;
        this.flashTiles = new Map(); // {key: timestamp} for flash animation
        this.animationId = null;

        // Mouse events
        canvas.addEventListener("mousemove", (e) => this.onMouseMove(e));
        canvas.addEventListener("click", (e) => this.onClick(e));
        canvas.addEventListener("mouseleave", () => {
            this.hoveredTile = null;
            this.render();
        });
    }

    init(worldState) {
        this.width = worldState.width;
        this.height = worldState.height;
        this.grid = worldState.grid;
        this.canvas.width = this.width * this.tileSize;
        this.canvas.height = this.height * this.tileSize;
        this.render();
    }

    updateTiles(tiles) {
        if (!this.grid) return;
        const now = Date.now();
        for (const tile of tiles) {
            if (tile.y < this.height && tile.x < this.width) {
                this.grid[tile.y][tile.x] = tile;
                this.flashTiles.set(`${tile.x},${tile.y}`, now);
            }
        }
        this.render();
        // Start flash animation
        if (!this.animationId) {
            this.animate();
        }
    }

    animate() {
        const now = Date.now();
        let hasFlash = false;
        for (const [key, time] of this.flashTiles) {
            if (now - time > 600) {
                this.flashTiles.delete(key);
            } else {
                hasFlash = true;
            }
        }
        if (hasFlash) {
            this.render();
            this.animationId = requestAnimationFrame(() => this.animate());
        } else {
            this.animationId = null;
        }
    }

    render() {
        if (!this.grid) return;
        const ctx = this.ctx;
        const ts = this.tileSize;

        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

        for (let y = 0; y < this.height; y++) {
            for (let x = 0; x < this.width; x++) {
                const tile = this.grid[y][x];
                const px = x * ts;
                const py = y * ts;

                // Fill tile color
                ctx.fillStyle = tile.color || TERRAIN_COLORS[tile.terrain] || "#c2b280";
                ctx.fillRect(px, py, ts, ts);

                // Grid border
                ctx.strokeStyle = "rgba(0,0,0,0.15)";
                ctx.lineWidth = 0.5;
                ctx.strokeRect(px, py, ts, ts);

                // Icon
                const icon = tile.icon ||
                    (tile.building_type && BUILDING_ICONS[tile.building_type]) ||
                    TERRAIN_ICONS[tile.terrain] || "";
                if (icon && ts >= 16) {
                    ctx.font = `${ts * 0.65}px serif`;
                    ctx.textAlign = "center";
                    ctx.textBaseline = "middle";
                    ctx.fillText(icon, px + ts / 2, py + ts / 2);
                }

                // Flash animation for newly placed tiles
                const flashKey = `${x},${y}`;
                if (this.flashTiles.has(flashKey)) {
                    const elapsed = Date.now() - this.flashTiles.get(flashKey);
                    const alpha = Math.max(0, 1 - elapsed / 600);
                    ctx.fillStyle = `rgba(255, 255, 255, ${alpha * 0.6})`;
                    ctx.fillRect(px, py, ts, ts);
                }

                // Hover highlight
                if (this.hoveredTile && this.hoveredTile.x === x && this.hoveredTile.y === y) {
                    ctx.fillStyle = "rgba(255, 255, 255, 0.3)";
                    ctx.fillRect(px, py, ts, ts);
                    ctx.strokeStyle = "#fff";
                    ctx.lineWidth = 2;
                    ctx.strokeRect(px + 1, py + 1, ts - 2, ts - 2);
                }
            }
        }
    }

    getTileAt(e) {
        const rect = this.canvas.getBoundingClientRect();
        const scaleX = this.canvas.width / rect.width;
        const scaleY = this.canvas.height / rect.height;
        const x = Math.floor((e.clientX - rect.left) * scaleX / this.tileSize);
        const y = Math.floor((e.clientY - rect.top) * scaleY / this.tileSize);
        if (x >= 0 && x < this.width && y >= 0 && y < this.height) {
            return { x, y, tile: this.grid[y][x] };
        }
        return null;
    }

    onMouseMove(e) {
        const info = this.getTileAt(e);
        if (info) {
            this.hoveredTile = { x: info.x, y: info.y };
            // Update tooltip
            const tile = info.tile;
            const tooltip = document.getElementById("tooltip");
            if (tooltip) {
                if (tile.terrain !== "empty") {
                    tooltip.textContent = tile.building_name || tile.terrain;
                    tooltip.style.display = "block";
                    tooltip.style.left = (e.clientX + 12) + "px";
                    tooltip.style.top = (e.clientY + 12) + "px";
                } else {
                    tooltip.style.display = "none";
                }
            }
        } else {
            this.hoveredTile = null;
            const tooltip = document.getElementById("tooltip");
            if (tooltip) tooltip.style.display = "none";
        }
        this.render();
    }

    onClick(e) {
        const info = this.getTileAt(e);
        if (info && info.tile.terrain !== "empty") {
            // Dispatch custom event for tile click
            this.canvas.dispatchEvent(new CustomEvent("tileclick", {
                detail: { x: info.x, y: info.y, tile: info.tile }
            }));
        }
    }
}
