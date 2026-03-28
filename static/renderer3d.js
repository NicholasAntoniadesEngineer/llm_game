// Roma Aeterna — Component-based procedural 3D renderer
// Every building is unique, assembled from AI-described specs

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
        this.scene.fog = new THREE.Fog(0x7EC8E3, 70, 140);

        // Camera
        this.camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.1, 200);

        // Renderer
        this.renderer3d = new THREE.WebGLRenderer({ antialias: true });
        this.renderer3d.setSize(container.clientWidth, container.clientHeight);
        this.renderer3d.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer3d.shadowMap.enabled = true;
        this.renderer3d.shadowMap.type = THREE.PCFSoftShadowMap;
        this.renderer3d.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer3d.toneMappingExposure = 1.2;
        container.appendChild(this.renderer3d.domElement);

        // Mediterranean lighting
        this.scene.add(new THREE.AmbientLight(0xffeedd, 0.45));
        const sun = new THREE.DirectionalLight(0xfff8e8, 1.0);
        sun.position.set(30, 40, 20);
        sun.castShadow = true;
        sun.shadow.mapSize.set(2048, 2048);
        const sc = sun.shadow.camera;
        sc.near = 1; sc.far = 120; sc.left = -50; sc.right = 50; sc.top = 50; sc.bottom = -50;
        this.scene.add(sun);
        this.scene.add(new THREE.HemisphereLight(0x87ceeb, 0x556b2f, 0.25));

        // Camera orbit
        this.cameraAngle = Math.PI / 4;
        this.cameraPitch = 0.5;
        this.cameraDistance = 55;
        this.cameraTarget = new THREE.Vector3(20, 0, 20);
        this.isDragging = false;
        this.prevMouse = { x: 0, y: 0 };

        this._setupControls();
        this._updateCamera();
        window.addEventListener("resize", () => this._onResize());
        this._animate();
    }

    _setupControls() {
        const el = this.renderer3d.domElement;
        el.addEventListener("mousedown", e => { this.isDragging = true; this.prevMouse = { x: e.clientX, y: e.clientY }; });
        el.addEventListener("mousemove", e => {
            if (this.isDragging) {
                this.cameraAngle -= (e.clientX - this.prevMouse.x) * 0.005;
                this.cameraPitch = Math.max(0.1, Math.min(1.3, this.cameraPitch + (e.clientY - this.prevMouse.y) * 0.005));
                this.prevMouse = { x: e.clientX, y: e.clientY };
                this._updateCamera();
            }
            this._updateHover(e);
        });
        el.addEventListener("mouseup", () => { this.isDragging = false; });
        el.addEventListener("wheel", e => {
            this.cameraDistance = Math.max(8, Math.min(100, this.cameraDistance + e.deltaY * 0.05));
            this._updateCamera();
            e.preventDefault();
        }, { passive: false });
        el.addEventListener("click", e => this._onClick(e));
        el.addEventListener("contextmenu", e => e.preventDefault());
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
        this.cameraTarget.set(this.width / 2, 0, this.height / 2);
        this._updateCamera();

        // Ground
        const ground = new THREE.Mesh(
            new THREE.PlaneGeometry(this.width + 10, this.height + 10),
            new THREE.MeshStandardMaterial({ color: 0xC4B17C, roughness: 1.0 })
        );
        ground.rotation.x = -Math.PI / 2;
        ground.position.set(this.width / 2, -0.02, this.height / 2);
        ground.receiveShadow = true;
        this.scene.add(ground);

        // Grid
        const grid = new THREE.GridHelper(Math.max(this.width, this.height), Math.max(this.width, this.height), 0x9a8e6b, 0x9a8e6b);
        grid.position.set(this.width / 2, 0.005, this.height / 2);
        grid.material.opacity = 0.06;
        grid.material.transparent = true;
        this.scene.add(grid);

        // Existing tiles from save
        for (let y = 0; y < this.height; y++)
            for (let x = 0; x < this.width; x++) {
                const tile = this.grid[y][x];
                if (tile.terrain !== "empty") this._buildFromSpec(tile, false);
            }
    }

    updateTiles(tiles) {
        if (!this.grid) return;
        for (const tile of tiles) {
            if (tile.x >= 0 && tile.y >= 0 && tile.x < this.width && tile.y < this.height) {
                this.grid[tile.y][tile.x] = tile;
                if (tile.terrain && tile.terrain !== "empty") {
                    this._buildFromSpec(tile, true);
                }
            }
        }
    }

    // ═══════════════════════════════════════════════
    // SHAPE-LIST RENDERER
    // AI describes each building as a list of 3D primitives.
    // We draw exactly what the AI sculpts — no templates.
    // ═══════════════════════════════════════════════

    _buildFromSpec(tile, animate) {
        const key = `${tile.x},${tile.y}`;
        if (this.buildingGroups.has(key)) {
            const old = this.buildingGroups.get(key);
            this.scene.remove(old);
            old.traverse(c => { if (c.geometry) c.geometry.dispose(); if (c.material) c.material.dispose(); });
        }

        const group = new THREE.Group();
        group.position.set(tile.x + 0.5, 0, tile.y + 0.5);
        group.userData = { tile };

        const spec = tile.spec || {};
        const shapes = spec.shapes || [];
        const terrain = tile.terrain;

        if (shapes.length > 0) {
            // AI-sculpted building — render each shape
            this._renderShapes(group, shapes);
        } else if (terrain === "road" || terrain === "forum" || terrain === "garden" ||
                   terrain === "water" || terrain === "grass") {
            this._buildTerrain(group, tile, spec);
        } else {
            // Fallback: simple box from tile color
            const h = spec.height || 1.0;
            const body = new THREE.Mesh(new THREE.BoxGeometry(0.8, h, 0.8), this._mat(tile.color || "#d4a373"));
            body.position.y = h / 2;
            group.add(body);
        }

        group.traverse(c => {
            if (c.isMesh) { c.castShadow = true; c.receiveShadow = true; c.userData.tile = tile; }
        });

        if (animate) {
            group.userData.animStartY = 5;
            group.userData.animTargetY = 0;
            group.userData.animStart = Date.now();
            group.position.y = 5;
        }

        this.scene.add(group);
        this.buildingGroups.set(key, group);
    }

    _buildTerrain(g, tile, spec) {
        const terrain = tile.terrain || tile.building_type;
        if (terrain === "road") {
            const road = new THREE.Mesh(new THREE.BoxGeometry(0.98, 0.05, 0.98), this._mat(spec.color || "#606060", 0.9));
            road.position.y = 0.025;
            g.add(road);
            // Varied cobblestone marks
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
            // Unique vegetation based on position
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
    // SHAPE-LIST RENDERER — AI sculpts each building
    // from 3D primitives (box, cylinder, cone, sphere, torus)
    // ═══════════════════════════════════════════════

    _renderShapes(group, shapes) {
        // First pass: find the lowest point to clamp to ground
        let minY = Infinity;
        for (const shape of shapes) {
            const pos = shape.pos || [0, 0, 0];
            const h = shape.height || (shape.size ? shape.size[1] : shape.radius * 2 || 0.5);
            const bottom = pos[1] - h / 2;
            if (bottom < minY) minY = bottom;
        }
        // Offset to ensure nothing is below ground
        const yOffset = minY < 0 ? -minY : 0;

        for (const shape of shapes) {
            let geo, mesh;
            const color = shape.color || "#d4a373";
            const mat = this._mat(color, shape.roughness || 0.75);
            const pos = shape.pos || [0, 0, 0];

            switch (shape.type) {
                case "box":
                    const sz = shape.size || [0.5, 0.5, 0.5];
                    geo = new THREE.BoxGeometry(sz[0], sz[1], sz[2]);
                    break;
                case "cylinder":
                    geo = new THREE.CylinderGeometry(
                        shape.radiusTop !== undefined ? shape.radiusTop : (shape.radius || 0.1),
                        shape.radiusBottom !== undefined ? shape.radiusBottom : (shape.radius || 0.1),
                        shape.height || 1.0,
                        shape.segments || 12
                    );
                    break;
                case "cone":
                    geo = new THREE.ConeGeometry(
                        shape.radius || 0.3,
                        shape.height || 0.5,
                        shape.segments || 8
                    );
                    break;
                case "sphere":
                    geo = new THREE.SphereGeometry(
                        shape.radius || 0.2,
                        shape.segments || 12,
                        shape.segments || 10
                    );
                    break;
                case "torus":
                    geo = new THREE.TorusGeometry(
                        shape.radius || 0.2,
                        shape.tube || 0.03,
                        8,
                        shape.segments || 16,
                        shape.arc || Math.PI * 2
                    );
                    break;
                default:
                    continue;
            }

            mesh = new THREE.Mesh(geo, mat);
            mesh.position.set(pos[0], pos[1] + yOffset, pos[2]);

            // Optional rotation
            if (shape.rotation) {
                mesh.rotation.set(
                    shape.rotation[0] || 0,
                    shape.rotation[1] || 0,
                    shape.rotation[2] || 0
                );
            }

            group.add(mesh);
        }
    }

    _assembleBuilding_DEAD(g, w, d, h, wallColor, roofColor, features, spec) {
        const featureSet = new Set(features.map(f => f.split(":")[0]));
        const getParam = (name) => {
            const f = features.find(f => f.startsWith(name + ":"));
            return f ? parseInt(f.split(":")[1]) || 0 : 0;
        };

        let currentY = 0;

        // 1. STEPPED BASE
        const steps = getParam("stepped_base") || 0;
        if (steps > 0) {
            for (let i = 0; i < steps; i++) {
                const sw = w + 0.1 - i * 0.04;
                const sd = d + 0.1 - i * 0.04;
                const step = new THREE.Mesh(new THREE.BoxGeometry(sw, 0.08, sd), this._mat("#c8b88a"));
                step.position.y = currentY + 0.04;
                g.add(step);
                currentY += 0.08;
            }
        }

        // 2. MAIN WALLS
        const wallH = h - currentY;
        const stories = getParam("stories") || 1;
        const storyH = wallH / stories;

        for (let s = 0; s < stories; s++) {
            const storyW = w - s * 0.03; // slight taper
            const storyD = d - s * 0.03;
            const wall = new THREE.Mesh(
                new THREE.BoxGeometry(storyW, storyH - 0.02, storyD),
                this._mat(wallColor)
            );
            wall.position.y = currentY + s * storyH + storyH / 2;
            g.add(wall);

            // WINDOWS
            const numWindows = getParam("windows") || 0;
            if (numWindows > 0) {
                const winPerSide = Math.ceil(numWindows / 2);
                for (let side = -1; side <= 1; side += 2) {
                    for (let wi = 0; wi < winPerSide; wi++) {
                        const winW = 0.05 + Math.random() * 0.03;
                        const winH = 0.08 + Math.random() * 0.04;
                        const spacing = storyD / (winPerSide + 1);
                        const win = new THREE.Mesh(
                            new THREE.BoxGeometry(0.02, winH, winW),
                            this._mat("#1a1008")
                        );
                        win.position.set(
                            side * (storyW / 2 + 0.005),
                            currentY + s * storyH + storyH * 0.55,
                            -storyD / 2 + spacing * (wi + 1)
                        );
                        g.add(win);
                    }
                }
            }

            // BALCONIES
            if (featureSet.has("balconies") && s > 0) {
                const balcony = new THREE.Mesh(
                    new THREE.BoxGeometry(storyW + 0.06, 0.025, storyD + 0.06),
                    this._mat("#a08060")
                );
                balcony.position.y = currentY + s * storyH + 0.01;
                g.add(balcony);
            }
        }

        const topY = currentY + wallH;

        // 3. COLUMNS
        const numColumns = getParam("columns") || 0;
        if (numColumns > 0) {
            const colH = wallH * 0.85;
            const colSpacing = d / (Math.ceil(numColumns / 2) + 1);
            let colIdx = 0;
            for (let side = -1; side <= 1; side += 2) {
                const cols = Math.ceil(numColumns / 2);
                for (let ci = 0; ci < cols; ci++) {
                    const radius = 0.025 + Math.random() * 0.01;
                    const col = new THREE.Mesh(
                        new THREE.CylinderGeometry(radius, radius + 0.005, colH, 8),
                        this._mat("#f0ead8", 0.3)
                    );
                    col.position.set(
                        side * (w / 2 - 0.02),
                        currentY + colH / 2,
                        -d / 2 + colSpacing * (ci + 1)
                    );
                    g.add(col);

                    // Capital
                    const capSize = radius * 2.5;
                    const cap = new THREE.Mesh(
                        new THREE.BoxGeometry(capSize, capSize * 0.4, capSize),
                        this._mat("#f0ead8", 0.3)
                    );
                    cap.position.set(col.position.x, currentY + colH + capSize * 0.2, col.position.z);
                    g.add(cap);

                    // Base
                    const base = new THREE.Mesh(
                        new THREE.CylinderGeometry(radius + 0.01, radius + 0.015, 0.04, 8),
                        this._mat("#e0d8c8")
                    );
                    base.position.set(col.position.x, currentY + 0.02, col.position.z);
                    g.add(base);
                    colIdx++;
                }
            }

            // Entablature if columns present
            const beam = new THREE.Mesh(
                new THREE.BoxGeometry(w + 0.04, 0.06, d + 0.04),
                this._mat("#e8e0d0", 0.35)
            );
            beam.position.y = topY - 0.03;
            g.add(beam);
        }

        // 4. ROOF
        if (featureSet.has("pediment")) {
            // Triangular pediment roof
            const peakH = h * 0.25;
            const verts = new Float32Array([
                -w/2, topY, -d/2,   w/2, topY, -d/2,   0, topY + peakH, 0,
                -w/2, topY,  d/2,   w/2, topY,  d/2,   0, topY + peakH, 0,
                -w/2, topY, -d/2,  -w/2, topY,  d/2,   0, topY + peakH, 0,
                 w/2, topY, -d/2,   w/2, topY,  d/2,   0, topY + peakH, 0,
            ]);
            const geo = new THREE.BufferGeometry();
            geo.setAttribute("position", new THREE.BufferAttribute(verts, 3));
            geo.computeVertexNormals();
            g.add(new THREE.Mesh(geo, this._mat(roofColor)));
        } else if (featureSet.has("dome")) {
            const domeR = Math.min(w, d) * 0.45;
            const dome = new THREE.Mesh(
                new THREE.SphereGeometry(domeR, 16, 10, 0, Math.PI * 2, 0, Math.PI / 2),
                this._mat(roofColor, 0.4)
            );
            dome.position.y = topY;
            g.add(dome);
        } else if (featureSet.has("flat_roof")) {
            const roof = new THREE.Mesh(
                new THREE.BoxGeometry(w + 0.04, 0.04, d + 0.04),
                this._mat(roofColor)
            );
            roof.position.y = topY + 0.02;
            g.add(roof);
        } else if (featureSet.has("tiled_roof")) {
            // Angled tiled roof
            for (const side of [-1, 1]) {
                const slope = new THREE.Mesh(
                    new THREE.BoxGeometry(w + 0.02, 0.04, d * 0.55),
                    this._mat(roofColor)
                );
                slope.position.set(0, topY + 0.08, side * d * 0.2);
                slope.rotation.x = side * 0.25;
                g.add(slope);
            }
        }

        // 5. ARCHES
        const numArches = getParam("arches") || 0;
        if (numArches > 0) {
            const archSpacing = w / (numArches + 1);
            for (let ai = 0; ai < numArches; ai++) {
                const archR = 0.12 + Math.random() * 0.05;
                const arch = new THREE.Mesh(
                    new THREE.TorusGeometry(archR, 0.025, 6, 10, Math.PI),
                    this._mat(wallColor)
                );
                arch.position.set(-w/2 + archSpacing * (ai + 1), h * 0.6, -d/2 - 0.01);
                arch.rotation.z = Math.PI;
                arch.rotation.y = Math.PI / 2;
                g.add(arch);
            }
        }

        // 6. DOOR
        if (featureSet.has("door") || featureSet.has("entrance")) {
            const doorW = 0.08 + Math.random() * 0.05;
            const doorH = 0.2 + Math.random() * 0.1;
            const door = new THREE.Mesh(
                new THREE.BoxGeometry(doorW, doorH, 0.02),
                this._mat("#3a2510")
            );
            door.position.set(0, currentY + doorH / 2, -d / 2 - 0.01);
            g.add(door);
        }

        // 7. BATTLEMENTS
        if (featureSet.has("battlements")) {
            const numMerlons = Math.floor(w / 0.12);
            for (let i = 0; i < numMerlons; i++) {
                if (i % 2 === 0) {
                    const merlon = new THREE.Mesh(
                        new THREE.BoxGeometry(0.06, 0.1, d * 0.3),
                        this._mat(wallColor)
                    );
                    merlon.position.set(-w/2 + 0.06 + i * 0.12, topY + 0.05, 0);
                    g.add(merlon);
                }
            }
        }

        // 8. AWNING / CANOPY
        if (featureSet.has("awning")) {
            const awning = new THREE.Mesh(
                new THREE.BoxGeometry(w * 0.9, 0.02, d * 0.4),
                this._mat(spec.awning_color || "#cc3333")
            );
            awning.position.set(0, topY * 0.85, -d / 2 - d * 0.15);
            awning.rotation.x = 0.15;
            g.add(awning);
        }

        // 9. COURTYARD / ATRIUM
        if (featureSet.has("atrium")) {
            const pool = new THREE.Mesh(
                new THREE.BoxGeometry(w * 0.3, 0.03, d * 0.3),
                new THREE.MeshStandardMaterial({ color: 0x2980b9, transparent: true, opacity: 0.7, roughness: 0.05 })
            );
            pool.position.y = topY + 0.02;
            g.add(pool);
        }

        // 10. PILASTERS
        const numPilasters = getParam("pilasters") || 0;
        if (numPilasters > 0) {
            const pilSpacing = d / (numPilasters + 1);
            for (let side = -1; side <= 1; side += 2) {
                for (let pi = 0; pi < numPilasters; pi++) {
                    const pil = new THREE.Mesh(
                        new THREE.BoxGeometry(0.04, wallH * 0.9, 0.05),
                        this._mat("#e0d8c8", 0.4)
                    );
                    pil.position.set(side * (w / 2 + 0.01), currentY + wallH * 0.45, -d/2 + pilSpacing * (pi + 1));
                    g.add(pil);
                }
            }
        }

        // 11. TIERS (amphitheater seating)
        const numTiers = getParam("tiers") || 0;
        if (numTiers > 0) {
            for (let ti = 0; ti < numTiers; ti++) {
                const tierR = Math.min(w, d) * 0.4 - ti * 0.04;
                if (tierR > 0) {
                    const tier = new THREE.Mesh(
                        new THREE.TorusGeometry(tierR, 0.02, 4, 20),
                        this._mat("#c4a860")
                    );
                    tier.rotation.x = Math.PI / 2;
                    tier.position.y = currentY + 0.1 + ti * storyH * 0.3;
                    g.add(tier);
                }
            }
        }
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
                const key = `${tile.x},${tile.y}`;
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
