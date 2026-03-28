// Roma Aeterna — Component-based procedural 3D renderer
// Buildings assembled from stacked architectural components (podium, colonnade, pediment, etc.)

const TILE_SIZE = 6; // world units per tile — controls overall scale of everything

class WorldRenderer {
    constructor(container) {
        this.container = container;
        this.grid = null;
        this.width = 0;
        this.height = 0;
        this.buildingGroups = new Map();
        this.raycaster = new THREE.Raycaster();
        this.mouse = new THREE.Vector2();
        this.hoveredGroup = null;

        // Scene — Mediterranean sky
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x7EC8E3);
        this.scene.fog = new THREE.Fog(0x7EC8E3, 800, 2500);

        // Camera
        this.camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.5, 2000);

        // Renderer
        this.renderer3d = new THREE.WebGLRenderer({ antialias: true });
        this.renderer3d.setSize(container.clientWidth, container.clientHeight);
        this.renderer3d.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
        this.renderer3d.shadowMap.enabled = true;
        this.renderer3d.shadowMap.type = THREE.PCFSoftShadowMap;
        this.renderer3d.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer3d.toneMappingExposure = 1.2;
        container.appendChild(this.renderer3d.domElement);

        // Mediterranean lighting
        this.scene.add(new THREE.AmbientLight(0xffeedd, 0.45));
        const sun = new THREE.DirectionalLight(0xfff8e8, 1.0);
        sun.position.set(180, 240, 120);
        sun.castShadow = true;
        sun.shadow.mapSize.set(2048, 2048);
        const sc = sun.shadow.camera;
        sc.near = 1; sc.far = 800; sc.left = -300; sc.right = 300; sc.top = 300; sc.bottom = -300;
        this.scene.add(sun);
        this.scene.add(new THREE.HemisphereLight(0x87ceeb, 0x556b2f, 0.25));

        // Camera orbit
        this.cameraAngle = Math.PI / 4;
        this.cameraPitch = 0.5;
        this.cameraDistance = 300;
        this.cameraTarget = new THREE.Vector3(120, 0, 120);
        this.isDragging = false;
        this.prevMouse = { x: 0, y: 0 };

        this._setupControls();
        this._updateCamera();
        window.addEventListener("resize", () => this._onResize());
        this._animate();
    }

    _setupControls() {
        const el = this.renderer3d.domElement;
        this.dragButton = -1;

        el.addEventListener("mousedown", e => {
            this.dragButton = e.button;
            this.isDragging = true;
            this.prevMouse = { x: e.clientX, y: e.clientY };
        });
        el.addEventListener("mousemove", e => {
            if (this.isDragging) {
                const dx = e.clientX - this.prevMouse.x;
                const dy = e.clientY - this.prevMouse.y;
                if (this.dragButton === 2 || this.dragButton === 1 || (this.dragButton === 0 && e.shiftKey)) {
                    // Right-click, middle-click, or shift+left: pan
                    const panSpeed = this.cameraDistance * 0.002;
                    const cosA = Math.cos(this.cameraAngle);
                    const sinA = Math.sin(this.cameraAngle);
                    // Pan direction matches mouse direction
                    this.cameraTarget.x -= (dx * sinA + dy * cosA) * panSpeed;
                    this.cameraTarget.z += (dx * cosA - dy * sinA) * panSpeed;
                } else {
                    // Left-click drag: orbit
                    this.cameraAngle -= dx * 0.004;
                    this.cameraPitch = Math.max(0.05, Math.min(1.4, this.cameraPitch + dy * 0.004));
                }
                this.prevMouse = { x: e.clientX, y: e.clientY };
                this._updateCamera();
            }
            this._updateHover(e);
        });
        el.addEventListener("mouseup", () => { this.isDragging = false; this.dragButton = -1; });
        el.addEventListener("wheel", e => {
            // Logarithmic zoom — feels even at any distance
            const zoomFactor = e.deltaY > 0 ? 1.08 : 0.92;
            this.cameraDistance = Math.max(5, Math.min(500, this.cameraDistance * zoomFactor));
            this._updateCamera();
            e.preventDefault();
        }, { passive: false });
        el.addEventListener("click", e => this._onClick(e));
        el.addEventListener("contextmenu", e => e.preventDefault());

        // WASD / arrow keys + QE for orbit
        this._keysDown = new Set();
        window.addEventListener("keydown", e => {
            // Don't capture if user is typing in an input
            if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
            this._keysDown.add(e.key.toLowerCase());
        });
        window.addEventListener("keyup", e => {
            this._keysDown.delete(e.key.toLowerCase());
        });
    }

    // Public methods for UI buttons
    panCamera(dirX, dirZ) {
        const speed = this.cameraDistance * 0.05;
        const cosA = Math.cos(this.cameraAngle);
        const sinA = Math.sin(this.cameraAngle);
        this.cameraTarget.x += (dirX * sinA + dirZ * cosA) * speed;
        this.cameraTarget.z += (-dirX * cosA + dirZ * sinA) * speed;
        this._updateCamera();
    }

    orbitCamera(dAngle, dPitch) {
        this.cameraAngle += dAngle;
        this.cameraPitch = Math.max(0.05, Math.min(1.4, this.cameraPitch + dPitch));
        this._updateCamera();
    }

    zoomCamera(factor) {
        this.cameraDistance = Math.max(5, Math.min(500, this.cameraDistance * factor));
        this._updateCamera();
    }

    _updateCamera() {
        const t = this.cameraTarget;
        this.camera.position.set(
            t.x + this.cameraDistance * Math.cos(this.cameraPitch) * Math.cos(this.cameraAngle),
            t.y + this.cameraDistance * Math.sin(this.cameraPitch),
            t.z + this.cameraDistance * Math.cos(this.cameraPitch) * Math.sin(this.cameraAngle)
        );
        this.camera.lookAt(t);
    }

    _onResize() {
        const w = this.container.clientWidth, h = this.container.clientHeight;
        this.camera.aspect = w / h;
        this.camera.updateProjectionMatrix();
        this.renderer3d.setSize(w, h);
    }

    // ─── Materials (cached for performance) ───
    _mat(color, roughness = 0.75) {
        const key = `${color}:${roughness}`;
        if (!this._matCache) this._matCache = new Map();
        if (!this._matCache.has(key)) {
            this._matCache.set(key, new THREE.MeshStandardMaterial({
                color: new THREE.Color(color), roughness, metalness: 0.02
            }));
        }
        return this._matCache.get(key);
    }

    // ─── Init ───
    init(worldState) {
        this.width = worldState.width;
        this.height = worldState.height;
        this.grid = worldState.grid;
        const S = TILE_SIZE;
        this.cameraTarget.set(this.width * S / 2, 0, this.height * S / 2);
        this._updateCamera();

        // Ground
        const gw = this.width * S + 10, gh = this.height * S + 10;
        const ground = new THREE.Mesh(
            new THREE.PlaneGeometry(gw, gh),
            new THREE.MeshStandardMaterial({ color: 0xC4B17C, roughness: 1.0 })
        );
        ground.rotation.x = -Math.PI / 2;
        ground.position.set(this.width * S / 2, -0.02, this.height * S / 2);
        ground.receiveShadow = true;
        this.scene.add(ground);

        // Grid
        const gridSize = Math.max(this.width, this.height) * S;
        const grid = new THREE.GridHelper(gridSize, Math.max(this.width, this.height), 0x9a8e6b, 0x9a8e6b);
        grid.position.set(this.width * S / 2, 0.005, this.height * S / 2);
        grid.material.opacity = 0.06;
        grid.material.transparent = true;
        this.scene.add(grid);

        // Existing tiles — render each once, respecting multi-tile anchors
        const renderedAnchors = new Set();
        for (let y = 0; y < this.height; y++)
            for (let x = 0; x < this.width; x++) {
                const tile = this.grid[y][x];
                if (tile.terrain === "empty") continue;
                const anchor = tile.spec && tile.spec.anchor;
                if (anchor) {
                    const ak = `${anchor.x},${anchor.y}`;
                    if (renderedAnchors.has(ak)) continue;
                    renderedAnchors.add(ak);
                    if (anchor.y < this.height && anchor.x < this.width)
                        this._buildFromSpec(this.grid[anchor.y][anchor.x], false);
                } else {
                    this._buildFromSpec(tile, false);
                }
            }
    }

    updateTiles(tiles) {
        if (!this.grid) return;
        for (const tile of tiles) {
            if (tile.x >= 0 && tile.y >= 0 && tile.x < this.width && tile.y < this.height) {
                this.grid[tile.y][tile.x] = tile;
                if (tile.terrain && tile.terrain !== "empty") {
                    const anchor = tile.spec && tile.spec.anchor;
                    if (anchor && (tile.x !== anchor.x || tile.y !== anchor.y)) {
                        // Secondary tile updated — re-render via anchor
                        if (anchor.y < this.height && anchor.x < this.width)
                            this._buildFromSpec(this.grid[anchor.y][anchor.x], true);
                    } else {
                        this._buildFromSpec(tile, true);
                    }
                }
            }
        }
    }

    // ═══════════════════════════════════════════════
    // MULTI-TILE SUPPORT
    // ═══════════════════════════════════════════════

    _getAnchorFootprint(anchor) {
        let minX = anchor.x, maxX = anchor.x, minY = anchor.y, maxY = anchor.y;
        for (let y = 0; y < this.height; y++)
            for (let x = 0; x < this.width; x++) {
                const a = this.grid[y][x].spec && this.grid[y][x].spec.anchor;
                if (a && a.x === anchor.x && a.y === anchor.y) {
                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                    if (y < minY) minY = y;
                    if (y > maxY) maxY = y;
                }
            }
        return { minX, maxX, minY, maxY };
    }

    // ═══════════════════════════════════════════════
    // COMPONENT-BASED RENDERER
    // Interprets spec.components (stacked architectural parts)
    // or generates type-specific defaults.
    // ═══════════════════════════════════════════════

    _buildFromSpec(tile, animate) {
        const spec = tile.spec || {};
        const key = spec.anchor ? `${spec.anchor.x},${spec.anchor.y}` : `${tile.x},${tile.y}`;

        // Clean up previous group
        if (this.buildingGroups.has(key)) {
            const old = this.buildingGroups.get(key);
            this.scene.remove(old);
            old.traverse(c => { if (c.geometry) c.geometry.dispose(); if (c.material) c.material.dispose(); });
        }

        // Calculate footprint (single tile or multi-tile)
        const S = TILE_SIZE;
        let tileW = 0.9, tileD = 0.9;
        let centerX = (tile.x + 0.5) * S, centerZ = (tile.y + 0.5) * S;
        if (spec.anchor && this.grid) {
            const fp = this._getAnchorFootprint(spec.anchor);
            tileW = (fp.maxX - fp.minX + 1) - 0.1;
            tileD = (fp.maxY - fp.minY + 1) - 0.1;
            centerX = (fp.minX + fp.maxX + 1) / 2 * S;
            centerZ = (fp.minY + fp.maxY + 1) / 2 * S;
        }

        const group = new THREE.Group();
        group.position.set(centerX, 0, centerZ);
        group.scale.set(S, S, S);
        group.userData = { tile };

        const components = spec.components || [];
        const terrain = tile.terrain;

        if (components.length > 0) {
            this._buildComponents(group, components, tileW, tileD);
        } else if (["road", "forum", "garden", "water", "grass"].includes(terrain)) {
            this._buildTerrain(group, tile, spec);
        } else {
            this._placeholderBlock(group, tile, tileW, tileD);
        }

        group.traverse(c => {
            if (c.isMesh) { c.castShadow = true; c.receiveShadow = true; c.userData.tile = tile; }
        });

        if (animate) {
            group.userData.animStartY = 5 * S;
            group.userData.animTargetY = 0;
            group.userData.animStart = Date.now();
            group.position.y = 5 * S;
        }

        this.scene.add(group);
        this.buildingGroups.set(key, group);
    }

    // ─── Terrain ───

    _buildTerrain(g, tile, spec) {
        const terrain = tile.terrain || tile.building_type;
        if (terrain === "road") {
            const road = new THREE.Mesh(new THREE.BoxGeometry(0.98, 0.05, 0.98), this._mat(spec.color || "#606060", 0.9));
            road.position.y = 0.025;
            g.add(road);
            const seed = (tile.x * 7 + tile.y * 13) % 5;
            for (let i = 0; i < 2 + seed % 3; i++) {
                const stone = new THREE.Mesh(
                    new THREE.BoxGeometry(0.06 + Math.random() * 0.08, 0.01, 0.06 + Math.random() * 0.08),
                    this._mat("#7a7a7a")
                );
                stone.position.set(-0.3 + Math.random() * 0.6, 0.055, -0.3 + Math.random() * 0.6);
                g.add(stone);
            }
        } else if (terrain === "forum") {
            g.add(new THREE.Mesh(new THREE.BoxGeometry(0.96, 0.03, 0.96), this._mat(spec.color || "#d4c67a")));
        } else if (terrain === "water") {
            const water = new THREE.Mesh(
                new THREE.BoxGeometry(0.98, 0.06, 0.98),
                new THREE.MeshStandardMaterial({ color: 0x2980b9, transparent: true, opacity: 0.8, roughness: 0.05 })
            );
            water.position.y = -0.03;
            water.userData.isWater = true;
            g.add(water);
        } else if (terrain === "garden" || terrain === "grass") {
            g.add(new THREE.Mesh(new THREE.BoxGeometry(0.96, 0.04, 0.96), this._mat(spec.color || "#4a8c3f")));
            const seed = tile.x * 31 + tile.y * 17;
            const numPlants = 1 + seed % 3;
            for (let i = 0; i < numPlants; i++) {
                const px = -0.25 + ((seed * (i + 1) * 7) % 50) / 100;
                const pz = -0.25 + ((seed * (i + 1) * 13) % 50) / 100;
                const treeH = 0.2 + ((seed * (i + 3)) % 30) / 100;
                const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.025, treeH, 5), this._mat("#6b4226"));
                trunk.position.set(px, 0.04 + treeH / 2, pz);
                g.add(trunk);
                const canopySize = 0.08 + ((seed * (i + 7)) % 20) / 200;
                const canopy = new THREE.Mesh(new THREE.SphereGeometry(canopySize, 6, 5), this._mat("#2d6b1e"));
                canopy.position.set(px, 0.04 + treeH + canopySize * 0.5, pz);
                g.add(canopy);
            }
        }
    }

    // ═══════════════════════════════════════════════
    // COMPONENT BUILDERS — with architectural intelligence.
    //
    // The renderer knows how building parts relate spatially:
    //   FOUNDATION  (podium)          — ground level, raises the base
    //   STRUCTURAL  (colonnade, block, walls, arcade) — main body, sits on foundation
    //   INFILL      (cella, atrium, tier) — sits INSIDE structural at same base, never on top
    //   ROOF        (pediment, dome, tiled_roof, flat_roof, vault) — always on top of structural
    //   DECORATIVE  (door, pilasters, awning, battlements) — at base level, no height effect
    //   FREESTANDING (statue, fountain) — stacks normally on whatever is below
    //
    // The AI just lists components; the renderer places them correctly.
    // ═══════════════════════════════════════════════

    _buildComponents(group, components, w, d) {
        // Architectural role of each component type
        const FOUNDATION  = new Set(["podium"]);
        const STRUCTURAL  = new Set(["colonnade", "block", "walls", "arcade"]);
        const INFILL      = new Set(["cella", "atrium", "tier"]);
        const ROOF        = new Set(["pediment", "dome", "tiled_roof", "flat_roof", "vault"]);
        const DECORATIVE  = new Set(["door", "pilasters", "awning", "battlements"]);
        // Everything else (statue, fountain) = FREESTANDING, stacks normally

        const builders = {
            podium: "_buildPodium", colonnade: "_buildColonnade",
            pediment: "_buildPediment", dome: "_buildDome",
            block: "_buildBlock", arcade: "_buildArcade",
            tiled_roof: "_buildTiledRoof", atrium: "_buildAtrium",
            statue: "_buildStatue", fountain: "_buildFountain",
            awning: "_buildAwning", battlements: "_buildBattlements",
            tier: "_buildTier", door: "_buildDoor",
            pilasters: "_buildPilasters", vault: "_buildVault",
            flat_roof: "_buildFlatRoof", cella: "_buildCella",
            walls: "_buildWalls",
        };

        // Two-pass: first collect all components, then render in correct order
        let baseLevel = 0;       // top of foundation (podium)
        let structuralTop = 0;   // top of tallest structural element

        // Pass 1: foundations set the base level
        for (const comp of components) {
            if (!FOUNDATION.has(comp.type) || !builders[comp.type]) continue;
            const topY = this._callBuilder(builders[comp.type], group, comp, baseLevel, w, d);
            baseLevel = Math.max(baseLevel, topY);
        }
        structuralTop = baseLevel;

        // Pass 2: structural elements sit on the foundation
        for (const comp of components) {
            if (!STRUCTURAL.has(comp.type) || !builders[comp.type]) continue;
            const topY = this._callBuilder(builders[comp.type], group, comp, baseLevel, w, d);
            structuralTop = Math.max(structuralTop, topY);
        }

        // Pass 3: infill sits INSIDE structural, at the foundation level
        for (const comp of components) {
            if (!INFILL.has(comp.type) || !builders[comp.type]) continue;
            this._callBuilder(builders[comp.type], group, comp, baseLevel, w, d);
        }

        // Pass 4: roofs go on top of the tallest structural element
        for (const comp of components) {
            if (!ROOF.has(comp.type) || !builders[comp.type]) continue;
            const topY = this._callBuilder(builders[comp.type], group, comp, structuralTop, w, d);
            structuralTop = Math.max(structuralTop, topY);
        }

        // Pass 5: decorative at base level
        for (const comp of components) {
            if (!DECORATIVE.has(comp.type) || !builders[comp.type]) continue;
            this._callBuilder(builders[comp.type], group, comp, baseLevel, w, d);
        }

        // Pass 6: freestanding elements stack on top of everything
        for (const comp of components) {
            if (FOUNDATION.has(comp.type) || STRUCTURAL.has(comp.type) ||
                INFILL.has(comp.type) || ROOF.has(comp.type) ||
                DECORATIVE.has(comp.type) || !builders[comp.type]) continue;
            const topY = this._callBuilder(builders[comp.type], group, comp, structuralTop, w, d);
            structuralTop = Math.max(structuralTop, topY);
        }
    }

    _callBuilder(method, group, comp, baseY, w, d) {
        if (comp.type === "statue" || comp.type === "fountain" || comp.type === "door") {
            return this[method](group, comp, baseY);
        }
        return this[method](group, comp, baseY, w, d);
    }

    // Stepped platform
    _buildPodium(group, comp, baseY, w, d) {
        const steps = comp.steps || 3;
        const totalH = comp.height || steps * 0.06;
        const stepH = totalH / steps;
        const color = comp.color || "#c8b88a";

        for (let i = 0; i < steps; i++) {
            const shrink = (i / steps) * 0.08;
            const step = new THREE.Mesh(
                new THREE.BoxGeometry(w - shrink, stepH, d - shrink),
                this._mat(color)
            );
            step.position.y = baseY + i * stepH + stepH / 2;
            group.add(step);
        }
        return baseY + totalH;
    }

    // Columns with capitals/bases arranged by style
    _buildColonnade(group, comp, baseY, w, d) {
        const numCols = comp.columns || 6;
        const colH = comp.height || 0.7;
        const style = comp.style || "ionic";
        const color = comp.color || "#e8e0d0";
        const r = comp.radius || Math.max(0.015, w / (numCols * 5));  // scale radius to footprint
        const peripteral = comp.peripteral !== false;

        const baseH = style === "doric" ? 0 : r * 1.0;  // base proportional to column
        const capH = style === "corinthian" ? r * 2.0 : r * 1.3;
        const capW = r * (style === "corinthian" ? 3.0 : 2.5);
        const inset = r + w * 0.03;  // scale inset to footprint

        // Calculate column positions
        const positions = [];
        if (peripteral && numCols >= 4) {
            const frontN = Math.max(2, Math.round(numCols * w / (2 * (w + d))));
            const sideN = Math.max(2, Math.round(numCols * d / (2 * (w + d))));
            const fx = (i, n) => -w / 2 + inset + (w - inset * 2) / Math.max(n - 1, 1) * i;
            const fz = (i, n) => -d / 2 + inset + (d - inset * 2) / Math.max(n - 1, 1) * i;
            for (let i = 0; i < frontN; i++) { positions.push({ x: fx(i, frontN), z: -d / 2 + inset }); }
            for (let i = 0; i < frontN; i++) { positions.push({ x: fx(i, frontN), z: d / 2 - inset }); }
            for (let i = 1; i < sideN - 1; i++) { positions.push({ x: -w / 2 + inset, z: fz(i, sideN) }); }
            for (let i = 1; i < sideN - 1; i++) { positions.push({ x: w / 2 - inset, z: fz(i, sideN) }); }
        } else {
            // Prostyle: front row only
            const spacing = (w - inset * 2) / Math.max(numCols - 1, 1);
            for (let i = 0; i < numCols; i++) {
                positions.push({ x: -w / 2 + inset + spacing * i, z: -d / 2 + inset });
            }
        }

        for (const pos of positions) {
            // Shaft — slight taper (entasis): top = 5/6 of bottom per Vitruvius
            const shaft = new THREE.Mesh(
                new THREE.CylinderGeometry(r * 0.83, r, colH, Math.max(8, Math.round(r * 200))),
                this._mat(color, 0.3)
            );
            shaft.position.set(pos.x, baseY + baseH + colH / 2, pos.z);
            group.add(shaft);

            // Base (not for doric)
            if (baseH > 0) {
                const base = new THREE.Mesh(
                    new THREE.CylinderGeometry(r + 0.01, r + 0.015, baseH, 8),
                    this._mat(color, 0.35)
                );
                base.position.set(pos.x, baseY + baseH / 2, pos.z);
                group.add(base);
            }

            // Capital
            if (style === "corinthian") {
                // Ornate: stacked boxes
                const c1 = new THREE.Mesh(new THREE.BoxGeometry(capW * 0.7, capH * 0.6, capW * 0.7), this._mat(color, 0.3));
                c1.position.set(pos.x, baseY + baseH + colH + capH * 0.3, pos.z);
                group.add(c1);
                const c2 = new THREE.Mesh(new THREE.BoxGeometry(capW, capH * 0.4, capW), this._mat(color, 0.3));
                c2.position.set(pos.x, baseY + baseH + colH + capH * 0.8, pos.z);
                group.add(c2);
            } else {
                const cap = new THREE.Mesh(new THREE.BoxGeometry(capW, capH, capW), this._mat(color, 0.3));
                cap.position.set(pos.x, baseY + baseH + colH + capH / 2, pos.z);
                group.add(cap);
            }
        }

        // Entablature
        const entH = 0.05;
        const ent = new THREE.Mesh(
            new THREE.BoxGeometry(w + 0.02, entH, d + 0.02),
            this._mat(color, 0.35)
        );
        ent.position.y = baseY + baseH + colH + capH + entH / 2;
        group.add(ent);

        return baseY + baseH + colH + capH + entH;
    }

    // Triangular gabled roof
    _buildPediment(group, comp, baseY, w, d) {
        // Vitruvian pediment: rise = 1/5 of width (~11 degrees)
        const peakH = comp.height || Math.min(w * 0.2, 0.2);
        const color = comp.color || "#d4a373";
        const hw = w / 2, hd = d / 2;

        // Gabled roof: ridge runs along Z, slopes on left/right, gables on front/back
        const verts = new Float32Array([
            // Left slope (two triangles)
            -hw, baseY, -hd,   -hw, baseY, hd,   0, baseY + peakH, hd,
            -hw, baseY, -hd,   0, baseY + peakH, hd,   0, baseY + peakH, -hd,
            // Right slope (two triangles)
            hw, baseY, hd,   hw, baseY, -hd,   0, baseY + peakH, -hd,
            hw, baseY, hd,   0, baseY + peakH, -hd,   0, baseY + peakH, hd,
            // Front gable
            -hw, baseY, -hd,   0, baseY + peakH, -hd,   hw, baseY, -hd,
            // Back gable
            hw, baseY, hd,   0, baseY + peakH, hd,   -hw, baseY, hd,
        ]);
        const geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(verts, 3));
        geo.computeVertexNormals();
        group.add(new THREE.Mesh(geo, this._mat(color)));

        // Ridge beam
        const ridge = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.02, d + 0.02), this._mat(color));
        ridge.position.set(0, baseY + peakH, 0);
        group.add(ridge);

        return baseY + peakH;
    }

    // Hemisphere dome
    _buildDome(group, comp, baseY, w, d) {
        const r = comp.radius || Math.min(w, d) * 0.4;
        const color = comp.color || "#8b7355";

        const dome = new THREE.Mesh(
            new THREE.SphereGeometry(r, 16, 10, 0, Math.PI * 2, 0, Math.PI / 2),
            this._mat(color, 0.4)
        );
        dome.position.y = baseY;
        group.add(dome);

        // Oculus ring at top
        const oculus = new THREE.Mesh(
            new THREE.TorusGeometry(r * 0.12, 0.01, 6, 12),
            this._mat("#e8e0d0", 0.3)
        );
        oculus.rotation.x = -Math.PI / 2;
        oculus.position.y = baseY + r - 0.01;
        group.add(oculus);

        return baseY + r;
    }

    // Multi-story block with windows
    _buildBlock(group, comp, baseY, w, d) {
        const stories = comp.stories || 2;
        const storyH = comp.storyHeight || 0.3;
        const color = comp.color || "#d4a373";
        const windowColor = comp.windowColor || "#1a1008";
        const totalH = stories * storyH;

        for (let s = 0; s < stories; s++) {
            const shrink = s * 0.015;
            const sw = w - shrink, sd = d - shrink;
            const wall = new THREE.Mesh(
                new THREE.BoxGeometry(sw, storyH - 0.01, sd),
                this._mat(color)
            );
            wall.position.y = baseY + s * storyH + storyH / 2;
            group.add(wall);

            // Windows on front and back — scale to footprint
            const winW = Math.max(0.03, sw * 0.04);
            const winH = storyH * 0.35;
            const numWin = comp.windows || Math.max(1, Math.floor(sw / (winW * 3.5)));
            const winSpacing = sw / (numWin + 1);

            for (let wi = 0; wi < numWin; wi++) {
                const wx = -sw / 2 + winSpacing * (wi + 1);
                const wy = baseY + s * storyH + storyH * 0.55;

                // Front windows
                const wf = new THREE.Mesh(new THREE.BoxGeometry(winW, winH, 0.02), this._mat(windowColor));
                wf.position.set(wx, wy, -sd / 2 - 0.005);
                group.add(wf);

                // Back windows
                const wb = new THREE.Mesh(new THREE.BoxGeometry(winW, winH, 0.02), this._mat(windowColor));
                wb.position.set(wx, wy, sd / 2 + 0.005);
                group.add(wb);
            }

            // Floor line between stories
            if (s > 0) {
                const ledge = new THREE.Mesh(
                    new THREE.BoxGeometry(sw + 0.02, 0.015, sd + 0.02),
                    this._mat(color)
                );
                ledge.position.y = baseY + s * storyH;
                group.add(ledge);
            }
        }
        return baseY + totalH;
    }

    // Arched openings with pillars
    _buildArcade(group, comp, baseY, w, d) {
        const numArches = comp.arches || 3;
        const totalH = comp.height || 0.6;
        const color = comp.color || "#c8b88a";
        const archSpacing = w / numArches;
        const pillarW = Math.max(0.04, archSpacing * 0.2);  // pier = 1/5 of arch span (Vitruvius: 1/4)
        const archR = Math.min((archSpacing - pillarW) / 2 * 0.85, totalH * 0.45);
        const pillarH = Math.max(0.1, totalH - archR);

        // Pillars between arches
        for (let i = 0; i <= numArches; i++) {
            const px = -w / 2 + i * archSpacing;
            const pillar = new THREE.Mesh(
                new THREE.BoxGeometry(pillarW, pillarH, d),
                this._mat(color)
            );
            pillar.position.set(px, baseY + pillarH / 2, 0);
            group.add(pillar);
        }

        // Arch semicircles on front and back faces
        for (let i = 0; i < numArches; i++) {
            const cx = -w / 2 + (i + 0.5) * archSpacing;
            for (const z of [-d / 2, d / 2]) {
                const arch = new THREE.Mesh(
                    new THREE.TorusGeometry(archR, 0.02, 6, 12, Math.PI),
                    this._mat(color)
                );
                arch.rotation.x = -Math.PI / 2;
                arch.position.set(cx, baseY + pillarH, z);
                group.add(arch);
            }
        }

        // Top beam
        const beamH = 0.04;
        const beam = new THREE.Mesh(
            new THREE.BoxGeometry(w + 0.02, beamH, d + 0.02),
            this._mat(color)
        );
        beam.position.y = baseY + pillarH + archR + beamH / 2;
        group.add(beam);

        return baseY + pillarH + archR + beamH;
    }

    // Angled tile roof
    _buildTiledRoof(group, comp, baseY, w, d) {
        const color = comp.color || "#b5651d";
        const peakH = comp.height || w * 0.2;

        for (const side of [-1, 1]) {
            const slope = new THREE.Mesh(
                new THREE.BoxGeometry(w + 0.02, 0.03, d * 0.55),
                this._mat(color)
            );
            slope.position.set(0, baseY + peakH * 0.5, side * d * 0.2);
            slope.rotation.x = side * 0.25;
            group.add(slope);
        }
        return baseY + peakH;
    }

    // Courtyard with impluvium pool
    _buildAtrium(group, comp, baseY, w, d) {
        const wallH = comp.height || 0.3;
        const t = comp.thickness || 0.06;
        const color = comp.color || "#d4a373";

        // Perimeter walls with gap in front
        const gapW = w * 0.3;
        const halfW = (w - gapW) / 2;

        const lf = new THREE.Mesh(new THREE.BoxGeometry(halfW, wallH, t), this._mat(color));
        lf.position.set(-w / 2 + halfW / 2, baseY + wallH / 2, -d / 2 + t / 2);
        group.add(lf);

        const rf = new THREE.Mesh(new THREE.BoxGeometry(halfW, wallH, t), this._mat(color));
        rf.position.set(w / 2 - halfW / 2, baseY + wallH / 2, -d / 2 + t / 2);
        group.add(rf);

        const bw = new THREE.Mesh(new THREE.BoxGeometry(w, wallH, t), this._mat(color));
        bw.position.set(0, baseY + wallH / 2, d / 2 - t / 2);
        group.add(bw);

        const lw = new THREE.Mesh(new THREE.BoxGeometry(t, wallH, d), this._mat(color));
        lw.position.set(-w / 2 + t / 2, baseY + wallH / 2, 0);
        group.add(lw);

        const rw = new THREE.Mesh(new THREE.BoxGeometry(t, wallH, d), this._mat(color));
        rw.position.set(w / 2 - t / 2, baseY + wallH / 2, 0);
        group.add(rw);

        // Impluvium pool in center
        const poolW = w * 0.3, poolD = d * 0.3;
        const pool = new THREE.Mesh(
            new THREE.BoxGeometry(poolW, 0.03, poolD),
            new THREE.MeshStandardMaterial({ color: 0x2980b9, transparent: true, opacity: 0.7, roughness: 0.05 })
        );
        pool.position.y = baseY + 0.015;
        pool.userData.isWater = true;
        group.add(pool);

        return baseY + wallH;
    }

    // Figure on pedestal
    _buildStatue(group, comp, baseY) {
        const totalH = comp.height || 0.5;
        const color = comp.color || "#c0b090";
        const pedColor = comp.pedestalColor || "#8a7e6e";
        const pedH = totalH * 0.3;
        const figH = totalH * 0.5;
        const headR = totalH * 0.08;

        const ped = new THREE.Mesh(new THREE.BoxGeometry(0.12, pedH, 0.12), this._mat(pedColor));
        ped.position.y = baseY + pedH / 2;
        group.add(ped);

        const body = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.05, figH, 8), this._mat(color, 0.5));
        body.position.y = baseY + pedH + figH / 2;
        group.add(body);

        const head = new THREE.Mesh(new THREE.SphereGeometry(headR, 8, 6), this._mat(color, 0.5));
        head.position.y = baseY + pedH + figH + headR;
        group.add(head);

        return baseY + totalH;
    }

    // Circular basin with water
    _buildFountain(group, comp, baseY) {
        const r = comp.radius || 0.15;
        const h = comp.height || 0.25;
        const color = comp.color || "#a0968a";

        // Basin
        const basin = new THREE.Mesh(
            new THREE.CylinderGeometry(r, r - 0.02, h * 0.35, 12),
            this._mat(color)
        );
        basin.position.y = baseY + h * 0.175;
        group.add(basin);

        // Water surface
        const water = new THREE.Mesh(
            new THREE.CylinderGeometry(r - 0.02, r - 0.02, 0.02, 12),
            new THREE.MeshStandardMaterial({ color: 0x2980b9, transparent: true, opacity: 0.7, roughness: 0.05 })
        );
        water.position.y = baseY + h * 0.33;
        water.userData.isWater = true;
        group.add(water);

        // Central spout column
        const col = new THREE.Mesh(
            new THREE.CylinderGeometry(0.015, 0.02, h * 0.7, 8),
            this._mat(color)
        );
        col.position.y = baseY + h * 0.35 + h * 0.35;
        group.add(col);

        return baseY + h;
    }

    // Shade canopy (decorative — does not advance Y)
    _buildAwning(group, comp, baseY, w, d) {
        const color = comp.color || "#cc3333";
        const awning = new THREE.Mesh(
            new THREE.BoxGeometry(w * 0.85, 0.02, d * 0.4),
            this._mat(color)
        );
        awning.position.set(0, baseY - 0.05, -d / 2 - d * 0.15);
        awning.rotation.x = 0.15;
        group.add(awning);
        return baseY;
    }

    // Crenellated wall top
    _buildBattlements(group, comp, baseY, w, d) {
        const color = comp.color || "#c8b88a";
        const merlonH = comp.height || 0.1;
        const merlonW = 0.06;
        const numMerlons = Math.max(2, Math.floor(w / (merlonW * 2)));
        const spacing = w / numMerlons;

        for (let i = 0; i < numMerlons; i++) {
            if (i % 2 === 0) {
                for (const z of [-d / 2, d / 2]) {
                    const m = new THREE.Mesh(new THREE.BoxGeometry(merlonW, merlonH, 0.04), this._mat(color));
                    m.position.set(-w / 2 + spacing * (i + 0.5), baseY + merlonH / 2, z);
                    group.add(m);
                }
            }
        }
        return baseY + merlonH;
    }

    // Amphitheater seating ring
    _buildTier(group, comp, baseY, w, d) {
        const h = comp.height || 0.15;
        const color = comp.color || "#c4a860";
        const r = Math.min(w, d) * 0.42;

        const tier = new THREE.Mesh(
            new THREE.TorusGeometry(r, h / 2, 6, 24),
            this._mat(color)
        );
        tier.rotation.x = Math.PI / 2;
        tier.position.y = baseY + h / 2;
        group.add(tier);

        return baseY + h;
    }

    // Entrance (decorative — does not advance Y)
    _buildDoor(group, comp, baseY) {
        const doorW = comp.width || 0.1;
        const doorH = comp.height || 0.2;
        const color = comp.color || "#3a2510";

        const door = new THREE.Mesh(
            new THREE.BoxGeometry(doorW, doorH, 0.02),
            this._mat(color)
        );
        door.position.set(comp.x || 0, baseY + doorH / 2, comp.z || 0);
        group.add(door);

        // Arch above door
        const archR = doorW * 0.6;
        const arch = new THREE.Mesh(
            new THREE.TorusGeometry(archR, 0.01, 6, 8, Math.PI),
            this._mat(comp.frameColor || "#8a7e6e")
        );
        arch.rotation.x = -Math.PI / 2;
        arch.position.set(comp.x || 0, baseY + doorH, comp.z || 0);
        group.add(arch);

        return baseY;
    }

    // Flat pilasters on walls (decorative — does not advance Y)
    _buildPilasters(group, comp, baseY, w, d) {
        const count = comp.count || 4;
        const h = comp.height || 0.5;
        const color = comp.color || "#e0d8c8";
        const spacing = d / (count + 1);

        for (const side of [-1, 1]) {
            for (let i = 0; i < count; i++) {
                const pil = new THREE.Mesh(
                    new THREE.BoxGeometry(0.04, h, 0.05),
                    this._mat(color, 0.4)
                );
                pil.position.set(side * (w / 2 + 0.01), baseY + h / 2, -d / 2 + spacing * (i + 1));
                group.add(pil);
            }
        }
        return baseY;
    }

    // Barrel vault ceiling
    _buildVault(group, comp, baseY, w, d) {
        const vaultH = comp.height || w * 0.35;
        const color = comp.color || "#c8b88a";
        const segs = 12;

        // Half-cylinder built from triangle strips
        const positions = [];
        for (let i = 0; i < segs; i++) {
            const a0 = (i / segs) * Math.PI;
            const a1 = ((i + 1) / segs) * Math.PI;
            const x0 = Math.cos(a0) * w / 2, y0 = Math.sin(a0) * vaultH;
            const x1 = Math.cos(a1) * w / 2, y1 = Math.sin(a1) * vaultH;

            positions.push(
                x0, baseY + y0, -d / 2,   x1, baseY + y1, -d / 2,   x0, baseY + y0, d / 2,
                x1, baseY + y1, -d / 2,   x1, baseY + y1, d / 2,    x0, baseY + y0, d / 2
            );
        }
        const geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(positions), 3));
        geo.computeVertexNormals();
        group.add(new THREE.Mesh(geo, this._mat(color)));

        return baseY + vaultH;
    }

    // Flat slab roof
    _buildFlatRoof(group, comp, baseY, w, d) {
        const color = comp.color || "#c8b88a";
        const overhang = comp.overhang || 0.04;
        const thickness = 0.04;

        const roof = new THREE.Mesh(
            new THREE.BoxGeometry(w + overhang, thickness, d + overhang),
            this._mat(color)
        );
        roof.position.y = baseY + thickness / 2;
        group.add(roof);

        return baseY + thickness;
    }

    // Temple inner chamber
    _buildCella(group, comp, baseY, w, d) {
        const h = comp.height || 0.6;
        const cellaW = comp.width || w * 0.6;
        const cellaD = comp.depth || d * 0.7;
        const color = comp.color || "#e8e0d0";

        const cella = new THREE.Mesh(
            new THREE.BoxGeometry(cellaW, h, cellaD),
            this._mat(color)
        );
        cella.position.set(0, baseY + h / 2, 0);
        group.add(cella);

        return baseY + h;
    }

    // Perimeter walls
    _buildWalls(group, comp, baseY, w, d) {
        const h = comp.height || 0.5;
        const t = comp.thickness || 0.06;
        const color = comp.color || "#d4a373";

        const front = new THREE.Mesh(new THREE.BoxGeometry(w, h, t), this._mat(color));
        front.position.set(0, baseY + h / 2, -d / 2 + t / 2);
        group.add(front);

        const back = new THREE.Mesh(new THREE.BoxGeometry(w, h, t), this._mat(color));
        back.position.set(0, baseY + h / 2, d / 2 - t / 2);
        group.add(back);

        const left = new THREE.Mesh(new THREE.BoxGeometry(t, h, d), this._mat(color));
        left.position.set(-w / 2 + t / 2, baseY + h / 2, 0);
        group.add(left);

        const right = new THREE.Mesh(new THREE.BoxGeometry(t, h, d), this._mat(color));
        right.position.set(w / 2 - t / 2, baseY + h / 2, 0);
        group.add(right);

        return baseY + h;
    }

    // Minimal placeholder for tiles that arrive without an AI-generated spec.
    // Every real building should have its spec generated by the URBANISTA agent.
    _placeholderBlock(group, tile, w, d) {
        const h = 0.3;
        const color = tile.color || "#d4a373";
        const body = new THREE.Mesh(new THREE.BoxGeometry(w * 0.8, h, d * 0.8), this._mat(color, 0.9));
        body.position.y = h / 2;
        body.material.transparent = true;
        body.material.opacity = 0.5;
        group.add(body);
    }

    // ─── Hover / Click ───

    _updateHover(e) {
        const rect = this.renderer3d.domElement.getBoundingClientRect();
        this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        this.raycaster.setFromCamera(this.mouse, this.camera);
        const meshes = [];
        this.buildingGroups.forEach(g => g.traverse(c => { if (c.isMesh) meshes.push(c); }));
        const hits = this.raycaster.intersectObjects(meshes);

        if (this.hoveredGroup) {
            this.hoveredGroup.traverse(c => {
                if (c.isMesh && c.userData._origE !== undefined) c.material.emissive.setHex(c.userData._origE);
            });
            this.hoveredGroup = null;
        }

        const tooltip = document.getElementById("tooltip");
        if (hits.length > 0) {
            const tile = hits[0].object.userData.tile;
            if (tile && tile.terrain !== "empty") {
                // Check for multi-tile anchor first
                const anchor = tile.spec && tile.spec.anchor;
                const key = anchor ? `${anchor.x},${anchor.y}` : `${tile.x},${tile.y}`;
                const group = this.buildingGroups.get(key);
                if (group) {
                    this.hoveredGroup = group;
                    group.traverse(c => {
                        if (c.isMesh && c.material.emissive) {
                            c.userData._origE = c.material.emissive.getHex();
                            c.material.emissive.setHex(0x222222);
                        }
                    });
                }
                if (tooltip) {
                    tooltip.textContent = tile.building_name || tile.terrain;
                    tooltip.style.display = "block";
                    tooltip.style.left = (e.clientX + 12) + "px";
                    tooltip.style.top = (e.clientY + 12) + "px";
                }
                return;
            }
        }
        if (tooltip) tooltip.style.display = "none";
    }

    _onClick(e) {
        const rect = this.renderer3d.domElement.getBoundingClientRect();
        this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        this.raycaster.setFromCamera(this.mouse, this.camera);
        const meshes = [];
        this.buildingGroups.forEach(g => g.traverse(c => { if (c.isMesh) meshes.push(c); }));
        const hits = this.raycaster.intersectObjects(meshes);
        if (hits.length > 0) {
            const tile = hits[0].object.userData.tile;
            if (tile) this.renderer3d.domElement.dispatchEvent(new CustomEvent("tileclick", { detail: { x: tile.x, y: tile.y, tile } }));
        }
    }

    _animate() {
        requestAnimationFrame(() => this._animate());
        const now = Date.now();

        // Keyboard controls: WASD/arrows pan, QE orbit, RF zoom
        if (this._keysDown && this._keysDown.size > 0) {
            const panSpeed = this.cameraDistance * 0.008;
            const cosA = Math.cos(this.cameraAngle);
            const sinA = Math.sin(this.cameraAngle);
            let fx = 0, fz = 0;
            if (this._keysDown.has("w") || this._keysDown.has("arrowup"))    { fx += cosA; fz += sinA; }
            if (this._keysDown.has("s") || this._keysDown.has("arrowdown"))  { fx -= cosA; fz -= sinA; }
            if (this._keysDown.has("a") || this._keysDown.has("arrowleft"))  { fx += sinA; fz -= cosA; }
            if (this._keysDown.has("d") || this._keysDown.has("arrowright")) { fx -= sinA; fz += cosA; }
            if (fx || fz) { this.cameraTarget.x += fx * panSpeed; this.cameraTarget.z += fz * panSpeed; }
            // Q/E rotate orbit
            if (this._keysDown.has("q")) this.cameraAngle += 0.02;
            if (this._keysDown.has("e")) this.cameraAngle -= 0.02;
            // R/F zoom
            if (this._keysDown.has("r")) this.cameraDistance = Math.max(5, this.cameraDistance * 0.97);
            if (this._keysDown.has("f")) this.cameraDistance = Math.min(500, this.cameraDistance * 1.03);
            this._updateCamera();
        }
        this.buildingGroups.forEach(group => {
            if (group.userData.animStart) {
                const t = Math.min(1, (now - group.userData.animStart) / 600);
                const ease = 1 - Math.pow(1 - t, 3);
                group.position.y = group.userData.animStartY + (group.userData.animTargetY - group.userData.animStartY) * ease;
                if (t >= 1) delete group.userData.animStart;
            }
            group.traverse(c => {
                if (c.userData && c.userData.isWater) {
                    c.position.y = -0.03 + Math.sin(now * 0.002 + group.position.x * 2 + group.position.z * 3) * 0.012;
                }
            });
        });
        this.renderer3d.render(this.scene, this.camera);
    }
}
