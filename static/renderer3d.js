// Roma Aeterna — Component-based procedural 3D renderer
// Buildings assembled from stacked architectural components (podium, colonnade, pediment, etc.)

const TILE_SIZE = 14; // world units per tile — controls overall scale of everything

/** Terrain types the renderer paints without spec.components (must match _buildTerrain branches). */
const TERRAIN_WITH_PROCEDURAL_MESH = new Set(["road", "forum", "garden", "water", "grass"]);

/** Phase 4 — contextual polish (neighbor-aware); meshes tagged isPhase4Context for debugging. */
const PHASE4_MAX_DECOR_MESHES = 96;
const PHASE4_STEP_DEPTH = 0.045;
const PHASE4_STEP_HEIGHT = 0.018;
const PHASE4_STEP_COUNT = 3;
/** Extra world half-height margin for frustum culling when Phase 4 adds façade extrusions (tile units × TILE_SIZE). */
const PHASE4_CULL_HEIGHT_EXTRA = 0.12;

class WorldRenderer {
    static _VALID_STACK_ROLES = new Set([
        "foundation", "structural", "infill", "roof", "decorative", "freestanding",
    ]);
    static _DEFAULT_STACK_ROLE = {
        podium: "foundation",
        colonnade: "structural",
        block: "structural",
        walls: "structural",
        arcade: "structural",
        cella: "infill",
        atrium: "infill",
        tier: "infill",
        pediment: "roof",
        dome: "roof",
        tiled_roof: "roof",
        flat_roof: "roof",
        vault: "roof",
        door: "decorative",
        pilasters: "decorative",
        awning: "decorative",
        battlements: "decorative",
        statue: "freestanding",
        fountain: "freestanding",
    };
    static _BUILDER_METHODS = {
        podium: "_buildPodium",
        colonnade: "_buildColonnade",
        pediment: "_buildPediment",
        dome: "_buildDome",
        block: "_buildBlock",
        arcade: "_buildArcade",
        tiled_roof: "_buildTiledRoof",
        atrium: "_buildAtrium",
        statue: "_buildStatue",
        fountain: "_buildFountain",
        awning: "_buildAwning",
        battlements: "_buildBattlements",
        tier: "_buildTier",
        door: "_buildDoor",
        pilasters: "_buildPilasters",
        vault: "_buildVault",
        flat_roof: "_buildFlatRoof",
        cella: "_buildCella",
        walls: "_buildWalls",
    };

    constructor(container) {
        this.container = container;
        this.grid = null;
        this.width = 0;
        this.height = 0;
        this.buildingGroups = new Map();
        this.raycaster = new THREE.Raycaster();
        this.mouse = new THREE.Vector2();
        this.hoveredGroup = null;
        this._meshList = [];
        this._meshListDirty = true;
        this._waterMeshes = [];
        this._animatingGroups = new Set();
        /** @type {number[][]|null} Elevation samples at grid corners [j][i], 0..height × 0..width */
        this._cornerHeights = null;
        // Frustum culling (bounding spheres per building tile)
        this._frustum = new THREE.Frustum();
        this._projScreenMatrix = new THREE.Matrix4();
        this._cullSphere = new THREE.Sphere();
        this._cullCenter = new THREE.Vector3();
        this._instDummy = new THREE.Object3D();

        // Scene — sky / IBL set in _setupIblAndBackground() after renderer exists
        this.scene = new THREE.Scene();

        // Camera
        this.camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.5, 20000);

        // Renderer — catch WebGL failures
        try {
            this.renderer3d = new THREE.WebGLRenderer({
                antialias: true,
                powerPreference: "high-performance",
                logarithmicDepthBuffer: true,
            });
        } catch (e) {
            container.innerHTML = '<p style="color:#ffd700;padding:40px;text-align:center;">WebGL unavailable. Try closing other browser tabs or restarting your browser.</p>';
            this._failed = true;
            return;
        }
        this.renderer3d.setSize(container.clientWidth, container.clientHeight);
        this.renderer3d.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer3d.shadowMap.enabled = true;
        this.renderer3d.shadowMap.type = THREE.PCFSoftShadowMap;
        this.renderer3d.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer3d.toneMappingExposure = 0.94;
        // Recover from context loss
        this.renderer3d.domElement.addEventListener("webglcontextlost", (e) => {
            e.preventDefault();
            console.warn("WebGL context lost — will restore");
        });
        this.renderer3d.domElement.addEventListener("webglcontextrestored", () => {
            console.log("WebGL context restored");
        });
        container.appendChild(this.renderer3d.domElement);

        this._setupIblAndBackground();
        const terrainDetail = this._createTerrainDetailMaps();
        this._terrainRoughnessMap = terrainDetail.roughnessMap;
        this._terrainNormalMap = terrainDetail.normalMap;

        // Mediterranean lighting — balanced with IBL (scene.environment)
        this.scene.add(new THREE.AmbientLight(0xfff5e6, 0.36));
        const sun = new THREE.DirectionalLight(0xfff0d0, 0.92);
        sun.position.set(400, 500, 250);
        sun.castShadow = true;
        // 2048² is a good balance for many buildings; raise if GPU-bound and quality-first
        sun.shadow.mapSize.set(2048, 2048);
        sun.shadow.normalBias = 0.028;
        sun.shadow.bias = -0.00065;
        const sc = sun.shadow.camera;
        sc.near = 1;
        sc.far = 8000;
        sc.left = -1200;
        sc.right = 1200;
        sc.top = 1200;
        sc.bottom = -1200;
        this.scene.add(sun);
        this._sunLight = sun;
        this.scene.add(new THREE.HemisphereLight(0xa8d4f0, 0x7a6e52, 0.42));
        const fillLight = new THREE.DirectionalLight(0xffd4a0, 0.1);
        fillLight.position.set(-200, 50, -100);
        this.scene.add(fillLight);
        const rimLight = new THREE.DirectionalLight(0xe8e2f8, 0.13);
        rimLight.position.set(-380, 120, -420);
        rimLight.castShadow = false;
        this.scene.add(rimLight);

        // Camera orbit
        this.cameraAngle = Math.PI / 4;
        this.cameraPitch = 0.5;
        this.cameraDistance = 600;
        /** Set in init() from map diagonal; zoom uses these bounds */
        this._camDistMin = null;
        this._camDistMax = null;
        this.cameraTarget = new THREE.Vector3(280, 0, 280);
        this.isDragging = false;
        this.prevMouse = { x: 0, y: 0 };
        /** Camera speed multiplier — adjustable from UI (0.25 .. 4.0, default 1.0) */
        this.cameraSpeedMultiplier = 1.0;
        /** Triple-click tracking */
        this._clickTimes = [];

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
                    // Pan using camera's actual right/forward vectors projected onto ground
                    const panSpeed = this.cameraDistance * 0.002 * (this.cameraSpeedMultiplier || 1.0);
                    // Camera right vector (perpendicular to look direction, on XZ plane)
                    const rightX = Math.sin(this.cameraAngle);
                    const rightZ = -Math.cos(this.cameraAngle);
                    // Camera forward vector (projected onto XZ plane)
                    const fwdX = -Math.cos(this.cameraAngle);
                    const fwdZ = -Math.sin(this.cameraAngle);
                    this.cameraTarget.x += (-dx * rightX + dy * fwdX) * panSpeed;
                    this.cameraTarget.z += (-dx * rightZ + dy * fwdZ) * panSpeed;
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
            this.cameraDistance = this._clampCameraDistance(this.cameraDistance * zoomFactor);
            this._updateCamera();
            e.preventDefault();
        }, { passive: false });
        el.addEventListener("click", e => this._onClick(e));
        el.addEventListener("contextmenu", e => e.preventDefault());

        // WASD pan; arrows = orbit yaw + pitch; Q/E = yaw (orbit); +/− zoom; R/F zoom alt
        // Space = raise view target; C = lower
        this._keysDown = new Set();
        const _normKey = (e) => {
            if (e.code === "NumpadAdd" || e.key === "+") return "+";
            if (e.code === "NumpadSubtract" || e.key === "-") return "-";
            if (e.key === "=") return "=";
            if (e.code === "Space" || e.key === " ") return "space";
            return e.key.toLowerCase();
        };
        window.addEventListener("keydown", e => {
            // Don't capture if user is typing in an input
            if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
            const nk = _normKey(e);
            if (nk === "space") e.preventDefault();
            this._keysDown.add(nk);
        });
        window.addEventListener("keyup", e => {
            this._keysDown.delete(_normKey(e));
        });
    }

    // Public methods for UI buttons (same axes as keyboard WASD in _animate)
    // dirX: -1 = left, +1 = right (camera right on ground); dirZ: +1 = forward, -1 = back (along view on XZ).
    panCamera(dirX, dirZ) {
        if (this._failed) return;
        const speed = this.cameraDistance * 0.05 * (this.cameraSpeedMultiplier || 1.0);
        const rightX = Math.sin(this.cameraAngle);
        const rightZ = -Math.cos(this.cameraAngle);
        const fwdX = -Math.cos(this.cameraAngle);
        const fwdZ = -Math.sin(this.cameraAngle);
        this.cameraTarget.x += (dirX * rightX + dirZ * fwdX) * speed;
        this.cameraTarget.z += (dirX * rightZ + dirZ * fwdZ) * speed;
        this._updateCamera();
    }

    /** Orbit yaw (dAngle) + pitch (dPitch) — Rotate Q/E, tilt row, arrow keys */
    orbitCamera(dAngle, dPitch) {
        if (this._failed) return;
        this.cameraAngle += dAngle;
        this.cameraPitch = Math.max(0.05, Math.min(1.4, this.cameraPitch + dPitch));
        this._updateCamera();
    }

    zoomCamera(factor) {
        if (this._failed) return;
        this.cameraDistance = this._clampCameraDistance(this.cameraDistance * factor);
        this._updateCamera();
    }

    /** direction: +1 = raise orbit target (Space), -1 = lower (C) — step matches pan buttons */
    liftCamera(direction) {
        if (this._failed) return;
        const liftSpeed = this.cameraDistance * 0.05 * (this.cameraSpeedMultiplier || 1.0);
        this.cameraTarget.y += direction * liftSpeed;
        this._updateCamera();
    }

    /** Restore orbit target, distance, yaw, and pitch to the same framing as after world load. */
    resetCameraToMap() {
        if (this._failed) return;
        if (this.width == null || this.height == null) return;
        this._flyTarget = null;
        const S = TILE_SIZE;
        let minH = 0;
        let maxH = 0;
        if (this._cornerHeights && this._cornerHeights.length) {
            for (const row of this._cornerHeights) {
                for (const v of row) {
                    if (v < minH) minH = v;
                    if (v > maxH) maxH = v;
                }
            }
        }
        const midY = ((minH + maxH) / 2) * S;
        const mapW = this.width * S;
        const mapH = this.height * S;
        this._resetCameraPoseToMapCenter(midY, mapW, mapH);
    }

    _resetCameraPoseToMapCenter(midY, mapW, mapH) {
        const diagonal = Math.hypot(mapW, mapH);
        this.cameraAngle = Math.PI / 4;
        this.cameraPitch = 0.5;
        this.cameraDistance = this._clampCameraDistance(diagonal * 0.45);
        this.cameraTarget.set(mapW / 2, midY, mapH / 2);
        this._syncPerspectiveFarPlane();
        this._updateCamera();
    }

    _clampCameraDistance(d) {
        const lo = (this._camDistMin != null && this._camDistMin > 0) ? this._camDistMin : 1.0;
        const hi = (this._camDistMax != null && this._camDistMax > 0) ? this._camDistMax : 8000;
        return Math.max(lo, Math.min(hi, d));
    }

    _syncPerspectiveFarPlane() {
        const far = (this._camDistMax != null && this._camDistMax > 0)
            ? Math.max(12000, this._camDistMax * 2.2)
            : 20000;
        this.camera.far = far;
        this.camera.updateProjectionMatrix();
    }

    flyTo(worldX, worldZ) {
        if (this._failed) return;
        this._flyTarget = { x: worldX, z: worldZ };
        this._flyStart = Date.now();
        this._flyFrom = { x: this.cameraTarget.x, z: this.cameraTarget.z };
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

    /**
     * Procedural equirect sky + PMREM for scene.environment (IBL on all MeshStandard/Physical materials).
     */
    _setupIblAndBackground() {
        if (!this.renderer3d || this._failed) return;
        const r = this.renderer3d;
        if (THREE.sRGBEncoding !== undefined) {
            r.outputEncoding = THREE.sRGBEncoding;
        }
        if (r.physicallyCorrectLights !== undefined) {
            r.physicallyCorrectLights = true;
        }

        const skyTex = this._createProceduralSkyTexture();
        const aniso = r.capabilities && r.capabilities.getMaxAnisotropy
            ? Math.min(16, r.capabilities.getMaxAnisotropy())
            : 8;
        skyTex.anisotropy = aniso;
        if (THREE.sRGBEncoding !== undefined) {
            skyTex.encoding = THREE.sRGBEncoding;
        }
        skyTex.needsUpdate = true;

        try {
            if (typeof THREE.PMREMGenerator === "function") {
                if (this._pmremGenerator) {
                    try {
                        this._pmremGenerator.dispose();
                    } catch (e) {
                        /* ignore */
                    }
                }
                this._pmremGenerator = new THREE.PMREMGenerator(r);
                if (typeof this._pmremGenerator.compileEquirectangularShader === "function") {
                    this._pmremGenerator.compileEquirectangularShader();
                }
                const rt = this._pmremGenerator.fromEquirectangular(skyTex);
                this.scene.environment = rt.texture;
                this._pmremRenderTarget = rt;
            }
        } catch (e) {
            console.warn("PMREM / IBL failed — continuing without environment map:", e);
        }

        this.scene.background = skyTex;
        this.scene.fog = null;
    }

    /** Warm dusty horizon + soft sun for Mediterranean / ancient city mood. */
    _createProceduralSkyTexture() {
        const w = 1024;
        const h = 512;
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");
        const top = ctx.createLinearGradient(0, 0, 0, h * 0.42);
        top.addColorStop(0, "#9ec8e8");
        top.addColorStop(0.45, "#d4c4a8");
        top.addColorStop(1, "#c4a878");
        ctx.fillStyle = top;
        ctx.fillRect(0, 0, w, h * 0.42);

        const low = ctx.createLinearGradient(0, h * 0.38, 0, h);
        low.addColorStop(0, "#b8a888");
        low.addColorStop(0.55, "#9a8a6a");
        low.addColorStop(1, "#6a5a48");
        ctx.fillStyle = low;
        ctx.fillRect(0, h * 0.38, w, h * 0.62);

        const sunX = w * 0.62;
        const sunY = h * 0.2;
        const sunR = Math.min(w, h) * 0.085;
        const rg = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, sunR * 2.8);
        rg.addColorStop(0, "rgba(255,248,220,0.95)");
        rg.addColorStop(0.25, "rgba(255,220,160,0.35)");
        rg.addColorStop(1, "rgba(255,200,120,0)");
        ctx.fillStyle = rg;
        ctx.fillRect(0, 0, w, h * 0.45);

        const tex = new THREE.CanvasTexture(canvas);
        tex.mapping = THREE.EquirectangularReflectionMapping;
        tex.flipY = false;
        tex.minFilter = THREE.LinearMipmapLinearFilter;
        tex.magFilter = THREE.LinearFilter;
        tex.generateMipmaps = true;
        return tex;
    }

    /**
     * Shared height field → roughness + tangent-space normal maps for terrain detail (tileable).
     * @returns {{ roughnessMap: THREE.DataTexture, normalMap: THREE.DataTexture }}
     */
    _createTerrainDetailMaps() {
        const size = 128;
        const hArr = new Float32Array(size * size);
        const sampleH = (x, y) => {
            const nx = (x / size) * 48;
            const ny = (y / size) * 48;
            return (
                Math.sin(nx * 0.31) * Math.cos(ny * 0.27) * 0.5 +
                Math.sin(nx * 0.11 + ny * 0.13) * 0.35 +
                Math.sin(nx * 0.07 + ny * 0.05) * 0.2
            );
        };
        for (let y = 0; y < size; y++) {
            for (let x = 0; x < size; x++) {
                hArr[y * size + x] = sampleH(x, y);
            }
        }
        const at = (x, y) => {
            const ix = Math.max(0, Math.min(size - 1, x));
            const iy = Math.max(0, Math.min(size - 1, y));
            return hArr[iy * size + ix];
        };

        const roughData = new Uint8Array(size * size * 4);
        const normData = new Uint8Array(size * size * 4);
        for (let y = 0; y < size; y++) {
            for (let x = 0; x < size; x++) {
                const hi = (y * size + x) * 4;
                const hh = hArr[y * size + x];
                const v = Math.max(0, Math.min(255, Math.floor(128 + hh * 95)));
                roughData[hi] = v;
                roughData[hi + 1] = v;
                roughData[hi + 2] = v;
                roughData[hi + 3] = 255;

                const du = at(x + 1, y) - at(x - 1, y);
                const dv = at(x, y + 1) - at(x, y - 1);
                const n = new THREE.Vector3(-du * 1.8, 2, -dv * 1.8).normalize();
                normData[hi] = Math.floor(n.x * 0.5 * 255 + 127.5);
                normData[hi + 1] = Math.floor(n.y * 0.5 * 255 + 127.5);
                normData[hi + 2] = Math.floor(n.z * 0.5 * 255 + 127.5);
                normData[hi + 3] = 255;
            }
        }

        const aniso = this.renderer3d && this.renderer3d.capabilities && this.renderer3d.capabilities.getMaxAnisotropy
            ? Math.min(8, this.renderer3d.capabilities.getMaxAnisotropy())
            : 4;

        const roughTex = new THREE.DataTexture(roughData, size, size);
        roughTex.format = THREE.RGBAFormat;
        roughTex.wrapS = roughTex.wrapT = THREE.RepeatWrapping;
        roughTex.repeat.set(48, 48);
        roughTex.needsUpdate = true;
        roughTex.anisotropy = aniso;

        const normTex = new THREE.DataTexture(normData, size, size);
        normTex.format = THREE.RGBAFormat;
        normTex.wrapS = normTex.wrapT = THREE.RepeatWrapping;
        normTex.repeat.set(48, 48);
        normTex.needsUpdate = true;
        normTex.anisotropy = aniso;
        if (THREE.LinearEncoding !== undefined) {
            normTex.encoding = THREE.LinearEncoding;
        }

        return { roughnessMap: roughTex, normalMap: normTex };
    }

    /**
     * Finer tileable normal map for building meshes (stone/stucco micro-relief), separate from terrain.
     * @returns {THREE.DataTexture}
     */
    _createBuildingSurfaceNormalMap() {
        const size = 64;
        const hArr = new Float32Array(size * size);
        const sampleH = (x, y) => {
            const nx = (x / size) * 96;
            const ny = (y / size) * 96;
            return (
                Math.sin(nx * 0.42) * Math.cos(ny * 0.38) * 0.42 +
                Math.sin(nx * 0.17 + ny * 0.19) * 0.28 +
                Math.sin(nx * 0.09 + ny * 0.11) * 0.18 +
                Math.sin(nx * 0.31 + ny * 0.07) * 0.12
            );
        };
        for (let y = 0; y < size; y++) {
            for (let x = 0; x < size; x++) {
                hArr[y * size + x] = sampleH(x, y);
            }
        }
        const at = (x, y) => {
            const ix = Math.max(0, Math.min(size - 1, x));
            const iy = Math.max(0, Math.min(size - 1, y));
            return hArr[iy * size + ix];
        };
        const normData = new Uint8Array(size * size * 4);
        for (let y = 0; y < size; y++) {
            for (let x = 0; x < size; x++) {
                const hi = (y * size + x) * 4;
                const du = at(x + 1, y) - at(x - 1, y);
                const dv = at(x, y + 1) - at(x, y - 1);
                const n = new THREE.Vector3(-du * 2.2, 2, -dv * 2.2).normalize();
                normData[hi] = Math.floor(n.x * 0.5 * 255 + 127.5);
                normData[hi + 1] = Math.floor(n.y * 0.5 * 255 + 127.5);
                normData[hi + 2] = Math.floor(n.z * 0.5 * 255 + 127.5);
                normData[hi + 3] = 255;
            }
        }
        const aniso = this.renderer3d && this.renderer3d.capabilities && this.renderer3d.capabilities.getMaxAnisotropy
            ? Math.min(8, this.renderer3d.capabilities.getMaxAnisotropy())
            : 4;
        const normTex = new THREE.DataTexture(normData, size, size);
        normTex.format = THREE.RGBAFormat;
        normTex.wrapS = normTex.wrapT = THREE.RepeatWrapping;
        normTex.repeat.set(8, 8);
        normTex.needsUpdate = true;
        normTex.anisotropy = aniso;
        if (THREE.LinearEncoding !== undefined) {
            normTex.encoding = THREE.LinearEncoding;
        }
        return normTex;
    }

    _getBuildingSurfaceNormalMap() {
        if (!this._buildingBaseNormalTex) {
            this._buildingBaseNormalTex = this._createBuildingSurfaceNormalMap();
        }
        return this._buildingBaseNormalTex;
    }

    /** Cloned textures with UV repeat — small bucket set to limit GPU memory. */
    _normalMapTextureForRepeat(repeat) {
        const r = Math.round(Math.max(0.5, Math.min(40, repeat)) * 4) / 4;
        if (!this._buildingNormalByRepeat) this._buildingNormalByRepeat = new Map();
        if (!this._buildingNormalByRepeat.has(r)) {
            // Cap cache at 16 entries to limit GPU memory; evict oldest
            if (this._buildingNormalByRepeat.size >= 16) {
                const oldestKey = this._buildingNormalByRepeat.keys().next().value;
                const oldestTex = this._buildingNormalByRepeat.get(oldestKey);
                if (oldestTex) oldestTex.dispose();
                this._buildingNormalByRepeat.delete(oldestKey);
            }
            const base = this._getBuildingSurfaceNormalMap();
            const t = base.clone();
            t.repeat.set(r, r);
            t.needsUpdate = true;
            this._buildingNormalByRepeat.set(r, t);
        }
        return this._buildingNormalByRepeat.get(r);
    }

    _disposeBuildingSurfaceResources() {
        if (this._matSurfaceDetailCache) {
            this._matSurfaceDetailCache.forEach((mat) => mat.dispose());
            this._matSurfaceDetailCache.clear();
        }
        if (this._buildingNormalByRepeat) {
            this._buildingNormalByRepeat.forEach((tex) => tex.dispose());
            this._buildingNormalByRepeat.clear();
        }
        if (this._buildingBaseNormalTex) {
            this._buildingBaseNormalTex.dispose();
            this._buildingBaseNormalTex = null;
        }
    }

    // ─── Materials (cached for performance) ───
    /** roughness/metalness 0..1; cache key stable to avoid float churn */
    _mat(color, roughness = 0.7, metalness = 0.02) {
        const r = Math.max(0.05, Math.min(1, Number(roughness)));
        const m = Math.max(0, Math.min(1, Number(metalness)));
        const key = `${color}:${r.toFixed(3)}:${m.toFixed(3)}`;
        if (!this._matCache) this._matCache = new Map();
        if (!this._matCache.has(key)) {
            const mat = new THREE.MeshStandardMaterial({
                color: new THREE.Color(color),
                roughness: r,
                metalness: m,
                envMapIntensity: 0.78,
            });
            mat._cached = true;
            this._matCache.set(key, mat);
        }
        return this._matCache.get(key);
    }

    /**
     * Per-component PBR from Urbanista: optional roughness / metalness (0..1);
     * optional surface_detail (0..1) + detail_repeat for procedural normal relief;
     * optional map_url for albedo (http/https, loads async).
     */
    _matPBR(comp, color, defaultRoughness = 0.7, defaultMetalness = 0.02) {
        const hasR = comp != null && Number.isFinite(comp.roughness);
        const hasM = comp != null && Number.isFinite(comp.metalness);
        const r = Math.max(0.05, Math.min(1, hasR ? Number(comp.roughness) : defaultRoughness));
        const m = Math.max(0, Math.min(1, hasM ? Number(comp.metalness) : defaultMetalness));

        if (comp != null && typeof comp.map_url === "string") {
            const u = comp.map_url.trim();
            if (u.length > 4 && /^https?:\/\//i.test(u)) {
                return this._createMaterialWithAlbedoUrl(comp, color, r, m);
            }
        }

        const sdRaw = comp != null && Number.isFinite(comp.surface_detail) ? Number(comp.surface_detail) : 0;
        if (comp != null && sdRaw > 0) {
            return this._getCachedSurfaceDetailMaterial(comp, color, r, m, sdRaw);
        }

        return this._mat(color, r, m);
    }

    _getCachedSurfaceDetailMaterial(comp, color, r, m, surfaceDetail) {
        const detail = Math.max(0.05, Math.min(1, surfaceDetail));
        const repeat = Number.isFinite(comp.detail_repeat) ? Math.max(0.5, Math.min(40, Number(comp.detail_repeat))) : 8;
        const key = `sd:${color}:${r.toFixed(3)}:${m.toFixed(3)}:${detail.toFixed(2)}:${repeat.toFixed(2)}`;
        if (!this._matSurfaceDetailCache) this._matSurfaceDetailCache = new Map();
        if (!this._matSurfaceDetailCache.has(key)) {
            const nmap = this._normalMapTextureForRepeat(repeat);
            const mat = new THREE.MeshStandardMaterial({
                color: new THREE.Color(color),
                roughness: r,
                metalness: m,
                normalMap: nmap,
                normalScale: new THREE.Vector2(detail * 0.52, detail * 0.52),
                envMapIntensity: 0.78,
            });
            mat._cached = true;
            this._matSurfaceDetailCache.set(key, mat);
        }
        return this._matSurfaceDetailCache.get(key);
    }

    /** Plain PBR body for async map_url loads — avoids cloning cached surface-detail materials (shared normal maps + dispose). */
    _materialBodyForAlbedoUrl(comp, color, r, m) {
        return new THREE.MeshStandardMaterial({
            color: new THREE.Color(color),
            roughness: r,
            metalness: m,
            envMapIntensity: 0.78,
        });
    }

    _createMaterialWithAlbedoUrl(comp, color, r, m) {
        const mat = this._materialBodyForAlbedoUrl(comp, color, r, m);
        mat._cached = false;
        const url = comp.map_url.trim();
        const maxA = this.renderer3d && this.renderer3d.capabilities && this.renderer3d.capabilities.getMaxAnisotropy
            ? Math.min(8, this.renderer3d.capabilities.getMaxAnisotropy())
            : 4;
        const loader = new THREE.TextureLoader();
        loader.load(
            url,
            (tex) => {
                if (THREE.sRGBEncoding !== undefined) {
                    tex.encoding = THREE.sRGBEncoding;
                }
                tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
                const rep = Number.isFinite(comp.detail_repeat) ? Math.max(0.5, Math.min(40, Number(comp.detail_repeat))) : 8;
                tex.repeat.set(rep, rep);
                tex.anisotropy = maxA;
                tex.needsUpdate = true;
                mat.map = tex;
                mat.needsUpdate = true;
            },
            undefined,
            () => {}
        );
        return mat;
    }

    // ─── Clear scene for reset ───
    _clearScene() {
        this._disposeBuildingSurfaceResources();
        // Dispose PMREM render target to free GPU memory
        if (this._pmremRenderTarget) {
            this._pmremRenderTarget.dispose();
            this._pmremRenderTarget = null;
        }
        // Dispose canvas-based background texture
        if (this.scene.background && typeof this.scene.background.dispose === "function") {
            this.scene.background.dispose();
            this.scene.background = null;
        }
        // Remove all dynamic objects (buildings, ground, grid) but keep lights and camera
        const keep = new Set();
        this.scene.children.forEach(child => {
            if (child.isLight || child.isCamera || child === this.camera) keep.add(child);
        });
        const toRemove = this.scene.children.filter(child => !keep.has(child));
        for (const obj of toRemove) {
            this.scene.remove(obj);
            obj.traverse(c => {
                if (c.geometry) c.geometry.dispose();
                if (c.material) {
                    if (Array.isArray(c.material)) c.material.forEach(m => m.dispose());
                    else if (!c.material._cached) c.material.dispose();
                }
            });
        }
        this.buildingGroups.clear();
        this._meshList = [];
        this._meshListDirty = true;
        this._waterMeshes = [];
        this._animatingGroups.clear();
        this._cornerHeights = null;
    }

    _disposeGroupResources(group) {
        group.traverse((c) => {
            if (c.geometry) c.geometry.dispose();
            if (c.material) {
                if (Array.isArray(c.material)) c.material.forEach((m) => { if (m && !m._cached) m.dispose(); });
                else if (!c.material._cached) c.material.dispose();
            }
        });
    }

    _emitRenderError(error, tile, key) {
        window.dispatchEvent(new CustomEvent("world-render-error", {
            detail: { error, key, tile: { x: tile.x, y: tile.y, terrain: tile.terrain } },
        }));
    }

    /**
     * Per-corner elevation (0..1) from adjacent tile elevations — smooth heightfield.
     * Corner (i,j) in world sits at (i*S, j*S); each tile has one nominal elevation.
     */
    _computeCornerHeightGrid() {
        const gw = this.width;
        const gh = this.height;
        const H = [];
        for (let j = 0; j <= gh; j++) {
            const row = [];
            for (let i = 0; i <= gw; i++) {
                let sum = 0;
                let n = 0;
                let maxBuildingElev = null; // Track if any adjacent tile is a building
                for (const [tx, ty] of [[i - 1, j - 1], [i, j - 1], [i - 1, j], [i, j]]) {
                    if (tx >= 0 && ty >= 0 && tx < gw && ty < gh) {
                        const t = this.grid[ty][tx];
                        const e = t && t.elevation != null ? Number(t.elevation) : 0;
                        if (Number.isFinite(e)) {
                            sum += e;
                            n++;
                            // Building tiles force the terrain up to their elevation
                            // so the ground forms a flat platform under the building
                            if (t.terrain === "building") {
                                if (maxBuildingElev === null || e > maxBuildingElev) {
                                    maxBuildingElev = e;
                                }
                            }
                        }
                    }
                }
                // If any adjacent tile is a building, raise this corner to the building's
                // elevation — creates a flat platform the building sits on naturally
                if (maxBuildingElev !== null) {
                    row.push(maxBuildingElev);
                } else {
                    row.push(n ? sum / n : 0);
                }
            }
            H.push(row);
        }
        return H;
    }

    /**
     * Bilinear surface Y (world units) matching the terrain mesh at (x,z).
     */
    _surfaceYAtWorldXZ(x, z) {
        const S = TILE_SIZE;
        const gw = this.width;
        const gh = this.height;
        const H = this._cornerHeights;
        if (!H || !H.length) return 0;
        const gx = x / S;
        const gz = z / S;
        const i0 = Math.min(Math.max(0, Math.floor(gx)), gw);
        const j0 = Math.min(Math.max(0, Math.floor(gz)), gh);
        const i1 = Math.min(i0 + 1, gw);
        const j1 = Math.min(j0 + 1, gh);
        const u = gx - i0;
        const v = gz - j0;
        const h00 = H[j0][i0];
        const h10 = H[j0][i1];
        const h01 = H[j1][i0];
        const h11 = H[j1][i1];
        const h0 = h00 * (1 - u) + h10 * u;
        const h1 = h01 * (1 - u) + h11 * u;
        const h = h0 * (1 - v) + h1 * v;
        return h * S;
    }

    _buildTerrainHeightfieldMesh(S, earthColor, minH, maxH) {
        const gw = this.width;
        const gh = this.height;
        const H = this._cornerHeights;
        const positions = [];
        const indices = [];
        const idx = (i, j) => j * (gw + 1) + i;
        const uvs = [];
        const colors = [];
        const baseCol = new THREE.Color(earthColor);
        const colorLow = baseCol.clone().multiplyScalar(0.78).lerp(new THREE.Color(0x5a7a88), 0.12);
        const colorHigh = baseCol.clone().multiplyScalar(1.14).lerp(new THREE.Color(0xd8c898), 0.18);
        const hSpan = maxH > minH ? maxH - minH : 1;
        const tmpCol = new THREE.Color();
        for (let j = 0; j <= gh; j++) {
            for (let i = 0; i <= gw; i++) {
                const hTile = H[j][i];
                const y = hTile * S;
                positions.push(i * S, y, j * S);
                uvs.push(i / Math.max(1, gw), j / Math.max(1, gh));
                const t = Math.max(0, Math.min(1, (hTile - minH) / hSpan));
                tmpCol.copy(colorLow).lerp(colorHigh, t);
                colors.push(tmpCol.r, tmpCol.g, tmpCol.b);
            }
        }
        for (let j = 0; j < gh; j++) {
            for (let i = 0; i < gw; i++) {
                const a = idx(i, j);
                const b = idx(i + 1, j);
                const c = idx(i, j + 1);
                const d = idx(i + 1, j + 1);
                indices.push(a, b, d, a, d, c);
            }
        }
        const geom = new THREE.BufferGeometry();
        geom.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
        geom.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
        geom.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
        geom.setIndex(indices);
        geom.computeVertexNormals();
        const matParams = {
            color: 0xffffff,
            vertexColors: true,
            metalness: 0.04,
            envMapIntensity: 0.62,
            flatShading: false,
        };
        if (this._terrainRoughnessMap) {
            matParams.roughnessMap = this._terrainRoughnessMap;
            matParams.roughness = 1;
        } else {
            matParams.roughness = 0.9;
        }
        if (this._terrainNormalMap) {
            matParams.normalMap = this._terrainNormalMap;
            matParams.normalScale = new THREE.Vector2(0.45, 0.45);
        }
        const mat = new THREE.MeshStandardMaterial(matParams);
        const mesh = new THREE.Mesh(geom, mat);
        mesh.receiveShadow = true;
        mesh.castShadow = false;
        mesh.userData.isTerrainHeightfield = true;
        return mesh;
    }

    /** If Urbanista used Greco-Roman template ids but tradition or name is Mesoamerican, swap to regional massing. */
    _maybeMesoamericanTemplateId(templateId, spec, tile) {
        let id = String(templateId);
        const trad = spec && spec.tradition != null ? String(spec.tradition).toLowerCase() : "";
        const bn = tile && tile.building_name != null ? String(tile.building_name).toLowerCase() : "";
        const meso =
            trad.includes("mesoamerican") ||
            trad.includes("aztec") ||
            trad.includes("mexica") ||
            trad.includes("tenochtitlan") ||
            trad.includes("nahua") ||
            trad.includes("templo") ||
            bn.includes("templo") ||
            bn.includes("teocalli") ||
            bn.includes("calpulli") ||
            bn.includes("tlatoani");
        if (!meso) return id;
        if (id === "temple") return "mesoamerican_temple";
        if (id === "monument") return "mesoamerican_shrine";
        if (id === "basilica" || id === "market") return "mesoamerican_civic";
        return id;
    }

    // ─── Init ───
    init(worldState) {
        this._clearScene();
        this.width = worldState.width;
        this.height = worldState.height;

        // Build empty 2D grid, then patch non-empty tiles from sparse payload
        const emptyTile = { terrain: "empty", elevation: 0 };
        this.grid = [];
        for (let y = 0; y < this.height; y++) {
            const row = [];
            for (let x = 0; x < this.width; x++) row.push({ ...emptyTile, x, y });
            this.grid.push(row);
        }
        if (Array.isArray(worldState.tiles)) {
            for (const t of worldState.tiles) {
                if (t.x >= 0 && t.y >= 0 && t.x < this.width && t.y < this.height)
                    this.grid[t.y][t.x] = t;
            }
        } else if (Array.isArray(worldState.grid)) {
            // Legacy dense grid format (backwards compat)
            this.grid = worldState.grid;
        }
        const S = TILE_SIZE;
        this._cornerHeights = this._computeCornerHeightGrid();

        let minH = 0;
        let maxH = 0;
        for (const row of this._cornerHeights) {
            for (const v of row) {
                if (v < minH) minH = v;
                if (v > maxH) maxH = v;
            }
        }
        const midY = ((minH + maxH) / 2) * S;
        const mapW = this.width * S;
        const mapH = this.height * S;
        const diagonal = Math.hypot(mapW, mapH);
        this._camDistMin = 0.8;
        this._camDistMax = Math.max(5200, diagonal * 2.75);

        if (this._sunLight && this._sunLight.shadow) {
            const sc = this._sunLight.shadow.camera;
            const half = Math.max(mapW, mapH) * 0.52;
            sc.left = -half;
            sc.right = half;
            sc.top = half;
            sc.bottom = -half;
            sc.far = Math.max(6000, diagonal * 3.5);
            sc.updateProjectionMatrix();
            const shadowMapSize = diagonal > 1400 ? 4096 : 2048;
            if (this._sunLight.shadow.map) {
                this._sunLight.shadow.map.dispose();
                this._sunLight.shadow.map = null;
            }
            this._sunLight.shadow.mapSize.set(shadowMapSize, shadowMapSize);
        }

        this._resetCameraPoseToMapCenter(midY, mapW, mapH);

        // Earth tone heightfield — matches _surfaceYAtWorldXZ for building / terrain props
        const earth = 0x9a7b52;
        const terrainMesh = this._buildTerrainHeightfieldMesh(S, earth, minH, maxH);
        this.scene.add(terrainMesh);
        this._terrainMesh = terrainMesh;

        // Distant water / lake bed (below lowest terrain)
        const waterY = minH * S - S * 0.35;
        const gw = this.width * S + 24;
        const gh = this.height * S + 24;
        const waterMat =
            typeof THREE.MeshPhysicalMaterial === "function"
                ? new THREE.MeshPhysicalMaterial({
                    color: 0x1a4a68,
                    roughness: 0.22,
                    metalness: 0.1,
                    envMapIntensity: 1.1,
                    transparent: true,
                    opacity: 0.9,
                    clearcoat: 0.45,
                    clearcoatRoughness: 0.18,
                })
                : new THREE.MeshStandardMaterial({
                    color: 0x2a5f7a,
                    roughness: 0.55,
                    metalness: 0.08,
                    envMapIntensity: 0.95,
                    transparent: true,
                    opacity: 0.9,
                });
        const water = new THREE.Mesh(new THREE.PlaneGeometry(gw, gh), waterMat);
        water.rotation.x = -Math.PI / 2;
        water.position.set(this.width * S / 2, waterY, this.height * S / 2);
        water.receiveShadow = true;
        water.userData.isWaterPlane = true;
        this._waterPlane = water;
        this._waterPlaneBaseY = waterY;
        this.scene.add(water);

        // Grid — slightly above lowest ground so it stays visible on slopes
        const gridSize = Math.max(this.width, this.height) * S;
        const grid = new THREE.GridHelper(gridSize, Math.max(this.width, this.height), 0x6a5c40, 0x6a5c40);
        grid.position.set(this.width * S / 2, minH * S + 0.04, this.height * S / 2);
        grid.material.opacity = 0.07;
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
        // Errors are returned per tile and emitted as world-render-error; no silent fallbacks.
    }

    updateTiles(tiles) {
        if (!this.grid) return;
        // Recompute terrain heightfield with new tile elevations so buildings sit on ground
        let heightsDirty = false;
        for (const tile of tiles) {
            if (tile.x >= 0 && tile.y >= 0 && tile.x < this.width && tile.y < this.height) {
                const old = this.grid[tile.y][tile.x];
                if (old.elevation !== tile.elevation) heightsDirty = true;
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
        if (heightsDirty) {
            this._cornerHeights = this._computeCornerHeightGrid();
            if (this._terrainMesh) {
                this.scene.remove(this._terrainMesh);
                this._terrainMesh.geometry.dispose();
                const S = TILE_SIZE;
                const earth = 0x9a7b52;
                let minH = 0, maxH = 0;
                for (const row of this._cornerHeights) {
                    for (const v of row) {
                        if (v < minH) minH = v;
                        if (v > maxH) maxH = v;
                    }
                }
                this._terrainMesh = this._buildTerrainHeightfieldMesh(S, earth, minH, maxH);
                this.scene.add(this._terrainMesh);
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

    _tileAt(gridX, gridY) {
        if (!this.grid || gridX < 0 || gridY < 0 || gridX >= this.width || gridY >= this.height) return null;
        return this.grid[gridY][gridX];
    }

    _structureKey(t) {
        if (!t) return "";
        if (t.spec && t.spec.anchor) return `${t.spec.anchor.x},${t.spec.anchor.y}`;
        return `${t.x},${t.y}`;
    }

    /**
     * Cardinal adjacency for this footprint: roads, other buildings, water, green, forum.
     */
    _computeNeighborContext(tile, spec) {
        const fp = spec && spec.anchor
            ? this._getAnchorFootprint(spec.anchor)
            : { minX: tile.x, maxX: tile.x, minY: tile.y, maxY: tile.y };
        const myKey = this._structureKey(tile);
        const at = (x, y) => this._tileAt(x, y);
        const road = (t) => t && t.terrain === "road";
        const water = (t) => t && t.terrain === "water";
        const green = (t) => t && (t.terrain === "garden" || t.terrain === "grass");
        const forum = (t) => t && t.terrain === "forum";
        const otherBuilding = (t) => t && t.terrain === "building" && this._structureKey(t) !== myKey;

        const anyNorth = () => {
            if (fp.minY <= 0) return { road: false, water: false, green: false, forum: false, otherBuilding: false };
            let r = false, w = false, g = false, f = false, o = false;
            for (let x = fp.minX; x <= fp.maxX; x++) {
                const t = at(x, fp.minY - 1);
                if (road(t)) r = true;
                if (water(t)) w = true;
                if (green(t)) g = true;
                if (forum(t)) f = true;
                if (otherBuilding(t)) o = true;
            }
            return { road: r, water: w, green: g, forum: f, otherBuilding: o };
        };
        const anySouth = () => {
            if (fp.maxY >= this.height - 1) return { road: false, water: false, green: false, forum: false, otherBuilding: false };
            let r = false, w = false, g = false, f = false, o = false;
            for (let x = fp.minX; x <= fp.maxX; x++) {
                const t = at(x, fp.maxY + 1);
                if (road(t)) r = true;
                if (water(t)) w = true;
                if (green(t)) g = true;
                if (forum(t)) f = true;
                if (otherBuilding(t)) o = true;
            }
            return { road: r, water: w, green: g, forum: f, otherBuilding: o };
        };
        const anyEast = () => {
            if (fp.maxX >= this.width - 1) return { road: false, water: false, green: false, forum: false, otherBuilding: false };
            let r = false, w = false, g = false, f = false, o = false;
            for (let y = fp.minY; y <= fp.maxY; y++) {
                const t = at(fp.maxX + 1, y);
                if (road(t)) r = true;
                if (water(t)) w = true;
                if (green(t)) g = true;
                if (forum(t)) f = true;
                if (otherBuilding(t)) o = true;
            }
            return { road: r, water: w, green: g, forum: f, otherBuilding: o };
        };
        const anyWest = () => {
            if (fp.minX <= 0) return { road: false, water: false, green: false, forum: false, otherBuilding: false };
            let r = false, w = false, g = false, f = false, o = false;
            for (let y = fp.minY; y <= fp.maxY; y++) {
                const t = at(fp.minX - 1, y);
                if (road(t)) r = true;
                if (water(t)) w = true;
                if (green(t)) g = true;
                if (forum(t)) f = true;
                if (otherBuilding(t)) o = true;
            }
            return { road: r, water: w, green: g, forum: f, otherBuilding: o };
        };

        return {
            footprint: fp,
            n: anyNorth(),
            s: anySouth(),
            e: anyEast(),
            w: anyWest(),
        };
    }

    _phase4RuinIntensity(tile, spec, p4) {
        if (p4 && p4.ruin_overgrowth != null) {
            const v = Number(p4.ruin_overgrowth);
            if (Number.isFinite(v)) return Math.max(0, Math.min(1, v));
        }
        const bt = String(tile.building_type || "").toLowerCase();
        if (bt.includes("ruin")) return 0.78;
        return 0;
    }

    _applyPhase4ContextualPolish(group, tile, tileW, tileD, spec, ctx) {
        const p4 = spec && typeof spec.phase4 === "object" && spec.phase4 ? spec.phase4 : {};
        if (p4.disable_all) return;

        let budget = PHASE4_MAX_DECOR_MESHES;
        const baseY = 0;
        const stepMat = this._mat(p4.step_color || "#c4b5a0", 0.88);
        const wallMat = this._mat(p4.party_wall_color || "#2c2826", 0.9);
        const fasciaMat = this._mat(p4.street_front_color || "#6b5344", 0.75);
        const waterMat = this._mat("#5c4a38", 0.85);
        const hedgeMat = this._mat("#3d5c32", 0.82);
        const ivyMat = this._mat("#2d4a28", 0.65);

        const COST_STEPS = PHASE4_STEP_COUNT;
        const COST_FASCIA = 1;
        const COST_PARTY = 1;
        const COST_WATER = 3;
        const COST_HEDGE = 1;
        const COST_AWNING = 1;
        const COST_SIGN = 1;

        const addRoadAwning = (edge) => {
            if (p4.disable_road_awning) return;
            const W = Math.max(0.35, tileW);
            const D = Math.max(0.35, tileD);
            const depth = 0.09;
            const thick = 0.014;
            const y0 =
                typeof p4.awning_height === "number" && Number.isFinite(p4.awning_height)
                    ? p4.awning_height
                    : 0.17;
            const awningMat = this._mat(p4.awning_color || "#7a6045", 0.78);
            const mesh = new THREE.Mesh(new THREE.BoxGeometry(W * 0.8, thick, depth), awningMat);
            mesh.userData.isPhase4Context = true;
            mesh.castShadow = true;
            mesh.receiveShadow = true;
            if (edge === "n") {
                mesh.position.set(0, baseY + y0, -D / 2 + depth * 0.35);
                mesh.rotation.x = -0.42;
            } else if (edge === "s") {
                mesh.position.set(0, baseY + y0, D / 2 - depth * 0.35);
                mesh.rotation.x = 0.42;
            } else if (edge === "w") {
                mesh.position.set(-W / 2 + depth * 0.35, baseY + y0, 0);
                mesh.rotation.z = 0.42;
            } else {
                mesh.position.set(W / 2 - depth * 0.35, baseY + y0, 0);
                mesh.rotation.z = -0.42;
            }
            group.add(mesh);
        };

        const addStreetSign = (edge) => {
            if (p4.disable_street_signs) return;
            const W = Math.max(0.35, tileW);
            const D = Math.max(0.35, tileD);
            const signMat = this._mat(p4.sign_color || "#3d2e24", 0.85);
            const board = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.045, 0.012), signMat);
            board.userData.isPhase4Context = true;
            board.castShadow = true;
            board.receiveShadow = true;
            const seed = tile.x * 131 + tile.y * 17;
            const along = ((seed % 1000) / 1000 - 0.5) * 0.45 * Math.min(W, D);
            const ySign = 0.2 + ((seed * 7) % 10) / 250;
            if (edge === "n") {
                board.position.set(along, baseY + ySign, -D / 2 + 0.045);
            } else if (edge === "s") {
                board.position.set(along, baseY + ySign, D / 2 - 0.045);
            } else if (edge === "w") {
                board.position.set(-W / 2 + 0.045, baseY + ySign, along);
            } else {
                board.position.set(W / 2 - 0.045, baseY + ySign, along);
            }
            group.add(board);
        };

        const addSteps = (edge) => {
            if (p4.disable_auto_steps) return;
            const W = Math.max(0.35, tileW);
            const D = Math.max(0.35, tileD);
            for (let i = 0; i < PHASE4_STEP_COUNT; i++) {
                const dz = PHASE4_STEP_DEPTH;
                const h = PHASE4_STEP_HEIGHT;
                let mesh;
                if (edge === "n") {
                    mesh = new THREE.Mesh(new THREE.BoxGeometry(W * 0.92, h, dz), stepMat);
                    mesh.position.set(0, baseY + h / 2 + i * h * 0.92, -D / 2 + dz / 2 + i * dz * 0.98);
                } else if (edge === "s") {
                    mesh = new THREE.Mesh(new THREE.BoxGeometry(W * 0.92, h, dz), stepMat);
                    mesh.position.set(0, baseY + h / 2 + i * h * 0.92, D / 2 - dz / 2 - i * dz * 0.98);
                } else if (edge === "w") {
                    mesh = new THREE.Mesh(new THREE.BoxGeometry(dz, h, D * 0.92), stepMat);
                    mesh.position.set(-W / 2 + dz / 2 + i * dz * 0.98, baseY + h / 2 + i * h * 0.92, 0);
                } else {
                    mesh = new THREE.Mesh(new THREE.BoxGeometry(dz, h, D * 0.92), stepMat);
                    mesh.position.set(W / 2 - dz / 2 - i * dz * 0.98, baseY + h / 2 + i * h * 0.92, 0);
                }
                mesh.userData.isPhase4Context = true;
                mesh.castShadow = true;
                mesh.receiveShadow = true;
                group.add(mesh);
            }
        };

        const addParty = (edge) => {
            if (p4.disable_party_walls) return;
            const h = typeof p4.party_wall_height === "number" ? p4.party_wall_height : 0.4;
            const t = 0.02;
            const W = Math.max(0.35, tileW);
            const D = Math.max(0.35, tileD);
            let mesh;
            if (edge === "n") {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(W * 0.93, h, t), wallMat);
                mesh.position.set(0, baseY + h / 2, -D / 2 + t / 2);
            } else if (edge === "s") {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(W * 0.93, h, t), wallMat);
                mesh.position.set(0, baseY + h / 2, D / 2 - t / 2);
            } else if (edge === "w") {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(t, h, D * 0.93), wallMat);
                mesh.position.set(-W / 2 + t / 2, baseY + h / 2, 0);
            } else {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(t, h, D * 0.93), wallMat);
                mesh.position.set(W / 2 - t / 2, baseY + h / 2, 0);
            }
            mesh.userData.isPhase4Context = true;
            mesh.castShadow = true;
            mesh.receiveShadow = true;
            group.add(mesh);
        };

        const addFascia = (edge) => {
            if (p4.disable_street_fascia) return;
            const fy = typeof p4.street_fascia_height === "number" ? p4.street_fascia_height : 0.11;
            const fh = 0.035;
            const fd = 0.055;
            const W = Math.max(0.35, tileW);
            const D = Math.max(0.35, tileD);
            let mesh;
            if (edge === "n") {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(W * 0.88, fh, fd), fasciaMat);
                mesh.position.set(0, baseY + fy, -D / 2 + fd / 2 + 0.01);
            } else if (edge === "s") {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(W * 0.88, fh, fd), fasciaMat);
                mesh.position.set(0, baseY + fy, D / 2 - fd / 2 - 0.01);
            } else if (edge === "w") {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(fd, fh, D * 0.88), fasciaMat);
                mesh.position.set(-W / 2 + fd / 2 + 0.01, baseY + fy, 0);
            } else {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(fd, fh, D * 0.88), fasciaMat);
                mesh.position.set(W / 2 - fd / 2 - 0.01, baseY + fy, 0);
            }
            mesh.userData.isPhase4Context = true;
            mesh.castShadow = true;
            mesh.receiveShadow = true;
            group.add(mesh);
        };

        const addWaterPosts = (edge) => {
            if (p4.disable_water_mooring) return;
            const W = Math.max(0.35, tileW);
            const D = Math.max(0.35, tileD);
            const nPost = 3;
            const r = 0.022;
            const postH = 0.14;
            for (let i = 0; i < nPost; i++) {
                const mesh = new THREE.Mesh(new THREE.CylinderGeometry(r, r * 1.1, postH, 6), waterMat);
                let px = 0, pz = 0;
                const f = (i + 0.5) / nPost - 0.5;
                if (edge === "n") {
                    px = f * W * 0.75;
                    pz = -D / 2 + 0.04;
                } else if (edge === "s") {
                    px = f * W * 0.75;
                    pz = D / 2 - 0.04;
                } else if (edge === "w") {
                    px = -W / 2 + 0.04;
                    pz = f * D * 0.75;
                } else {
                    px = W / 2 - 0.04;
                    pz = f * D * 0.75;
                }
                mesh.position.set(px, baseY + postH / 2, pz);
                mesh.userData.isPhase4Context = true;
                mesh.castShadow = true;
                group.add(mesh);
            }
        };

        const addGardenHedge = (edge) => {
            if (p4.disable_garden_hedge) return;
            const hh = 0.07;
            const th = 0.04;
            const W = Math.max(0.35, tileW);
            const D = Math.max(0.35, tileD);
            let mesh;
            if (edge === "n") {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(W * 0.85, hh, th), hedgeMat);
                mesh.position.set(0, baseY + hh / 2, -D / 2 + th / 2 + 0.02);
            } else if (edge === "s") {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(W * 0.85, hh, th), hedgeMat);
                mesh.position.set(0, baseY + hh / 2, D / 2 - th / 2 - 0.02);
            } else if (edge === "w") {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(th, hh, D * 0.85), hedgeMat);
                mesh.position.set(-W / 2 + th / 2 + 0.02, baseY + hh / 2, 0);
            } else {
                mesh = new THREE.Mesh(new THREE.BoxGeometry(th, hh, D * 0.85), hedgeMat);
                mesh.position.set(W / 2 - th / 2 - 0.02, baseY + hh / 2, 0);
            }
            mesh.userData.isPhase4Context = true;
            mesh.castShadow = true;
            mesh.receiveShadow = true;
            group.add(mesh);
        };

        const edgeKeys = ["n", "s", "e", "w"];
        for (const e of edgeKeys) {
            const seg = ctx[e];
            if (seg.road) {
                if (!p4.disable_auto_steps && budget >= COST_STEPS) {
                    addSteps(e);
                    budget -= COST_STEPS;
                }
                if (!p4.disable_street_fascia && budget >= COST_FASCIA) {
                    addFascia(e);
                    budget -= COST_FASCIA;
                }
                if (!p4.disable_road_awning && budget >= COST_AWNING) {
                    addRoadAwning(e);
                    budget -= COST_AWNING;
                }
                if (!p4.disable_street_signs && budget >= COST_SIGN) {
                    addStreetSign(e);
                    budget -= COST_SIGN;
                }
            }
            if (seg.otherBuilding && !p4.disable_party_walls && budget >= COST_PARTY) {
                addParty(e);
                budget -= COST_PARTY;
            }
            if (seg.water && !p4.disable_water_mooring && budget >= COST_WATER) {
                addWaterPosts(e);
                budget -= COST_WATER;
            }
            if ((seg.green || seg.forum) && !p4.disable_garden_hedge && budget >= COST_HEDGE) {
                addGardenHedge(e);
                budget -= COST_HEDGE;
            }
        }

        const ruinI = this._phase4RuinIntensity(tile, spec, p4);
        if (!p4.disable_ruin_vegetation && ruinI > 0 && budget > 4) {
            const nIvy = Math.min(budget, Math.floor(8 + ruinI * 26));
            const seed = tile.x * 131 + tile.y * 97;
            for (let i = 0; i < nIvy; i++) {
                const rx = ((seed * (i + 3)) % 1000) / 1000 - 0.5;
                const rz = ((seed * (i + 7)) % 1000) / 1000 - 0.5;
                const px = rx * tileW * 0.42;
                const pz = rz * tileD * 0.42;
                const rs = 0.025 + ((seed + i * 11) % 20) / 700;
                const py = baseY + 0.22 + ((seed * i) % 15) / 100;
                const mesh = new THREE.Mesh(new THREE.SphereGeometry(rs, 5, 4), ivyMat);
                mesh.position.set(px, py, pz);
                mesh.userData.isPhase4Context = true;
                mesh.castShadow = true;
                group.add(mesh);
            }
        }
    }

    // ═══════════════════════════════════════════════
    // COMPONENT-BASED RENDERER
    // Terrain tiles use procedural meshes; buildings require spec.components OR spec.template (parametric_templates.js).
    // No placeholder geometry — violations return { ok: false } and emit world-render-error.
    // ═══════════════════════════════════════════════

    /**
     * @returns {{ ok: true } | { ok: false, error: string, key: string }}
     */
    _buildFromSpec(tile, animate) {
        const spec = tile.spec;
        const key = spec && spec.anchor ? `${spec.anchor.x},${spec.anchor.y}` : `${tile.x},${tile.y}`;
        const oldGroup = this.buildingGroups.get(key) || null;

        const terrain = tile.terrain;
        const isTerrainMesh = TERRAIN_WITH_PROCEDURAL_MESH.has(terrain);

        const S = TILE_SIZE;
        let tileW = 0.9, tileD = 0.9;
        let centerX = (tile.x + 0.5) * S, centerZ = (tile.y + 0.5) * S;
        if (spec && spec.anchor && this.grid) {
            const fp = this._getAnchorFootprint(spec.anchor);
            tileW = (fp.maxX - fp.minX + 1) - 0.1;
            tileD = (fp.maxY - fp.minY + 1) - 0.1;
            tileW = Math.max(0.3, tileW);
            tileD = Math.max(0.3, tileD);
            centerX = (fp.minX + fp.maxX + 1) / 2 * S;
            centerZ = (fp.minY + fp.maxY + 1) / 2 * S;
        }

        let resolvedComponents = null;
        if (!isTerrainMesh && spec) {
            const tmpl = spec.template;
            if (tmpl && typeof tmpl === "object" && tmpl.id) {
                if (typeof globalThis.expandParametricTemplate !== "function") {
                    const err =
                        "expandParametricTemplate missing — ensure parametric_templates.js loads before renderer3d.js";
                    this._emitRenderError(err, tile, key);
                    return { ok: false, error: err, key };
                }
                try {
                    const templateIdResolved = this._maybeMesoamericanTemplateId(String(tmpl.id), spec, tile);
                    resolvedComponents = globalThis.expandParametricTemplate(
                        templateIdResolved,
                        tmpl.params && typeof tmpl.params === "object" ? tmpl.params : {},
                        tileW,
                        tileD
                    );
                } catch (e) {
                    const msg = e && e.message ? e.message : String(e);
                    const err = `Parametric template failed for tile (${tile.x},${tile.y}): ${msg}`;
                    this._emitRenderError(err, tile, key);
                    return { ok: false, error: err, key };
                }
            } else if (Array.isArray(spec.components) && spec.components.length > 0) {
                resolvedComponents = spec.components;
            }
        }

        if (!isTerrainMesh) {
            if (!spec || !resolvedComponents || resolvedComponents.length === 0) {
                const err = `Building tile (${tile.x},${tile.y}) requires spec.components or spec.template with a known id; terrain=${JSON.stringify(terrain)}`;
                this._emitRenderError(err, tile, key);
                return { ok: false, error: err, key };
            }
        }

        // The terrain heightfield already creates flat platforms under buildings
        // (see _computeCornerHeightGrid). Sample the center — it matches the platform.
        const elevation = this._surfaceYAtWorldXZ(centerX, centerZ);

        const group = new THREE.Group();
        group.position.set(centerX, elevation, centerZ);
        group.scale.set(S, S, S);
        group.userData = { tile, baseY: elevation };
        if (spec && spec.tradition != null) {
            group.userData.tradition = spec.tradition;
        }

        try {
            if (isTerrainMesh) {
                this._buildTerrain(group, tile, spec || {});
            } else {
                const proportionRules = spec && spec.proportion_rules && typeof spec.proportion_rules === "object"
                    ? spec.proportion_rules
                    : null;
                this._buildComponents(group, resolvedComponents, tileW, tileD, proportionRules);
            }
        } catch (e) {
            this._disposeGroupResources(group);
            const err = e && e.message ? e.message : String(e);
            const msg = `Build failed for tile (${tile.x},${tile.y}): ${err}`;
            this._emitRenderError(msg, tile, key);
            return { ok: false, error: msg, key };
        }

        if (!isTerrainMesh && this.grid && spec) {
            const neighborContext = this._computeNeighborContext(tile, spec);
            this._applyPhase4ContextualPolish(group, tile, tileW, tileD, spec, neighborContext);
        }

        if (oldGroup) {
            this.scene.remove(oldGroup);
            this._disposeGroupResources(oldGroup);
            this._waterMeshes = this._waterMeshes.filter((m) => {
                let parent = m.parent;
                while (parent) {
                    if (parent === oldGroup) return false;
                    parent = parent.parent;
                }
                return true;
            });
            this._animatingGroups.delete(oldGroup);
        }

        group.traverse((c) => {
            if (c.isMesh) {
                c.receiveShadow = true;
                c.userData.tile = tile;
                if (c.geometry && c.geometry.boundingSphere) {
                    c.geometry.computeBoundingSphere();
                    c.castShadow = c.geometry.boundingSphere.radius > 0.05;
                } else {
                    c.castShadow = true;
                }
            }
        });

        group.traverse((c) => {
            if (c.userData && c.userData.isWater) this._waterMeshes.push(c);
        });

        if (animate) {
            group.userData.animStartY = elevation - 2 * S;
            group.userData.animTargetY = elevation;
            group.userData.animStart = Date.now();
            group.position.y = elevation - 2 * S;
            this._animatingGroups.add(group);
        }

        const worldHalfFootprint = Math.max(tileW, tileD) * S * 0.5;
        const hasStructuralComponents = resolvedComponents && resolvedComponents.length > 0;
        const p4 = spec && typeof spec.phase4 === "object" && spec.phase4 ? spec.phase4 : {};
        const phase4CullExtra =
            !isTerrainMesh && spec && p4.disable_all !== true ? S * PHASE4_CULL_HEIGHT_EXTRA : 0;
        const worldHalfHeight = (hasStructuralComponents ? S * 2.8 : S * 0.04) + phase4CullExtra;
        group.userData.cullCenterOffsetY = worldHalfHeight;
        group.userData.cullRadius = Math.hypot(worldHalfFootprint, worldHalfHeight);

        this.scene.add(group);
        this.buildingGroups.set(key, group);
        this._meshListDirty = true;
        return { ok: true };
    }

    // ─── Terrain ───

    /** Deterministic 0..1 from tile + index (replaces Math.random on roads). */
    _terrainRand01(tileSeed, i, salt) {
        const v = ((tileSeed * 9301 + i * 49297 + salt * 233280) % 999983) / 999983;
        return Math.max(0, Math.min(1, v));
    }

    /** Optional spec.scenery from Urbanista — tunes dressing density (0..1 each). */
    _sceneryFromSpec(spec) {
        const s = spec && typeof spec.scenery === "object" ? spec.scenery : {};
        const c = (v) => (v != null && Number.isFinite(Number(v)) ? Math.max(0, Math.min(1, Number(v))) : null);
        return {
            vegetation_density: c(s.vegetation_density),
            pavement_detail: c(s.pavement_detail),
            water_murk: c(s.water_murk),
        };
    }

    _buildTerrain(g, tile, spec) {
        const terrain = tile.terrain;
        const sc = this._sceneryFromSpec(spec);
        const tseed = tile.x * 131 + tile.y * 97;
        if (terrain === "road") {
            const road = new THREE.Mesh(new THREE.BoxGeometry(0.98, 0.05, 0.98), this._mat(spec.color || "#606060", 0.9));
            road.position.y = 0.025;
            g.add(road);
            const pave = sc.pavement_detail != null ? sc.pavement_detail : 0.45;
            const nStone = Math.min(14, Math.floor(1 + pave * 10) + (tseed % 3));
            for (let i = 0; i < nStone; i++) {
                const w = 0.06 + this._terrainRand01(tseed, i, 1) * 0.08;
                const d = 0.06 + this._terrainRand01(tseed, i, 2) * 0.08;
                const stone = new THREE.Mesh(new THREE.BoxGeometry(w, 0.01, d), this._mat("#7a7a7a"));
                stone.position.set(
                    -0.3 + this._terrainRand01(tseed, i, 3) * 0.6,
                    0.055,
                    -0.3 + this._terrainRand01(tseed, i, 4) * 0.6
                );
                g.add(stone);
            }
        } else if (terrain === "forum") {
            g.add(new THREE.Mesh(new THREE.BoxGeometry(0.96, 0.03, 0.96), this._mat(spec.color || "#d4c67a")));
            const pave = sc.pavement_detail != null ? sc.pavement_detail : 0.25;
            const nSlab = Math.min(8, Math.floor(pave * 7));
            for (let i = 0; i < nSlab; i++) {
                const sw = 0.08 + this._terrainRand01(tseed, i, 11) * 0.12;
                const slab = new THREE.Mesh(new THREE.BoxGeometry(sw, 0.008, sw * 0.9), this._mat("#c8b898", 0.75));
                slab.position.set(
                    -0.32 + this._terrainRand01(tseed, i, 12) * 0.64,
                    0.022,
                    -0.32 + this._terrainRand01(tseed, i, 13) * 0.64
                );
                g.add(slab);
            }
        } else if (terrain === "water") {
            const murk = sc.water_murk != null ? sc.water_murk : 0.35;
            const opacity = 0.55 + murk * 0.38;
            const water = new THREE.Mesh(
                new THREE.BoxGeometry(0.98, 0.06, 0.98),
                new THREE.MeshStandardMaterial({ color: 0x2980b9, transparent: true, opacity, roughness: 0.05 })
            );
            water.position.y = -0.03;
            water.userData.isWater = true;
            g.add(water);
        } else if (terrain === "garden" || terrain === "grass") {
            const groundColor = spec.color || (terrain === "garden" ? "#4a8c3f" : "#6b8c4f");
            g.add(new THREE.Mesh(new THREE.BoxGeometry(0.96, 0.04, 0.96), this._mat(groundColor, 0.85)));
            const seed = tile.x * 31 + tile.y * 17;
            const veg = sc.vegetation_density != null ? sc.vegetation_density : null;
            const basePlants = 1 + seed % 3;
            const numPlants = veg != null ? Math.round(veg * 6) : basePlants;
            const n = Math.max(0, Math.min(6, numPlants));
            for (let i = 0; i < n; i++) {
                const px = -0.25 + ((seed * (i + 1) * 7) % 50) / 100;
                const pz = -0.25 + ((seed * (i + 1) * 13) % 50) / 100;
                const treeH = 0.15 + ((seed * (i + 3)) % 35) / 100;
                // Tapered trunk
                const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.01, 0.022, treeH, 5), this._mat("#5a3a1a", 0.8));
                trunk.position.set(px, 0.04 + treeH / 2, pz);
                g.add(trunk);
                // Varied canopy — alternate between round and conical trees
                const canopySize = 0.07 + ((seed * (i + 7)) % 25) / 200;
                const treeType = (seed + i) % 3;
                let canopy;
                if (treeType === 0) {
                    // Round deciduous
                    canopy = new THREE.Mesh(new THREE.SphereGeometry(canopySize, 6, 5), this._mat("#2d6b1e", 0.75));
                } else if (treeType === 1) {
                    // Conical (cypress/pine)
                    canopy = new THREE.Mesh(new THREE.ConeGeometry(canopySize * 0.7, canopySize * 2.2, 6), this._mat("#1a5c14", 0.7));
                } else {
                    // Broad flat (olive/fig)
                    canopy = new THREE.Mesh(new THREE.SphereGeometry(canopySize * 1.1, 6, 4), this._mat("#3a7a2a", 0.75));
                    canopy.scale.y = 0.6;
                }
                canopy.position.set(px, 0.04 + treeH + canopySize * 0.5, pz);
                g.add(canopy);
            }
            // Low bushes/ground cover for gardens
            if (terrain === "garden" && n > 0) {
                const nBush = 2 + seed % 3;
                for (let i = 0; i < nBush; i++) {
                    const bx = -0.3 + this._terrainRand01(seed, i + 20, 5) * 0.6;
                    const bz = -0.3 + this._terrainRand01(seed, i + 20, 6) * 0.6;
                    const bush = new THREE.Mesh(
                        new THREE.SphereGeometry(0.035 + this._terrainRand01(seed, i, 7) * 0.02, 5, 4),
                        this._mat("#3a6b2a", 0.8)
                    );
                    bush.position.set(bx, 0.06, bz);
                    bush.scale.y = 0.6;
                    g.add(bush);
                }
            }
        } else {
            throw new Error(`Terrain "${terrain}" has no procedural mesh (internal inconsistency with TERRAIN_WITH_PROCEDURAL_MESH)`);
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

    /** @param {unknown} v @returns {number|null} */
    _finiteRuleNumber(v) {
        if (v == null || v === "") return null;
        const n = Number(v);
        return Number.isFinite(n) ? n : null;
    }

    /**
     * Apply optional spec.proportion_rules only. No civilization-specific defaults —
     * every clamp comes from generative JSON; omitted keys mean no clamp on that axis.
     */
    _applyGenerativeProportionRules(components, w, d, rules) {
        const out = components.map((c) =>
            c && typeof c === "object" && c.type ? { ...c } : c
        );
        if (!rules || typeof rules !== "object") return out;

        const minSpan = Math.max(0.22, Math.min(w, d));

        const colR = rules.colonnade;
        if (colR && typeof colR === "object") {
            for (const col of out.filter((c) => c && c.type === "colonnade")) {
                let n = this._finiteRuleNumber(col.columns);
                if (n != null) {
                    const lo = this._finiteRuleNumber(colR.columns_min);
                    const hi = this._finiteRuleNumber(colR.columns_max);
                    if (lo != null) n = Math.max(lo, n);
                    if (hi != null) n = Math.min(hi, n);
                    col.columns = Math.round(n);
                } else if (this._finiteRuleNumber(colR.columns_min) != null || this._finiteRuleNumber(colR.columns_max) != null) {
                    const lo = this._finiteRuleNumber(colR.columns_min) ?? 2;
                    const hi = this._finiteRuleNumber(colR.columns_max) ?? 48;
                    const base = Number(col.columns);
                    let v = Number.isFinite(base) ? base : lo;
                    v = Math.max(lo, Math.min(hi, v));
                    col.columns = Math.round(v);
                }
                if (col.radius != null) {
                    let r = Number(col.radius);
                    const rMin = this._finiteRuleNumber(colR.min_radius);
                    const rMax = this._finiteRuleNumber(colR.max_radius);
                    if (rMin != null) r = Math.max(rMin, r);
                    if (rMax != null) r = Math.min(rMax, r);
                    col.radius = r;
                }
                if (col.height != null && col.radius != null) {
                    let h = Number(col.height);
                    const r = Number(col.radius);
                    const ratio = this._finiteRuleNumber(colR.height_to_lower_diameter_ratio);
                    if (ratio != null) {
                        const slack = this._finiteRuleNumber(colR.ratio_slack) ?? 1;
                        h = Math.min(h, r * ratio * slack);
                    }
                    const capFrac = this._finiteRuleNumber(colR.max_shaft_height_fraction_of_min_span);
                    if (capFrac != null) h = Math.min(h, minSpan * capFrac);
                    const hMin = this._finiteRuleNumber(colR.min_shaft_height);
                    const hMax = this._finiteRuleNumber(colR.max_shaft_height);
                    if (hMin != null) h = Math.max(hMin, h);
                    if (hMax != null) h = Math.min(hMax, h);
                    col.height = h;
                } else if (col.height != null) {
                    let h = Number(col.height);
                    const hMin = this._finiteRuleNumber(colR.min_shaft_height);
                    const hMax = this._finiteRuleNumber(colR.max_shaft_height);
                    const capFrac = this._finiteRuleNumber(colR.max_shaft_height_fraction_of_min_span);
                    if (hMin != null) h = Math.max(hMin, h);
                    if (hMax != null) h = Math.min(hMax, h);
                    if (capFrac != null) h = Math.min(h, minSpan * capFrac);
                    col.height = h;
                }
            }
        }

        const cellaR = rules.cella;
        if (cellaR && typeof cellaR === "object") {
            const inset = this._finiteRuleNumber(cellaR.inset_per_side) ?? 0;
            const maxW0 = Math.max(0.05, w - inset * 2);
            const maxD0 = Math.max(0.05, d - inset * 2);
            const wf = this._finiteRuleNumber(cellaR.max_width_fraction);
            const df = this._finiteRuleNumber(cellaR.max_depth_fraction);
            const maxH = this._finiteRuleNumber(cellaR.max_height);
            for (const cella of out.filter((c) => c && c.type === "cella")) {
                if (cella.width != null) {
                    let cw = Number(cella.width);
                    if (wf != null) cw = Math.min(cw, maxW0 * wf);
                    cella.width = cw;
                }
                if (cella.depth != null) {
                    let cd = Number(cella.depth);
                    if (df != null) cd = Math.min(cd, maxD0 * df);
                    cella.depth = cd;
                }
                if (cella.height != null && maxH != null) {
                    cella.height = Math.min(Number(cella.height), maxH);
                }
            }
        }

        const podR = rules.podium;
        if (podR && typeof podR === "object") {
            for (const p of out.filter((c) => c && c.type === "podium")) {
                if (p.steps != null) {
                    let s = Math.round(Number(p.steps));
                    const smin = this._finiteRuleNumber(podR.steps_min);
                    const smax = this._finiteRuleNumber(podR.steps_max);
                    if (smin != null) s = Math.max(smin, s);
                    if (smax != null) s = Math.min(smax, s);
                    p.steps = s;
                }
                if (p.height != null) {
                    let h = Number(p.height);
                    const hMin = this._finiteRuleNumber(podR.min_height);
                    const hMax = this._finiteRuleNumber(podR.max_height);
                    if (hMin != null) h = Math.max(hMin, h);
                    if (hMax != null) h = Math.min(hMax, h);
                    p.height = h;
                }
            }
        }

        const domeR = rules.dome;
        if (domeR && typeof domeR === "object") {
            for (const dm of out.filter((c) => c && c.type === "dome")) {
                if (dm.radius != null) {
                    let rad = Number(dm.radius);
                    const rMin = this._finiteRuleNumber(domeR.min_radius);
                    const rMax = this._finiteRuleNumber(domeR.max_radius);
                    const rFrac = this._finiteRuleNumber(domeR.max_radius_fraction_of_min_span);
                    if (rMin != null) rad = Math.max(rMin, rad);
                    if (rMax != null) rad = Math.min(rMax, rad);
                    if (rFrac != null) rad = Math.min(rad, minSpan * rFrac);
                    dm.radius = rad;
                }
            }
        }

        const pedR = rules.pediment;
        if (pedR && typeof pedR === "object") {
            for (const ped of out.filter((c) => c && c.type === "pediment")) {
                if (ped.height != null) {
                    let h = Number(ped.height);
                    const hMax = this._finiteRuleNumber(pedR.max_height);
                    const hf = this._finiteRuleNumber(pedR.max_height_fraction_of_w);
                    if (hMax != null) h = Math.min(h, hMax);
                    if (hf != null) h = Math.min(h, w * hf);
                    ped.height = h;
                }
            }
        }

        const blockR = rules.block;
        if (blockR && typeof blockR === "object") {
            for (const b of out.filter((c) => c && c.type === "block")) {
                if (b.stories != null) {
                    let st = Math.round(Number(b.stories));
                    const smin = this._finiteRuleNumber(blockR.stories_min);
                    const smax = this._finiteRuleNumber(blockR.stories_max);
                    if (smin != null) st = Math.max(smin, st);
                    if (smax != null) st = Math.min(smax, st);
                    b.stories = st;
                }
                if (b.storyHeight != null) {
                    let sh = Number(b.storyHeight);
                    const shMin = this._finiteRuleNumber(blockR.min_story_height);
                    const shMax = this._finiteRuleNumber(blockR.max_story_height);
                    if (shMin != null) sh = Math.max(shMin, sh);
                    if (shMax != null) sh = Math.min(shMax, sh);
                    b.storyHeight = sh;
                }
                const aggMax = this._finiteRuleNumber(blockR.max_aggregate_height);
                if (aggMax != null && b.stories != null && b.storyHeight != null) {
                    const st = Number(b.stories);
                    let sh = Number(b.storyHeight);
                    if (st * sh > aggMax) b.storyHeight = aggMax / Math.max(1, st);
                }
            }
        }

        const wallR = rules.walls;
        if (wallR && typeof wallR === "object") {
            for (const wl of out.filter((c) => c && c.type === "walls")) {
                if (wl.height != null) {
                    let h = Number(wl.height);
                    const hMin = this._finiteRuleNumber(wallR.min_height);
                    const hMax = this._finiteRuleNumber(wallR.max_height);
                    if (hMin != null) h = Math.max(hMin, h);
                    if (hMax != null) h = Math.min(hMax, h);
                    wl.height = h;
                }
                if (wl.thickness != null) {
                    let t = Number(wl.thickness);
                    const tMin = this._finiteRuleNumber(wallR.min_thickness);
                    const tMax = this._finiteRuleNumber(wallR.max_thickness);
                    if (tMin != null) t = Math.max(tMin, t);
                    if (tMax != null) t = Math.min(tMax, t);
                    wl.thickness = t;
                }
            }
        }

        const applyMinMaxHeight = (type, key) => {
            const r = rules[key];
            if (!r || typeof r !== "object") return;
            const hMin = this._finiteRuleNumber(r.min_height);
            const hMax = this._finiteRuleNumber(r.max_height);
            for (const o of out.filter((c) => c && c.type === type)) {
                if (o.height == null) continue;
                let h = Number(o.height);
                if (hMin != null) h = Math.max(hMin, h);
                if (hMax != null) h = Math.min(hMax, h);
                o.height = h;
            }
        };
        applyMinMaxHeight("arcade", "arcade");
        applyMinMaxHeight("tiled_roof", "tiled_roof");
        applyMinMaxHeight("vault", "vault");
        applyMinMaxHeight("atrium", "atrium");
        applyMinMaxHeight("tier", "tier");
        applyMinMaxHeight("statue", "statue");

        const fountR = rules.fountain;
        if (fountR && typeof fountR === "object") {
            for (const f of out.filter((c) => c && c.type === "fountain")) {
                if (f.height != null) {
                    let h = Number(f.height);
                    const hMin = this._finiteRuleNumber(fountR.min_height);
                    const hMax = this._finiteRuleNumber(fountR.max_height);
                    if (hMin != null) h = Math.max(hMin, h);
                    if (hMax != null) h = Math.min(hMax, h);
                    f.height = h;
                }
                if (f.radius != null) {
                    let rad = Number(f.radius);
                    const rMin = this._finiteRuleNumber(fountR.min_radius);
                    const rMax = this._finiteRuleNumber(fountR.max_radius);
                    if (rMin != null) rad = Math.max(rMin, rad);
                    if (rMax != null) rad = Math.min(rMax, rad);
                    f.radius = rad;
                }
            }
        }

        return out;
    }

    _resolveStackRole(comp) {
        const VALID = WorldRenderer._VALID_STACK_ROLES;
        if (comp.stack_role) {
            if (!VALID.has(comp.stack_role)) {
                throw new Error(`Invalid stack_role ${JSON.stringify(comp.stack_role)}`);
            }
            return comp.stack_role;
        }
        if (comp.type === "procedural") {
            throw new Error("type procedural requires stack_role (foundation|structural|infill|roof|decorative|freestanding)");
        }
        const d = WorldRenderer._DEFAULT_STACK_ROLE[comp.type];
        if (!d) {
            throw new Error(`Unknown component type for renderer: ${JSON.stringify(comp.type)}`);
        }
        return d;
    }

    _invokeBuilder(group, comp, anchorY, w, d) {
        if (comp.type === "procedural") {
            return this._buildProcedural(group, comp, anchorY, w, d);
        }
        const method = WorldRenderer._BUILDER_METHODS[comp.type];
        if (!method) {
            throw new Error(`No builder for component type ${JSON.stringify(comp.type)}`);
        }
        if (comp.type === "statue" || comp.type === "fountain" || comp.type === "door") {
            return this[method](group, comp, anchorY);
        }
        return this[method](group, comp, anchorY, w, d);
    }

    /**
     * Generative geometry: primitive parts only. Positions are centers in tile-local space;
     * Y is added to anchorY. Each part requires color #RRGGBB (validated server-side).
     */
    _buildProcedural(group, comp, anchorY, w, d) {
        const parts = comp.parts;
        let maxTop = anchorY;
        const capSeg = (n, lo, hi) => Math.min(hi, Math.max(lo, Math.round(n) || lo));

        for (let i = 0; i < parts.length; i++) {
            const p = parts[i];
            const px = Number((p.position && p.position[0]) ?? 0);
            const py = Number((p.position && p.position[1]) ?? 0);
            const pz = Number((p.position && p.position[2]) ?? 0);
            if (![px, py, pz].every((n) => Number.isFinite(n))) {
                throw new Error(`procedural.parts[${i}]: position must be finite [x,y,z]`);
            }
            const mat = this._matPBR(p, p.color, 0.7, 0.02);
            let mesh;

            if (p.shape === "box") {
                let sx, sy, sz;
                if (Array.isArray(p.size) && p.size.length === 3) {
                    sx = Number(p.size[0]); sy = Number(p.size[1]); sz = Number(p.size[2]);
                } else {
                    sx = Number(p.width); sy = Number(p.height); sz = Number(p.depth);
                }
                if (![sx, sy, sz].every((n) => Number.isFinite(n) && n > 0)) {
                    throw new Error(`procedural.parts[${i}]: box needs positive size or width/height/depth`);
                }
                mesh = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), mat);
                mesh.position.set(px, anchorY + py, pz);
                maxTop = Math.max(maxTop, anchorY + py + sy / 2);
            } else if (p.shape === "cylinder") {
                const rBot = Number(p.radiusBottom ?? p.radius);
                const rTop = Number(p.radiusTop ?? p.radius);
                const h = Number(p.height);
                const seg = capSeg(p.radialSegments ?? 16, 6, 32);
                if (![rBot, rTop, h].every((n) => Number.isFinite(n) && n > 0)) {
                    throw new Error(`procedural.parts[${i}]: cylinder needs positive radius/height`);
                }
                mesh = new THREE.Mesh(new THREE.CylinderGeometry(rTop, rBot, h, seg), mat);
                mesh.position.set(px, anchorY + py, pz);
                maxTop = Math.max(maxTop, anchorY + py + h / 2);
            } else if (p.shape === "sphere") {
                const r = Number(p.radius);
                if (!Number.isFinite(r) || r <= 0) throw new Error(`procedural.parts[${i}]: sphere needs radius`);
                const ws = capSeg(p.widthSegments ?? 12, 6, 32);
                const hs = capSeg(p.heightSegments ?? 8, 4, 24);
                mesh = new THREE.Mesh(new THREE.SphereGeometry(r, ws, hs), mat);
                mesh.position.set(px, anchorY + py, pz);
                maxTop = Math.max(maxTop, anchorY + py + r);
            } else if (p.shape === "cone") {
                const r = Number(p.radius);
                const h = Number(p.height);
                const seg = capSeg(p.radialSegments ?? 12, 6, 32);
                if (![r, h].every((n) => Number.isFinite(n) && n > 0)) {
                    throw new Error(`procedural.parts[${i}]: cone needs positive radius and height`);
                }
                mesh = new THREE.Mesh(new THREE.ConeGeometry(r, h, seg), mat);
                mesh.position.set(px, anchorY + py, pz);
                maxTop = Math.max(maxTop, anchorY + py + h / 2);
            } else if (p.shape === "torus") {
                const R = Number(p.radius);
                const tube = Number(p.tube);
                if (![R, tube].every((n) => Number.isFinite(n) && n > 0)) {
                    throw new Error(`procedural.parts[${i}]: torus needs radius and tube`);
                }
                const rs = capSeg(p.radialSegments ?? 12, 6, 32);
                const ts = capSeg(p.tubularSegments ?? 24, 8, 48);
                mesh = new THREE.Mesh(new THREE.TorusGeometry(R, tube, rs, ts), mat);
                mesh.position.set(px, anchorY + py, pz);
                maxTop = Math.max(maxTop, anchorY + py + tube + R);
            } else if (p.shape === "plane") {
                const pw = Number(p.width);
                const ph = Number(p.height);
                if (![pw, ph].every((n) => Number.isFinite(n) && n > 0)) {
                    throw new Error(`procedural.parts[${i}]: plane needs width and height (XZ extent)`);
                }
                mesh = new THREE.Mesh(new THREE.PlaneGeometry(pw, ph), mat);
                mesh.rotation.x = -Math.PI / 2;
                mesh.position.set(px, anchorY + py, pz);
                maxTop = Math.max(maxTop, anchorY + py + 0.002);
            } else if (p.shape === "stacked_tower") {
                // Compound: tapered tower from stacked layers (shikhara, pagoda tier, minaret)
                // params: base_width, base_depth, height, layers (int), taper (0-1), color
                const bw = Number(p.base_width || p.width || 0.3);
                const bd = Number(p.base_depth || p.depth || bw);
                const th = Number(p.height || 0.5);
                const layers = Math.max(2, Math.min(12, Math.round(p.layers || 5)));
                const taper = Math.max(0.1, Math.min(0.95, Number(p.taper || 0.6)));
                const layerH = th / layers;
                for (let li = 0; li < layers; li++) {
                    const frac = 1 - (li / layers) * taper;
                    const lw = bw * frac;
                    const ld = bd * frac;
                    const layer = new THREE.Mesh(
                        new THREE.BoxGeometry(lw, layerH * 0.9, ld), mat
                    );
                    layer.position.set(px, anchorY + py + li * layerH + layerH / 2, pz);
                    layer.castShadow = true;
                    layer.receiveShadow = true;
                    group.add(layer);
                }
                maxTop = Math.max(maxTop, anchorY + py + th);
                mesh = null; // Already added layers
            } else if (p.shape === "tiered_pyramid") {
                // Compound: stepped pyramid (Mesoamerican, Khmer, ziggurat)
                // params: base_width, base_depth, height, steps (int), color
                const bw = Number(p.base_width || p.width || 0.8);
                const bd = Number(p.base_depth || p.depth || bw);
                const th = Number(p.height || 0.5);
                const steps = Math.max(2, Math.min(10, Math.round(p.steps || 4)));
                const stepH = th / steps;
                for (let si = 0; si < steps; si++) {
                    const frac = 1 - (si / steps) * 0.7;
                    const sw = bw * frac;
                    const sd = bd * frac;
                    const step = new THREE.Mesh(
                        new THREE.BoxGeometry(sw, stepH, sd), mat
                    );
                    step.position.set(px, anchorY + py + si * stepH + stepH / 2, pz);
                    step.castShadow = true;
                    step.receiveShadow = true;
                    group.add(step);
                }
                maxTop = Math.max(maxTop, anchorY + py + th);
                mesh = null;
            } else if (p.shape === "colonnade_ring") {
                // Compound: ring of columns (peristyle, Buddhist stupa railing, chhatri)
                // params: radius, height, column_count, column_radius, color
                const ringR = Number(p.radius || 0.2);
                const ringH = Number(p.height || 0.3);
                const nCols = Math.max(4, Math.min(24, Math.round(p.column_count || 8)));
                const colR = Number(p.column_radius || 0.012);
                for (let ci = 0; ci < nCols; ci++) {
                    const angle = (ci / nCols) * Math.PI * 2;
                    const cx = px + Math.cos(angle) * ringR;
                    const cz = pz + Math.sin(angle) * ringR;
                    const col = new THREE.Mesh(
                        new THREE.CylinderGeometry(colR, colR * 1.1, ringH, 6), mat
                    );
                    col.position.set(cx, anchorY + py + ringH / 2, cz);
                    col.castShadow = true;
                    group.add(col);
                }
                // Beam ring on top
                const beam = new THREE.Mesh(
                    new THREE.TorusGeometry(ringR, colR * 1.5, 6, nCols), mat
                );
                beam.rotation.x = -Math.PI / 2;
                beam.position.set(px, anchorY + py + ringH, pz);
                beam.castShadow = true;
                group.add(beam);
                maxTop = Math.max(maxTop, anchorY + py + ringH);
                mesh = null;
            } else if (p.shape === "water_channel") {
                // Compound: rectangular water channel with side walls
                // params: width, depth (length), height (wall height), water_color
                const cw = Number(p.width || 0.15);
                const cd = Number(p.depth || 0.6);
                const ch = Number(p.height || 0.04);
                const wallT = 0.01;
                const wallMat = mat;
                // Left wall
                const lw = new THREE.Mesh(new THREE.BoxGeometry(wallT, ch, cd), wallMat);
                lw.position.set(px - cw / 2, anchorY + py + ch / 2, pz);
                lw.castShadow = true; group.add(lw);
                // Right wall
                const rw = new THREE.Mesh(new THREE.BoxGeometry(wallT, ch, cd), wallMat);
                rw.position.set(px + cw / 2, anchorY + py + ch / 2, pz);
                rw.castShadow = true; group.add(rw);
                // Water surface
                const waterColor = p.water_color || "#2980b9";
                const wm = new THREE.MeshStandardMaterial({
                    color: waterColor, transparent: true, opacity: 0.8, roughness: 0.08
                });
                const ws = new THREE.Mesh(new THREE.BoxGeometry(cw - wallT * 2, 0.005, cd), wm);
                ws.position.set(px, anchorY + py + ch * 0.7, pz);
                ws.userData.isWater = true; group.add(ws);
                maxTop = Math.max(maxTop, anchorY + py + ch);
                mesh = null;
            } else if (p.shape === "arch") {
                // Compound: freestanding arch (torana, torii, triumphal arch)
                // params: width, height, thickness, pillar_width
                const aw = Number(p.width || 0.3);
                const ah = Number(p.height || 0.4);
                const at = Number(p.thickness || 0.04);
                const pw = Number(p.pillar_width || 0.04);
                // Left pillar
                const lp = new THREE.Mesh(new THREE.BoxGeometry(pw, ah * 0.75, at), mat);
                lp.position.set(px - aw / 2 + pw / 2, anchorY + py + ah * 0.375, pz);
                lp.castShadow = true; group.add(lp);
                // Right pillar
                const rp = new THREE.Mesh(new THREE.BoxGeometry(pw, ah * 0.75, at), mat);
                rp.position.set(px + aw / 2 - pw / 2, anchorY + py + ah * 0.375, pz);
                rp.castShadow = true; group.add(rp);
                // Arch curve
                const archR = (aw - pw * 2) / 2 * 0.85;
                const archMesh = new THREE.Mesh(
                    new THREE.TorusGeometry(archR, pw * 0.6, 6, 12, Math.PI), mat
                );
                archMesh.rotation.x = -Math.PI / 2;
                archMesh.position.set(px, anchorY + py + ah * 0.75, pz);
                archMesh.castShadow = true; group.add(archMesh);
                // Lintel
                const lintel = new THREE.Mesh(new THREE.BoxGeometry(aw, pw * 0.8, at * 1.2), mat);
                lintel.position.set(px, anchorY + py + ah * 0.75 + archR, pz);
                lintel.castShadow = true; group.add(lintel);
                maxTop = Math.max(maxTop, anchorY + py + ah);
                mesh = null;
            } else {
                throw new Error(`procedural.parts[${i}]: unknown shape ${JSON.stringify(p.shape)}`);
            }

            if (!mesh) continue; // Compound shapes already added their meshes above
            if (Array.isArray(p.rotation) && p.rotation.length >= 3) {
                mesh.rotation.x += Number(p.rotation[0]) || 0;
                mesh.rotation.y += Number(p.rotation[1]) || 0;
                mesh.rotation.z += Number(p.rotation[2]) || 0;
            }
            mesh.castShadow = true;
            mesh.receiveShadow = true;
            if (comp.component_id) mesh.userData.componentId = comp.component_id;
            group.add(mesh);
        }
        return maxTop;
    }

    _buildComponents(group, components, w, d, proportionRules) {
        const comps = this._applyGenerativeProportionRules(components, w, d, proportionRules);
        const buckets = {
            foundation: [], structural: [], infill: [], roof: [], decorative: [], freestanding: [],
        };
        for (const comp of comps) {
            if (!comp || typeof comp !== "object" || !comp.type) {
                throw new Error("Invalid component entry in spec.components");
            }
            const role = this._resolveStackRole(comp);
            buckets[role].push(comp);
        }
        const stackPri = (c) => (c.stack_priority != null && Number.isFinite(Number(c.stack_priority))
            ? Number(c.stack_priority)
            : 0);
        for (const k of Object.keys(buckets)) {
            buckets[k].sort((a, b) => stackPri(a) - stackPri(b));
        }

        let baseLevel = 0;
        let structuralTop = 0;

        for (const comp of buckets.foundation) {
            const topY = this._invokeBuilder(group, comp, baseLevel, w, d);
            baseLevel = Math.max(baseLevel, topY);
        }
        structuralTop = baseLevel;

        for (const comp of buckets.structural) {
            const topY = this._invokeBuilder(group, comp, baseLevel, w, d);
            structuralTop = Math.max(structuralTop, topY);
        }

        for (const comp of buckets.infill) {
            this._invokeBuilder(group, comp, baseLevel, w, d);
        }

        for (const comp of buckets.roof) {
            const topY = this._invokeBuilder(group, comp, structuralTop, w, d);
            structuralTop = Math.max(structuralTop, topY);
        }

        for (const comp of buckets.decorative) {
            this._invokeBuilder(group, comp, baseLevel, w, d);
        }

        for (const comp of buckets.freestanding) {
            const topY = this._invokeBuilder(group, comp, structuralTop, w, d);
            structuralTop = Math.max(structuralTop, topY);
        }
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
                this._matPBR(comp, color, 0.75)
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
        const stRaw = comp.style;
        if (!stRaw || typeof stRaw !== "string") {
            throw new Error("colonnade requires style: doric | ionic | corinthian");
        }
        const style = stRaw.toLowerCase();
        if (!["doric", "ionic", "corinthian"].includes(style)) {
            throw new Error(`colonnade style not supported: ${JSON.stringify(stRaw)}`);
        }
        const color = comp.color || "#e8e0d0";
        const r = comp.radius || Math.max(0.015, w / (numCols * 5));  // scale radius to footprint
        const peripteral = comp.peripteral !== false;
        const userRough = Number.isFinite(comp.roughness) ? Math.max(0.05, Math.min(1, comp.roughness)) : null;
        const userMetal = Number.isFinite(comp.metalness) ? Math.max(0, Math.min(1, comp.metalness)) : null;
        const shaftR = userRough !== null ? userRough : 0.3;
        const baseR = userRough !== null ? Math.min(0.98, userRough + 0.05) : 0.35;
        const capR = userRough !== null ? userRough : 0.3;
        const entR = userRough !== null ? Math.min(0.98, userRough + 0.08) : 0.35;
        const pbrM = userMetal !== null ? userMetal : 0.02;

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

        const n = positions.length;
        // Cap segments: very thin columns need few; thick columns cap at 28 for GPU cost
        const shaftSegs = Math.min(28, Math.max(8, Math.round(r * 200)));
        const shaftGeom = new THREE.CylinderGeometry(r * 0.83, r, colH, shaftSegs);
        const shaftMat = this._matPBR(Object.assign({}, comp, { roughness: shaftR, metalness: pbrM }), color, shaftR, pbrM);
        const shaftInst = new THREE.InstancedMesh(shaftGeom, shaftMat, n);
        shaftInst.castShadow = true;
        shaftInst.receiveShadow = true;
        const dummy = this._instDummy;

            if (baseH > 0) {
            const baseGeom = new THREE.CylinderGeometry(r + 0.01, r + 0.015, baseH, 8);
            const baseMat = this._matPBR(Object.assign({}, comp, { roughness: baseR, metalness: pbrM }), color, baseR, pbrM);
            const baseInst = new THREE.InstancedMesh(baseGeom, baseMat, n);
            baseInst.castShadow = true;
            baseInst.receiveShadow = true;
            for (let i = 0; i < n; i++) {
                const pos = positions[i];
                dummy.position.set(pos.x, baseY + baseH / 2, pos.z);
                dummy.rotation.set(0, 0, 0);
                dummy.scale.set(1, 1, 1);
                dummy.updateMatrix();
                baseInst.setMatrixAt(i, dummy.matrix);
            }
            baseInst.instanceMatrix.needsUpdate = true;
            group.add(baseInst);
        }

        for (let i = 0; i < n; i++) {
            const pos = positions[i];
            dummy.position.set(pos.x, baseY + baseH + colH / 2, pos.z);
            dummy.rotation.set(0, 0, 0);
            dummy.scale.set(1, 1, 1);
            dummy.updateMatrix();
            shaftInst.setMatrixAt(i, dummy.matrix);
        }
        shaftInst.instanceMatrix.needsUpdate = true;
        group.add(shaftInst);

        const capMat = this._matPBR(Object.assign({}, comp, { roughness: capR, metalness: pbrM }), color, capR, pbrM);
            if (style === "corinthian") {
            const c1Geom = new THREE.BoxGeometry(capW * 0.7, capH * 0.6, capW * 0.7);
            const c2Geom = new THREE.BoxGeometry(capW, capH * 0.4, capW);
            const c1Inst = new THREE.InstancedMesh(c1Geom, capMat, n);
            const c2Inst = new THREE.InstancedMesh(c2Geom, capMat, n);
            c1Inst.castShadow = c2Inst.castShadow = true;
            c1Inst.receiveShadow = c2Inst.receiveShadow = true;
            for (let i = 0; i < n; i++) {
                const pos = positions[i];
                dummy.position.set(pos.x, baseY + baseH + colH + capH * 0.3, pos.z);
                dummy.updateMatrix();
                c1Inst.setMatrixAt(i, dummy.matrix);
                dummy.position.set(pos.x, baseY + baseH + colH + capH * 0.8, pos.z);
                dummy.updateMatrix();
                c2Inst.setMatrixAt(i, dummy.matrix);
            }
            c1Inst.instanceMatrix.needsUpdate = true;
            c2Inst.instanceMatrix.needsUpdate = true;
            group.add(c1Inst, c2Inst);
            } else {
            const capGeom = new THREE.BoxGeometry(capW, capH, capW);
            const capInst = new THREE.InstancedMesh(capGeom, capMat, n);
            capInst.castShadow = true;
            capInst.receiveShadow = true;
            for (let i = 0; i < n; i++) {
                const pos = positions[i];
                dummy.position.set(pos.x, baseY + baseH + colH + capH / 2, pos.z);
                dummy.updateMatrix();
                capInst.setMatrixAt(i, dummy.matrix);
            }
            capInst.instanceMatrix.needsUpdate = true;
            group.add(capInst);
        }

        // Entablature
        const entH = 0.05;
        const ent = new THREE.Mesh(
            new THREE.BoxGeometry(w + 0.02, entH, d + 0.02),
            this._matPBR(Object.assign({}, comp, { roughness: entR, metalness: pbrM }), color, entR, pbrM)
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
        group.add(new THREE.Mesh(geo, this._matPBR(comp, color, 0.65)));

        // Ridge beam
        const ridge = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.02, d + 0.02), this._matPBR(comp, color, 0.65));
        ridge.position.set(0, baseY + peakH, 0);
        group.add(ridge);

        return baseY + peakH;
    }

    // Hemisphere dome
    _buildDome(group, comp, baseY, w, d) {
        const r = comp.radius || Math.min(w, d) * 0.4;
        const color = comp.color || "#8b7355";

        // Drum (cylindrical base for the dome — architectural standard)
        const drumH = r * 0.35;
        const drumR = r * 1.02;
        const drum = new THREE.Mesh(
            new THREE.CylinderGeometry(drumR, drumR * 1.04, drumH, 16),
            this._matPBR(comp, color, 0.65)
        );
        drum.position.y = baseY + drumH / 2;
        group.add(drum);

        // Dome hemisphere sits on the drum
        const dome = new THREE.Mesh(
            new THREE.SphereGeometry(r, 16, 10, 0, Math.PI * 2, 0, Math.PI / 2),
            this._matPBR(comp, color, 0.4)
        );
        dome.position.y = baseY + drumH;
        group.add(dome);

        // Oculus ring at top
        const oculus = new THREE.Mesh(
            new THREE.TorusGeometry(r * 0.12, 0.012, 6, 12),
            this._mat("#e8e0d0", 0.28, 0.12)
        );
        oculus.rotation.x = -Math.PI / 2;
        oculus.position.y = baseY + drumH + r - 0.01;
        group.add(oculus);

        // Finial (small sphere/cone at apex — common across cultures)
        const finial = new THREE.Mesh(
            new THREE.SphereGeometry(r * 0.06, 8, 6),
            this._mat("#DAA520", 0.3, 0.2)
        );
        finial.position.y = baseY + drumH + r + r * 0.04;
        group.add(finial);

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
                this._matPBR(comp, color, 0.72)
            );
            wall.position.y = baseY + s * storyH + storyH / 2;
            group.add(wall);

            // Windows on front and back — scale to footprint
            const winW = Math.max(0.03, sw * 0.04);
            const winH = storyH * 0.35;
            const numWin = comp.windows || Math.max(1, Math.floor(sw / (winW * 3.5)));
            const winSpacing = sw / (numWin + 1);

            const winMat = this._mat(windowColor, 0.38, 0.12);
            for (let wi = 0; wi < numWin; wi++) {
                const wx = -sw / 2 + winSpacing * (wi + 1);
                const wy = baseY + s * storyH + storyH * 0.55;

                // Front windows
                const wf = new THREE.Mesh(new THREE.BoxGeometry(winW, winH, 0.02), winMat);
                wf.position.set(wx, wy, -sd / 2 - 0.005);
                group.add(wf);

                // Back windows
                const wb = new THREE.Mesh(new THREE.BoxGeometry(winW, winH, 0.02), winMat);
                wb.position.set(wx, wy, sd / 2 + 0.005);
                group.add(wb);
            }

            // Side windows (left and right) — fewer, proportional to depth
            const sideWinCount = Math.max(1, Math.floor(numWin * (sd / sw)));
            const sideWinSpacing = sd / (sideWinCount + 1);
            for (let wi = 0; wi < sideWinCount; wi++) {
                const wz = -sd / 2 + sideWinSpacing * (wi + 1);
                const wy = baseY + s * storyH + storyH * 0.55;
                const wl = new THREE.Mesh(new THREE.BoxGeometry(0.02, winH, winW), winMat);
                wl.position.set(-sw / 2 - 0.005, wy, wz);
                group.add(wl);
                const wr = new THREE.Mesh(new THREE.BoxGeometry(0.02, winH, winW), winMat);
                wr.position.set(sw / 2 + 0.005, wy, wz);
                group.add(wr);
            }

            // Floor line between stories
            if (s > 0) {
                const ledge = new THREE.Mesh(
                    new THREE.BoxGeometry(sw + 0.02, 0.015, sd + 0.02),
                    this._matPBR(comp, color, 0.72)
                );
                ledge.position.y = baseY + s * storyH;
                group.add(ledge);
            }
        }
        // Flat roof cap (parapet ledge) — prevents naked top
        const capH = 0.02;
        const cap = new THREE.Mesh(
            new THREE.BoxGeometry(w + 0.03, capH, d + 0.03),
            this._matPBR(comp, color, 0.65)
        );
        cap.position.y = baseY + totalH + capH / 2;
        group.add(cap);

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
                this._matPBR(comp, color, 0.72)
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
                    this._matPBR(comp, color, 0.72)
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
            this._matPBR(comp, color, 0.72)
        );
        beam.position.y = baseY + pillarH + archR + beamH / 2;
        group.add(beam);

        return baseY + pillarH + archR + beamH;
    }

    // Angled tile roof
    _buildTiledRoof(group, comp, baseY, w, d) {
        const color = comp.color || "#b5651d";
        const peakH = comp.height || w * 0.2;
        const slopeAngle = Math.atan2(peakH, d * 0.5);
        const slopeLen = Math.hypot(peakH, d * 0.5);

        // Two sloped surfaces
        for (const side of [-1, 1]) {
            const slope = new THREE.Mesh(
                new THREE.BoxGeometry(w + 0.04, 0.025, slopeLen * 0.52),
                this._matPBR(comp, color, 0.75)
            );
            slope.position.set(0, baseY + peakH * 0.5, side * d * 0.22);
            slope.rotation.x = side * slopeAngle * 0.65;
            group.add(slope);
        }

        // Ridge beam along the peak
        const ridge = new THREE.Mesh(
            new THREE.BoxGeometry(w + 0.06, 0.035, 0.04),
            this._matPBR(comp, color, 0.6)
        );
        ridge.position.y = baseY + peakH;
        group.add(ridge);

        // Eave overhang on both sides
        for (const side of [-1, 1]) {
            const eave = new THREE.Mesh(
                new THREE.BoxGeometry(w + 0.08, 0.015, 0.06),
                this._matPBR(comp, color, 0.8)
            );
            eave.position.set(0, baseY + 0.01, side * (d * 0.5 + 0.02));
            group.add(eave);
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

        const lf = new THREE.Mesh(new THREE.BoxGeometry(halfW, wallH, t), this._matPBR(comp, color, 0.72));
        lf.position.set(-w / 2 + halfW / 2, baseY + wallH / 2, -d / 2 + t / 2);
        group.add(lf);

        const rf = new THREE.Mesh(new THREE.BoxGeometry(halfW, wallH, t), this._matPBR(comp, color, 0.72));
        rf.position.set(w / 2 - halfW / 2, baseY + wallH / 2, -d / 2 + t / 2);
        group.add(rf);

        const bw = new THREE.Mesh(new THREE.BoxGeometry(w, wallH, t), this._matPBR(comp, color, 0.72));
        bw.position.set(0, baseY + wallH / 2, d / 2 - t / 2);
        group.add(bw);

        const lw = new THREE.Mesh(new THREE.BoxGeometry(t, wallH, d), this._matPBR(comp, color, 0.72));
        lw.position.set(-w / 2 + t / 2, baseY + wallH / 2, 0);
        group.add(lw);

        const rw = new THREE.Mesh(new THREE.BoxGeometry(t, wallH, d), this._matPBR(comp, color, 0.72));
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
        const figMat = this._matPBR(comp, color, 0.45, 0.08);

        // Pedestal — varied shape based on optional comp.shape
        const pedW = 0.12;
        const ped = new THREE.Mesh(new THREE.BoxGeometry(pedW, pedH, pedW), this._matPBR(comp, pedColor, 0.82));
        ped.position.y = baseY + pedH / 2;
        group.add(ped);
        // Pedestal cap
        const pedCap = new THREE.Mesh(new THREE.BoxGeometry(pedW + 0.02, 0.01, pedW + 0.02), this._matPBR(comp, pedColor, 0.75));
        pedCap.position.y = baseY + pedH;
        group.add(pedCap);

        const shape = comp.shape || "figure";
        if (shape === "obelisk" || shape === "column") {
            // Tall tapered column/obelisk
            const obelisk = new THREE.Mesh(
                new THREE.CylinderGeometry(0.025, 0.04, figH * 1.4, 4),
                figMat
            );
            obelisk.position.y = baseY + pedH + figH * 0.7;
            obelisk.rotation.y = Math.PI / 4;
            group.add(obelisk);
        } else if (shape === "equestrian") {
            // Horse + rider silhouette (box body + cylinder legs)
            const body = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.05, 0.04), figMat);
            body.position.y = baseY + pedH + figH * 0.35;
            group.add(body);
            // Rider
            const rider = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.025, figH * 0.4, 6), figMat);
            rider.position.y = baseY + pedH + figH * 0.6;
            group.add(rider);
            const rHead = new THREE.Mesh(new THREE.SphereGeometry(headR, 8, 6), figMat);
            rHead.position.y = baseY + pedH + figH * 0.85;
            group.add(rHead);
        } else {
            // Default standing figure
            const body = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.045, figH, 8), figMat);
            body.position.y = baseY + pedH + figH / 2;
            group.add(body);
            // Shoulders
            const shoulders = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.02, 0.04), figMat);
            shoulders.position.y = baseY + pedH + figH * 0.85;
            group.add(shoulders);
            const head = new THREE.Mesh(new THREE.SphereGeometry(headR, 8, 6), figMat);
            head.position.y = baseY + pedH + figH + headR;
            group.add(head);
        }

        return baseY + totalH;
    }

    // Circular basin with water
    _buildFountain(group, comp, baseY) {
        const r = comp.radius || 0.15;
        const h = comp.height || 0.25;
        const color = comp.color || "#a0968a";

        // Basin outer rim
        const basin = new THREE.Mesh(
            new THREE.CylinderGeometry(r, r - 0.02, h * 0.35, 16),
            this._matPBR(comp, color, 0.75)
        );
        basin.position.y = baseY + h * 0.175;
        group.add(basin);

        // Basin lip (decorative ring)
        const lip = new THREE.Mesh(
            new THREE.TorusGeometry(r - 0.005, 0.012, 6, 16),
            this._matPBR(comp, color, 0.6)
        );
        lip.rotation.x = -Math.PI / 2;
        lip.position.y = baseY + h * 0.35;
        group.add(lip);

        // Water surface — more visible (higher opacity, normal map for ripples)
        const waterMat = new THREE.MeshStandardMaterial({
            color: 0x2980b9, transparent: true, opacity: 0.85,
            roughness: 0.08, metalness: 0.05,
        });
        if (this._terrainNormalMap) {
            waterMat.normalMap = this._terrainNormalMap;
            waterMat.normalScale = new THREE.Vector2(0.12, 0.12);
        }
        const water = new THREE.Mesh(
            new THREE.CylinderGeometry(r * 0.85, r * 0.85, 0.02, 16),
            waterMat
        );
        water.position.y = baseY + h * 0.32;
        water.userData.isWater = true;
        group.add(water);

        // Central spout column
        const col = new THREE.Mesh(
            new THREE.CylinderGeometry(0.012, 0.018, h * 0.75, 8),
            this._matPBR(comp, color, 0.55)
        );
        col.position.y = baseY + h * 0.35 + h * 0.35;
        group.add(col);

        // Spout cap (small sphere at top)
        const cap = new THREE.Mesh(
            new THREE.SphereGeometry(0.018, 8, 6),
            this._matPBR(comp, color, 0.4)
        );
        cap.position.y = baseY + h * 0.35 + h * 0.72;
        group.add(cap);

        return baseY + h;
    }

    // Shade canopy (decorative — does not advance Y)
    _buildAwning(group, comp, baseY, w, d) {
        const color = comp.color || "#cc3333";
        const awning = new THREE.Mesh(
            new THREE.BoxGeometry(w * 0.85, 0.02, d * 0.4),
            this._matPBR(comp, color, 0.75)
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
                    const m = new THREE.Mesh(new THREE.BoxGeometry(merlonW, merlonH, 0.04), this._matPBR(comp, color, 0.72));
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
            this._matPBR(comp, color, 0.68)
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
            this._matPBR(comp, color, 0.55)
        );
        door.position.set(comp.x || 0, baseY + doorH / 2, comp.z || 0);
        group.add(door);

        // Arch above door
        const archR = doorW * 0.6;
        const arch = new THREE.Mesh(
            new THREE.TorusGeometry(archR, 0.01, 6, 8, Math.PI),
            this._matPBR(comp, comp.frameColor || "#8a7e6e", 0.45)
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
                    this._matPBR(comp, color, 0.4)
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
        group.add(new THREE.Mesh(geo, this._matPBR(comp, color, 0.72)));

        return baseY + vaultH;
    }

    // Flat slab roof
    _buildFlatRoof(group, comp, baseY, w, d) {
        const color = comp.color || "#c8b88a";
        const overhang = comp.overhang || 0.04;
        const thickness = 0.04;

        const roof = new THREE.Mesh(
            new THREE.BoxGeometry(w + overhang, thickness, d + overhang),
            this._matPBR(comp, color, 0.72)
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
            this._matPBR(comp, color, 0.72)
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

        const front = new THREE.Mesh(new THREE.BoxGeometry(w, h, t), this._matPBR(comp, color, 0.72));
        front.position.set(0, baseY + h / 2, -d / 2 + t / 2);
        group.add(front);

        const back = new THREE.Mesh(new THREE.BoxGeometry(w, h, t), this._matPBR(comp, color, 0.72));
        back.position.set(0, baseY + h / 2, d / 2 - t / 2);
        group.add(back);

        const left = new THREE.Mesh(new THREE.BoxGeometry(t, h, d), this._matPBR(comp, color, 0.72));
        left.position.set(-w / 2 + t / 2, baseY + h / 2, 0);
        group.add(left);

        const right = new THREE.Mesh(new THREE.BoxGeometry(t, h, d), this._matPBR(comp, color, 0.72));
        right.position.set(w / 2 - t / 2, baseY + h / 2, 0);
        group.add(right);

        return baseY + h;
    }

    // ─── Hover / Click ───

    _getMeshList() {
        if (this._meshListDirty) {
            this._meshList = [];
            this.buildingGroups.forEach(g => g.traverse(c => { if (c.isMesh) this._meshList.push(c); }));
            this._meshListDirty = false;
        }
        return this._meshList;
    }

    _updateHover(e) {
        const rect = this.renderer3d.domElement.getBoundingClientRect();
        this.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        this.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        this.raycaster.setFromCamera(this.mouse, this.camera);
        const hits = this.raycaster.intersectObjects(this._getMeshList());

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
                    const name = tile.building_name || tile.terrain;
                    const type = tile.building_type ? ` (${tile.building_type})` : '';
                    const desc = tile.description ? `\n${tile.description.substring(0, 80)}...` : '';
                    tooltip.innerHTML = `<strong>${name}</strong>${type}${desc}`;
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
        const hits = this.raycaster.intersectObjects(this._getMeshList());

        // Triple-click detection (3 clicks within 600ms)
        const now = Date.now();
        this._clickTimes.push(now);
        if (this._clickTimes.length > 3) this._clickTimes.shift();
        const isTripleClick = this._clickTimes.length === 3 && (now - this._clickTimes[0]) < 600;

        if (hits.length > 0) {
            const tile = hits[0].object.userData.tile;
            if (tile) {
                this.renderer3d.domElement.dispatchEvent(new CustomEvent("tileclick", { detail: { x: tile.x, y: tile.y, tile } }));
                // Triple-click: zoom camera close to the building
                if (isTripleClick && tile.terrain !== "empty") {
                    this._clickTimes = [];
                    this._zoomToTile(tile);
                }
            }
        }
    }

    /** Smoothly zoom the camera close to a specific tile/building. */
    _zoomToTile(tile) {
        const S = TILE_SIZE;
        // Find the building group for multi-tile buildings
        const anchor = tile.spec && tile.spec.anchor;
        const ax = anchor ? anchor.x : tile.x;
        const ay = anchor ? anchor.y : tile.y;
        const key = `${ax},${ay}`;
        const group = this.buildingGroups.get(key);

        let cx, cz, targetDist;
        if (group) {
            // Center on the group's bounding box center
            const box = new THREE.Box3().setFromObject(group);
            const center = box.getCenter(new THREE.Vector3());
            cx = center.x;
            cz = center.z;
            // Distance based on building size
            const size = box.getSize(new THREE.Vector3());
            targetDist = Math.max(size.x, size.y, size.z) * 2.5;
        } else {
            cx = (tile.x + 0.5) * S;
            cz = (tile.y + 0.5) * S;
            targetDist = S * 4;
        }
        targetDist = this._clampCameraDistance(Math.max(targetDist, 30));

        // Animate to the target
        this._flyTarget = { x: cx, z: cz };
        this._flyStart = Date.now();
        this._flyFrom = { x: this.cameraTarget.x, z: this.cameraTarget.z };
        this._flyDistTarget = targetDist;
        this._flyDistFrom = this.cameraDistance;
    }

    _animate() {
        requestAnimationFrame(() => this._animate());
        const now = Date.now();

        // Keyboard: WASD pan; arrows + Q/E = orbit yaw + pitch; +/− zoom; R/F fine zoom
        if (this._keysDown && this._keysDown.size > 0) {
            const sm = this.cameraSpeedMultiplier || 1.0;
            const panSpeed = this.cameraDistance * 0.008 * sm;
            const rightX = Math.sin(this.cameraAngle);
            const rightZ = -Math.cos(this.cameraAngle);
            const fwdX = -Math.cos(this.cameraAngle);
            const fwdZ = -Math.sin(this.cameraAngle);
            let rx = 0, rz = 0;
            if (this._keysDown.has("w")) { rx += fwdX; rz += fwdZ; }
            if (this._keysDown.has("s")) { rx -= fwdX; rz -= fwdZ; }
            if (this._keysDown.has("a")) { rx -= rightX; rz -= rightZ; }
            if (this._keysDown.has("d")) { rx += rightX; rz += rightZ; }
            if (rx || rz) { this.cameraTarget.x += rx * panSpeed; this.cameraTarget.z += rz * panSpeed; }
            // Q/E: yaw orbit (same as Rotate buttons and ◀ / ▶)
            const orbitSpeed = 0.02 * sm;
            if (this._keysDown.has("q")) this.cameraAngle += orbitSpeed;
            if (this._keysDown.has("e")) this.cameraAngle -= orbitSpeed;
            // ArrowLeft/Right = orbit yaw
            if (this._keysDown.has("arrowleft")) this.cameraAngle += orbitSpeed;
            if (this._keysDown.has("arrowright")) this.cameraAngle -= orbitSpeed;
            // ArrowUp/Down = pitch (same as floating Camera ▲ / ▼)
            if (this._keysDown.has("arrowup")) {
                this.cameraPitch = Math.max(0.05, Math.min(1.4, this.cameraPitch + orbitSpeed));
            }
            if (this._keysDown.has("arrowdown")) {
                this.cameraPitch = Math.max(0.05, Math.min(1.4, this.cameraPitch - orbitSpeed));
            }
            // Space: raise orbit target (world Y); C: lower
            const liftSpeed = this.cameraDistance * 0.01 * sm;
            if (this._keysDown.has("space")) {
                this.cameraTarget.y += liftSpeed;
            }
            if (this._keysDown.has("c")) {
                this.cameraTarget.y -= liftSpeed;
            }
            // +/− and = (US keyboard): same factors as Camera panel zoom buttons (~4% per step)
            if (this._keysDown.has("+") || this._keysDown.has("=")) {
                this.cameraDistance = this._clampCameraDistance(this.cameraDistance * 0.96);
            }
            if (this._keysDown.has("-")) {
                this.cameraDistance = this._clampCameraDistance(this.cameraDistance * 1.04);
            }
            // R/F: smaller per-frame zoom (legacy)
            if (this._keysDown.has("r")) this.cameraDistance = this._clampCameraDistance(this.cameraDistance * 0.97);
            if (this._keysDown.has("f")) this.cameraDistance = this._clampCameraDistance(this.cameraDistance * 1.03);
            this._updateCamera();
        }
        // Animate only groups that are currently dropping in
        for (const group of this._animatingGroups) {
            const t = Math.min(1, (now - group.userData.animStart) / 600);
            const ease = 1 - Math.pow(1 - t, 3);
            group.position.y = group.userData.animStartY + (group.userData.animTargetY - group.userData.animStartY) * ease;
            if (t >= 1) { delete group.userData.animStart; this._animatingGroups.delete(group); }
        }
        // Animate only tracked water meshes (river/harbor tiles)
        {
            const waveT = now * 0.001; // seconds-ish for wave calc
            for (const c of this._waterMeshes) {
                const gp = c.parent;
                // Original shimmer + subtle Y oscillation with wave propagation offset
                const phase = gp.position.x * 0.15 + gp.position.z * 0.2;
                c.position.y = -0.03
                    + Math.sin(now * 0.002 + gp.position.x * 2 + gp.position.z * 3) * 0.012
                    + Math.sin(waveT * 0.3 * Math.PI * 2 + phase) * 0.15;
            }
        }
        // Animate distant water plane — gentle roughness shimmer + subtle wave
        if (this._waterPlane) {
            const wSec = now * 0.001;
            const sine = Math.sin(wSec * 0.3 * Math.PI * 2); // ~0.3 Hz
            // Roughness oscillates between 0.15 and 0.30
            this._waterPlane.material.roughness = 0.225 + sine * 0.075;
            // Gentle Y oscillation ±0.15 world units
            this._waterPlane.position.y = this._waterPlaneBaseY + sine * 0.15;
        }
        if (this._flyTarget) {
            const t = Math.min(1, (now - this._flyStart) / 1500);
            const ease = 1 - Math.pow(1 - t, 3);
            this.cameraTarget.x = this._flyFrom.x + (this._flyTarget.x - this._flyFrom.x) * ease;
            this.cameraTarget.z = this._flyFrom.z + (this._flyTarget.z - this._flyFrom.z) * ease;
            // Also animate zoom distance if triple-click set a target distance
            if (this._flyDistTarget != null && this._flyDistFrom != null) {
                this.cameraDistance = this._clampCameraDistance(
                    this._flyDistFrom + (this._flyDistTarget - this._flyDistFrom) * ease
                );
            }
            this._updateCamera();
            if (t >= 1) {
                this._flyTarget = null;
                this._flyDistTarget = null;
                this._flyDistFrom = null;
            }
        }

        this._projScreenMatrix.multiplyMatrices(this.camera.projectionMatrix, this.camera.matrixWorldInverse);
        this._frustum.setFromProjectionMatrix(this._projScreenMatrix);
        this.buildingGroups.forEach((group) => {
            if (group.userData.cullRadius == null) return;
            this._cullCenter.set(
                group.position.x,
                group.position.y + (group.userData.cullCenterOffsetY || 0),
                group.position.z
            );
            this._cullSphere.set(this._cullCenter, group.userData.cullRadius);
            group.visible = this._frustum.intersectsSphere(this._cullSphere);
        });

        this.renderer3d.render(this.scene, this.camera);
    }
}
