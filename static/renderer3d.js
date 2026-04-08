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
        hipped_roof: "roof",
        vault: "roof",
        door: "decorative",
        pilasters: "decorative",
        awning: "decorative",
        battlements: "decorative",
        staircase: "decorative",
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
        hipped_roof: "_buildHippedRoof",
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
        staircase: "_buildStaircase",
    };

    constructor(container) {
        this.container = container;
        this.grid = null;
        this.width = 0;
        this.height = 0;
        this.minX = 0;
        this.minY = 0;
        this.buildingGroups = new Map();
        this.raycaster = new THREE.Raycaster();
        this.mouse = new THREE.Vector2();
        this.hoveredGroup = null;
        this._meshList = [];
        this._meshListDirty = true;
        this._waterMeshes = [];
        this._animatingGroups = new Set();
        this._constructingGroups = new Set();
        // Spatial index: anchor key → footprint bounds (maintained on tile update)
        this._anchorFootprints = new Map();
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

        // Post-processing pipeline — graceful degradation if addons fail to load
        this.composer = null;
        this._setupPostProcessing();

        this._setupIblAndBackground();
        const terrainDetail = this._createTerrainDetailMaps();
        this._terrainRoughnessMap = terrainDetail.roughnessMap;
        this._terrainNormalMap = terrainDetail.normalMap;

        // Procedural canvas texture cache
        this._canvasTextureCache = new Map();

        // Dynamic lighting system — supports time-of-day via setTimeOfDay(0-1)
        this._ambientLight = new THREE.AmbientLight(0xfff5e6, 0.36);
        this.scene.add(this._ambientLight);
        const sun = new THREE.DirectionalLight(0xfff0d0, 0.92);
        sun.position.set(400, 500, 250);
        sun.castShadow = true;
        sun.shadow.mapSize.set(4096, 4096);
        sun.shadow.normalBias = 0.04;
        sun.shadow.bias = -0.0004;
        const sc = sun.shadow.camera;
        sc.near = 1;
        sc.far = 8000;
        sc.left = -1200;
        sc.right = 1200;
        sc.top = 1200;
        sc.bottom = -1200;
        this.scene.add(sun);
        this._sunLight = sun;
        this._hemiLight = new THREE.HemisphereLight(0xa8d4f0, 0x7a6e52, 0.42);
        this.scene.add(this._hemiLight);
        this._fillLight = new THREE.DirectionalLight(0xffd4a0, 0.1);
        this._fillLight.position.set(-200, 50, -100);
        this.scene.add(this._fillLight);
        // Secondary fill from opposite side — softens harsh shadow areas
        this._fillLight2 = new THREE.DirectionalLight(0xc8d8f0, 0.08);
        this._fillLight2.position.set(300, 80, -200);
        this._fillLight2.castShadow = false;
        this.scene.add(this._fillLight2);
        const rimLight = new THREE.DirectionalLight(0xe8e2f8, 0.13);
        rimLight.position.set(-380, 120, -420);
        rimLight.castShadow = false;
        this.scene.add(rimLight);
        this._timeOfDay = 0.35; // Default: late morning

        // Particle system — dust motes for atmosphere
        this._dustParticles = null;
        this._dustVelocities = null;

        // District label sprites — floating text above each district center
        this._districtLabelsGroup = new THREE.Group();
        this._districtLabelsGroup.name = "districtLabels";
        this.scene.add(this._districtLabelsGroup);
        this._districtLabelsVisible = true;

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
        /** Camera speed multiplier — adjustable from UI (0.1 .. 1.5, default 0.5) */
        this.cameraSpeedMultiplier = 0.5;
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
            // Shift+L: toggle district labels
            if (e.shiftKey && e.key.toLowerCase() === "l") {
                this.toggleDistrictLabels();
                e.preventDefault();
                return;
            }
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
        if (this.composer) {
            this.composer.setSize(w, h);
            // Update FXAA resolution uniform
            if (this._fxaaPass && this._fxaaPass.material && this._fxaaPass.material.uniforms) {
                const pixelRatio = this.renderer3d.getPixelRatio();
                this._fxaaPass.material.uniforms['resolution'].value.set(
                    1 / (w * pixelRatio), 1 / (h * pixelRatio)
                );
            }
        }
    }

    // ─── Post-Processing Pipeline ───

    _setupPostProcessing() {
        if (this._failed || !this.renderer3d) return;
        // Graceful degradation: skip if addons did not load
        if (typeof THREE.EffectComposer !== "function" ||
            typeof THREE.RenderPass !== "function" ||
            typeof THREE.ShaderPass !== "function") {
            console.warn("Post-processing addons not loaded — rendering without post-processing");
            return;
        }
        try {
            const w = this.container.clientWidth;
            const h = this.container.clientHeight;
            const pixelRatio = this.renderer3d.getPixelRatio();

            this.composer = new THREE.EffectComposer(this.renderer3d);

            // 1. RenderPass — base scene
            const renderPass = new THREE.RenderPass(this.scene, this.camera);
            this.composer.addPass(renderPass);

            // 2. UnrealBloomPass — subtle glow on sunlit marble and bronze
            if (typeof THREE.UnrealBloomPass === "function") {
                const bloomPass = new THREE.UnrealBloomPass(
                    new THREE.Vector2(w, h),
                    0.12,  // strength (subtle)
                    0.3,   // radius
                    0.92   // threshold (only brightest surfaces)
                );
                this.composer.addPass(bloomPass);
                this._bloomPass = bloomPass;
            }

            // 3. Custom color grading ShaderPass — warm Mediterranean tint
            const ColorGradeShader = {
                uniforms: {
                    tDiffuse: { value: null },
                    shadowTint: { value: new THREE.Vector3(1.01, 0.98, 0.94) },  // very subtle warm shadows
                    highlightTint: { value: new THREE.Vector3(1.0, 0.99, 0.95) }, // neutral highlights
                },
                vertexShader: `
                    varying vec2 vUv;
                    void main() {
                        vUv = uv;
                        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
                    }
                `,
                fragmentShader: `
                    uniform sampler2D tDiffuse;
                    uniform vec3 shadowTint;
                    uniform vec3 highlightTint;
                    varying vec2 vUv;
                    void main() {
                        vec4 texel = texture2D(tDiffuse, vUv);
                        float luminance = dot(texel.rgb, vec3(0.299, 0.587, 0.114));
                        // Blend between shadow tint (dark areas) and highlight tint (bright areas)
                        vec3 tint = mix(shadowTint, highlightTint, smoothstep(0.2, 0.7, luminance));
                        gl_FragColor = vec4(texel.rgb * tint, texel.a);
                    }
                `,
            };
            const colorGradePass = new THREE.ShaderPass(ColorGradeShader);
            this.composer.addPass(colorGradePass);
            this._colorGradePass = colorGradePass;

            // 4. Vignette ShaderPass — subtle radial darkening at edges
            const VignetteShader = {
                uniforms: {
                    tDiffuse: { value: null },
                    darkness: { value: 0.35 },
                    offset: { value: 1.2 },
                },
                vertexShader: `
                    varying vec2 vUv;
                    void main() {
                        vUv = uv;
                        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
                    }
                `,
                fragmentShader: `
                    uniform sampler2D tDiffuse;
                    uniform float darkness;
                    uniform float offset;
                    varying vec2 vUv;
                    void main() {
                        vec4 texel = texture2D(tDiffuse, vUv);
                        vec2 uv = (vUv - vec2(0.5)) * vec2(offset);
                        float vignette = 1.0 - dot(uv, uv);
                        vignette = clamp(vignette, 0.0, 1.0);
                        texel.rgb *= mix(1.0 - darkness, 1.0, vignette);
                        gl_FragColor = texel;
                    }
                `,
            };
            const vignettePass = new THREE.ShaderPass(VignetteShader);
            this.composer.addPass(vignettePass);
            this._vignettePass = vignettePass;

            // 5. FXAA — anti-aliasing (final pass)
            if (typeof THREE.FXAAShader !== "undefined") {
                const fxaaPass = new THREE.ShaderPass(THREE.FXAAShader);
                fxaaPass.material.uniforms['resolution'].value.set(
                    1 / (w * pixelRatio), 1 / (h * pixelRatio)
                );
                this.composer.addPass(fxaaPass);
                this._fxaaPass = fxaaPass;
            }

            console.log("Post-processing pipeline initialized");
        } catch (e) {
            console.warn("Post-processing setup failed — falling back to direct render:", e);
            this.composer = null;
        }
    }

    // ─── Procedural Canvas Textures ───

    /**
     * Generate a canvas texture for architectural surface types.
     * @param {"stone"|"marble"|"brick"|"terracotta"|"wood"} type
     * @param {string} baseColor - hex color string
     * @param {number} [size=256]
     * @returns {THREE.CanvasTexture}
     */
    _generateTexture(type, baseColor, size) {
        size = size || 256;
        const cacheKey = `tex:${type}:${baseColor}:${size}`;
        if (this._canvasTextureCache && this._canvasTextureCache.has(cacheKey)) {
            return this._canvasTextureCache.get(cacheKey);
        }

        const canvas = document.createElement("canvas");
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext("2d");
        const base = new THREE.Color(baseColor);
        const r = Math.floor(base.r * 255);
        const g = Math.floor(base.g * 255);
        const b = Math.floor(base.b * 255);

        // Seed-based pseudo-random for deterministic textures
        let seed = 0;
        for (let i = 0; i < cacheKey.length; i++) seed = ((seed << 5) - seed + cacheKey.charCodeAt(i)) | 0;
        const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };

        if (type === "stone") {
            // Random noise in beige spectrum with subtle vein lines
            ctx.fillStyle = baseColor;
            ctx.fillRect(0, 0, size, size);
            const imgData = ctx.getImageData(0, 0, size, size);
            const d = imgData.data;
            for (let i = 0; i < d.length; i += 4) {
                const noise = (rand() - 0.5) * 30;
                d[i] = Math.max(0, Math.min(255, r + noise));
                d[i + 1] = Math.max(0, Math.min(255, g + noise * 0.9));
                d[i + 2] = Math.max(0, Math.min(255, b + noise * 0.7));
            }
            ctx.putImageData(imgData, 0, 0);
            // Subtle vein lines
            ctx.strokeStyle = `rgba(${r * 0.7}, ${g * 0.7}, ${b * 0.6}, 0.15)`;
            ctx.lineWidth = 1;
            for (let v = 0; v < 5; v++) {
                ctx.beginPath();
                let vx = rand() * size, vy = rand() * size;
                ctx.moveTo(vx, vy);
                for (let s = 0; s < 6; s++) {
                    vx += (rand() - 0.5) * size * 0.3;
                    vy += (rand() - 0.3) * size * 0.2;
                    ctx.lineTo(vx, vy);
                }
                ctx.stroke();
            }
        } else if (type === "marble") {
            // White with gray/blue veins
            ctx.fillStyle = baseColor;
            ctx.fillRect(0, 0, size, size);
            const imgData = ctx.getImageData(0, 0, size, size);
            const d = imgData.data;
            for (let i = 0; i < d.length; i += 4) {
                const noise = (rand() - 0.5) * 12;
                d[i] = Math.max(0, Math.min(255, r + noise));
                d[i + 1] = Math.max(0, Math.min(255, g + noise));
                d[i + 2] = Math.max(0, Math.min(255, b + noise * 1.2));
            }
            ctx.putImageData(imgData, 0, 0);
            // Gray/blue veins
            for (let v = 0; v < 4; v++) {
                ctx.strokeStyle = `rgba(${120 + rand() * 40}, ${125 + rand() * 40}, ${140 + rand() * 50}, ${0.1 + rand() * 0.15})`;
                ctx.lineWidth = 0.5 + rand() * 1.5;
                ctx.beginPath();
                let vx = rand() * size, vy = rand() * size;
                ctx.moveTo(vx, vy);
                for (let s = 0; s < 8; s++) {
                    vx += (rand() - 0.5) * size * 0.25;
                    vy += (rand() - 0.3) * size * 0.15;
                    ctx.quadraticCurveTo(
                        vx + (rand() - 0.5) * 20, vy + (rand() - 0.5) * 20,
                        vx, vy
                    );
                }
                ctx.stroke();
            }
        } else if (type === "brick") {
            // Grid pattern with per-brick color variation
            ctx.fillStyle = baseColor;
            ctx.fillRect(0, 0, size, size);
            const brickH = Math.floor(size / 8);
            const brickW = Math.floor(size / 4);
            const mortarW = 2;
            ctx.fillStyle = `rgba(${r * 0.6}, ${g * 0.6}, ${b * 0.5}, 1)`;
            ctx.fillRect(0, 0, size, size); // mortar base
            for (let row = 0; row < 8; row++) {
                const offset = (row % 2) * (brickW / 2);
                for (let col = -1; col < 5; col++) {
                    const bx = col * brickW + offset;
                    const by = row * brickH;
                    const variation = (rand() - 0.5) * 35;
                    ctx.fillStyle = `rgb(${Math.max(0, Math.min(255, r + variation))}, ${Math.max(0, Math.min(255, g + variation * 0.7))}, ${Math.max(0, Math.min(255, b + variation * 0.5))})`;
                    ctx.fillRect(bx + mortarW, by + mortarW, brickW - mortarW * 2, brickH - mortarW * 2);
                }
            }
        } else if (type === "terracotta") {
            // Warm orange with slight surface variation
            ctx.fillStyle = baseColor;
            ctx.fillRect(0, 0, size, size);
            const imgData = ctx.getImageData(0, 0, size, size);
            const d = imgData.data;
            for (let y = 0; y < size; y++) {
                for (let x = 0; x < size; x++) {
                    const i = (y * size + x) * 4;
                    const noise = (rand() - 0.5) * 20;
                    // Subtle horizontal streaks
                    const streak = Math.sin(y * 0.3 + rand() * 0.5) * 8;
                    d[i] = Math.max(0, Math.min(255, r + noise + streak));
                    d[i + 1] = Math.max(0, Math.min(255, g + noise * 0.8 + streak * 0.6));
                    d[i + 2] = Math.max(0, Math.min(255, b + noise * 0.5));
                }
            }
            ctx.putImageData(imgData, 0, 0);
        } else if (type === "wood") {
            // Brown with horizontal grain lines
            ctx.fillStyle = baseColor;
            ctx.fillRect(0, 0, size, size);
            const imgData = ctx.getImageData(0, 0, size, size);
            const d = imgData.data;
            for (let y = 0; y < size; y++) {
                const grainBase = Math.sin(y * 0.15) * 15 + Math.sin(y * 0.4) * 8;
                for (let x = 0; x < size; x++) {
                    const i = (y * size + x) * 4;
                    const grain = grainBase + (rand() - 0.5) * 12;
                    d[i] = Math.max(0, Math.min(255, r + grain));
                    d[i + 1] = Math.max(0, Math.min(255, g + grain * 0.8));
                    d[i + 2] = Math.max(0, Math.min(255, b + grain * 0.4));
                }
            }
            ctx.putImageData(imgData, 0, 0);
            // Occasional knot
            for (let k = 0; k < 2; k++) {
                const kx = rand() * size, ky = rand() * size;
                const kr = 3 + rand() * 6;
                ctx.beginPath();
                ctx.arc(kx, ky, kr, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${r * 0.6}, ${g * 0.5}, ${b * 0.4}, 0.3)`;
                ctx.fill();
            }
        } else {
            // Fallback: just fill base color with subtle noise
            ctx.fillStyle = baseColor;
            ctx.fillRect(0, 0, size, size);
        }

        const tex = new THREE.CanvasTexture(canvas);
        tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
        tex.repeat.set(2, 2);
        tex.needsUpdate = true;
        if (this._canvasTextureCache) this._canvasTextureCache.set(cacheKey, tex);
        return tex;
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

        // Try Three.js Sky shader for dynamic sky
        if (typeof THREE.Sky === "function") {
            try {
                this._setupDynamicSky(r);
                return; // Dynamic sky handles PMREM and background
            } catch (e) {
                console.warn("Dynamic Sky setup failed — falling back to procedural texture:", e);
                // Clean up partial sky setup
                if (this._sky) { this.scene.remove(this._sky); this._sky = null; }
            }
        }

        // Fallback: procedural equirect sky texture
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
        // Fog is set in init() after scene scale is known
    }

    /** Set up Three.js Sky shader with PMREM environment map. */
    _setupDynamicSky(renderer) {
        const sky = new THREE.Sky();
        sky.scale.setScalar(10000);
        this.scene.add(sky);
        this._sky = sky;

        this._sunPosition = new THREE.Vector3();

        // Configure sky uniforms for Mediterranean atmosphere
        const skyUniforms = sky.material.uniforms;
        skyUniforms['turbidity'].value = 4;
        skyUniforms['rayleigh'].value = 1.5;
        skyUniforms['mieCoefficient'].value = 0.005;
        skyUniforms['mieDirectionalG'].value = 0.85;

        // PMREM for environment reflections
        if (typeof THREE.PMREMGenerator === "function") {
            if (this._pmremGenerator) {
                try { this._pmremGenerator.dispose(); } catch (e) { /* ignore */ }
            }
            this._pmremGenerator = new THREE.PMREMGenerator(renderer);
            this._pmremGenerator.compileEquirectangularShader();
        }

        // Apply default time of day (late morning)
        this._updateSkyForTimeOfDay(this._timeOfDay || 0.35);
    }

    /** Update Sky shader, PMREM environment, and scene background for given time. */
    _updateSkyForTimeOfDay(t) {
        if (!this._sky) return;

        const angle = (t - 0.25) * Math.PI; // 0.25=horizon east, 0.5=zenith, 0.75=horizon west
        const sunY = Math.sin(angle);
        const sunX = Math.cos(angle);
        const sunAlt = Math.max(0, sunY);

        // Sun position for sky shader — phi from zenith, theta around Y axis
        const phi = Math.PI / 2 - Math.asin(Math.max(-0.05, sunY)); // Allow slight below-horizon
        const theta = Math.atan2(sunX, 0.3); // Slight Z offset for dramatic angle
        this._sunPosition.setFromSphericalCoords(1, phi, theta);

        const skyUniforms = this._sky.material.uniforms;
        skyUniforms['sunPosition'].value.copy(this._sunPosition);

        // Adjust sky parameters based on time
        const warmth = 1 - sunAlt;
        skyUniforms['turbidity'].value = 2 + warmth * 3; // Moderate haze, capped
        skyUniforms['rayleigh'].value = 1.5 + sunAlt * 0.5; // Subtle blue shift
        skyUniforms['mieCoefficient'].value = 0.003 + warmth * 0.005; // Gentle scattering

        // Regenerate environment map from sky — HEAVILY throttled (very expensive)
        const now = Date.now();
        if (!this._lastSkyEnvUpdate || now - this._lastSkyEnvUpdate > 2000) {
            this._lastSkyEnvUpdate = now;
            this._regenerateSkyEnvironment();
        }
    }

    /** Regenerate PMREM from current sky state. Very expensive — call sparingly. */
    _regenerateSkyEnvironment() {
        if (!this._pmremGenerator || !this._sky) return;
        try {
            // Temporarily hide the sky from the scene to avoid feedback loop
            const skyVisible = this._sky.visible;
            this._sky.visible = true;
            if (this._pmremRenderTarget) {
                this._pmremRenderTarget.dispose();
            }
            const rt = this._pmremGenerator.fromScene(this.scene, 0, 0.1, 10000);
            this.scene.environment = rt.texture;
            this._pmremRenderTarget = rt;
            this._sky.visible = skyVisible;
        } catch (e) {
            // Silently continue — environment map is nice-to-have
        }
    }

    /** Warm dusty horizon + soft sun + wispy clouds for ancient city atmosphere. */
    _createProceduralSkyTexture() {
        const w = 1024;
        const h = 512;
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");

        // Upper sky gradient — deep blue to warm horizon
        const top = ctx.createLinearGradient(0, 0, 0, h * 0.42);
        top.addColorStop(0, "#7eb8e0");
        top.addColorStop(0.3, "#a8c8d8");
        top.addColorStop(0.6, "#d4c4a8");
        top.addColorStop(1, "#c4a878");
        ctx.fillStyle = top;
        ctx.fillRect(0, 0, w, h * 0.42);

        // Lower sky (horizon + below)
        const low = ctx.createLinearGradient(0, h * 0.38, 0, h);
        low.addColorStop(0, "#b8a888");
        low.addColorStop(0.55, "#9a8a6a");
        low.addColorStop(1, "#6a5a48");
        ctx.fillStyle = low;
        ctx.fillRect(0, h * 0.38, w, h * 0.62);

        // Procedural cloud wisps — layered noise for natural cirrus patterns
        const cloudData = ctx.getImageData(0, 0, w, h);
        const data = cloudData.data;
        // Simple multi-octave noise for cloud density
        const noise = (px, py, freq) => {
            const x = px * freq, y = py * freq;
            return (Math.sin(x * 1.3 + y * 0.7) * 0.5
                + Math.sin(x * 0.4 - y * 1.1) * 0.3
                + Math.sin(x * 2.1 + y * 1.9) * 0.2)
                * Math.sin(x * 0.07 + y * 0.05);
        };
        for (let py = 0; py < h * 0.38; py++) {
            const skyFrac = py / (h * 0.38); // 0 at top, 1 at horizon
            // Clouds concentrated in mid-sky band (20-60% height)
            const bandWeight = Math.max(0, 1 - Math.pow((skyFrac - 0.35) / 0.25, 2));
            if (bandWeight < 0.01) continue;
            for (let px = 0; px < w; px++) {
                const n = noise(px / w, py / h, 12) * 0.4
                    + noise(px / w + 3.7, py / h + 1.2, 24) * 0.3
                    + noise(px / w + 7.1, py / h + 5.3, 48) * 0.2;
                const density = Math.max(0, n * bandWeight);
                if (density > 0.05) {
                    const idx = (py * w + px) * 4;
                    const alpha = Math.min(0.45, density * 0.8);
                    // Blend white cloud over existing sky
                    data[idx] = Math.min(255, data[idx] + 255 * alpha);
                    data[idx + 1] = Math.min(255, data[idx + 1] + 248 * alpha);
                    data[idx + 2] = Math.min(255, data[idx + 2] + 235 * alpha);
                }
            }
        }
        ctx.putImageData(cloudData, 0, 0);

        // Sun glow — warm directional light source
        const sunX = w * 0.62;
        const sunY = h * 0.18;
        const sunR = Math.min(w, h) * 0.085;
        // Outer halo
        const halo = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, sunR * 4.5);
        halo.addColorStop(0, "rgba(255,240,200,0.15)");
        halo.addColorStop(0.5, "rgba(255,220,160,0.06)");
        halo.addColorStop(1, "rgba(255,200,120,0)");
        ctx.fillStyle = halo;
        ctx.fillRect(0, 0, w, h * 0.45);
        // Core glow
        const rg = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, sunR * 2.8);
        rg.addColorStop(0, "rgba(255,252,235,0.95)");
        rg.addColorStop(0.15, "rgba(255,240,200,0.6)");
        rg.addColorStop(0.4, "rgba(255,220,160,0.2)");
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
    _mat(colorOrDescriptor, roughness = 0.7, metalness = 0.02) {
        // Handle material descriptor objects: {color, roughness, metalness, texture}
        if (colorOrDescriptor && typeof colorOrDescriptor === "object" && !Array.isArray(colorOrDescriptor)) {
            const desc = colorOrDescriptor;
            const col = desc.color || "#888888";
            const rVal = desc.roughness != null ? desc.roughness : roughness;
            const mVal = desc.metalness != null ? desc.metalness : metalness;
            const texType = desc.texture || null;
            return this._matFromDescriptor(col, rVal, mVal, texType);
        }

        let color = colorOrDescriptor;

        // Material name resolution — look up in grammar engine
        if (typeof color === "string" && !color.startsWith("#") && !/^0x/i.test(color) && !/^\d/.test(color)) {
            const matDef = window.EternalCities?.GrammarEngine?.getMaterial?.(color);
            if (matDef) {
                const rVal = matDef.roughness != null ? matDef.roughness : roughness;
                const mVal = matDef.metalness != null ? matDef.metalness : metalness;
                const texType = matDef.texture || null;
                return this._matFromDescriptor(matDef.color || "#888888", rVal, mVal, texType);
            }
            // Not found in grammar engine — treat as hex color fallback
        }

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

    /** Internal: create material from resolved descriptor values, optionally with canvas texture. */
    _matFromDescriptor(color, roughness, metalness, textureType) {
        const r = Math.max(0.05, Math.min(1, Number(roughness)));
        const m = Math.max(0, Math.min(1, Number(metalness)));
        const texKey = textureType || "none";
        const key = `desc:${color}:${r.toFixed(3)}:${m.toFixed(3)}:${texKey}`;
        if (!this._matCache) this._matCache = new Map();
        if (!this._matCache.has(key)) {
            const opts = {
                color: new THREE.Color(color),
                roughness: r,
                metalness: m,
                envMapIntensity: 0.78,
            };
            if (textureType && typeof this._generateTexture === "function") {
                const tex = this._generateTexture(textureType, color, 256);
                if (tex) opts.map = tex;
            }
            const mat = new THREE.MeshStandardMaterial(opts);
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
        // Remove all dynamic objects (buildings, ground, grid) but keep lights, camera, and Sky
        const keep = new Set();
        this.scene.children.forEach(child => {
            if (child.isLight || child.isCamera || child === this.camera) keep.add(child);
            if (this._sky && child === this._sky) keep.add(child);
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
        this._constructingGroups.clear();
        this._cornerHeights = null;
        // Clean up particles
        this._dustParticles = null;
        this._dustVelocities = null;
        // District labels group was removed with scene children — recreate
        this._districtLabelsGroup = new THREE.Group();
        this._districtLabelsGroup.name = "districtLabels";
        this.scene.add(this._districtLabelsGroup);
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
    // ─── Terrain Data from Blueprint (hills/water) ───

    setTerrainData(hills, water) {
        this._blueprintHills = hills || [];
        this._blueprintWater = water || [];
        if (this.grid) {
            this._cornerHeights = this._computeCornerHeightGrid();
            this._rebuildTerrainMesh();
        }
    }

    _rebuildTerrainMesh() {
        const S = TILE_SIZE;
        const H = this._cornerHeights;
        if (!H || !H.length) return;

        // Remove old terrain
        if (this._terrainMesh) {
            this.scene.remove(this._terrainMesh);
            this._terrainMesh.geometry.dispose();
        }
        // Remove old water plane
        if (this._riverWaterPlane) {
            this.scene.remove(this._riverWaterPlane);
            this._riverWaterPlane.geometry.dispose();
        }

        // Compute height range
        let minH = Infinity, maxH = -Infinity;
        for (const row of H) {
            for (const h of row) {
                if (h < minH) minH = h;
                if (h > maxH) maxH = h;
            }
        }
        if (!Number.isFinite(minH)) minH = 0;
        if (!Number.isFinite(maxH)) maxH = 0;

        // Rebuild terrain mesh
        this._terrainMesh = this._buildTerrainHeightfieldMesh(S, 0x9a7b52, minH, maxH);
        this.scene.add(this._terrainMesh);

        // Add water plane at Y=0 if we have water features or negative elevation
        const hasWater = (this._blueprintWater && this._blueprintWater.length > 0);
        if (hasWater || minH < -0.1) {
            const gw = this.width || 40;
            const gh = this.height || 40;
            const waterGeo = new THREE.PlaneGeometry(gw * S * 1.5, gh * S * 1.5);
            waterGeo.rotateX(-Math.PI / 2);
            const waterMat = new THREE.MeshStandardMaterial({
                color: 0x2a6a8a,
                roughness: 0.15,
                metalness: 0.3,
                transparent: true,
                opacity: 0.7,
            });
            this._riverWaterPlane = new THREE.Mesh(waterGeo, waterMat);
            this._riverWaterPlane.position.set(gw * S / 2, -0.05 * S, gh * S / 2);
            this._riverWaterPlane.receiveShadow = true;
            this._riverWaterPlane.userData.isWater = true;
            this.scene.add(this._riverWaterPlane);
        }

        // Reposition existing buildings to match new terrain
        this.buildingGroups.forEach((group, key) => {
            const tile = group.userData && group.userData.tile;
            if (tile) {
                const newY = this._surfaceYAtWorldXZ(
                    (tile.x + 0.5) * S, (tile.y + 0.5) * S
                );
                group.position.y = newY;
                group.userData.baseY = newY;
            }
        });
    }

    _computeElevationFromHills(tileX, tileZ) {
        let elev = 0;
        for (const hill of (this._blueprintHills || [])) {
            const dx = tileX - hill.cx;
            const dz = tileZ - hill.cy;
            const distSq = dx * dx + dz * dz;
            const sigma = hill.radius || 5;
            elev += (hill.peak || 2) * Math.exp(-distSq / (2 * sigma * sigma));
        }
        // Carve rivers
        for (const w of (this._blueprintWater || [])) {
            if (w.type === "river" && w.points && w.points.length >= 2) {
                const dist = this._distToPolyline(tileX, tileZ, w.points);
                const riverWidth = 1.5;
                if (dist < riverWidth * 3) {
                    elev -= 1.5 * Math.exp(-dist * dist / (2 * riverWidth * riverWidth));
                }
            }
        }
        return elev;
    }

    _distToPolyline(px, pz, points) {
        let minDist = Infinity;
        for (let i = 0; i < points.length - 1; i++) {
            const [ax, ay] = points[i];
            const [bx, by] = points[i + 1];
            const dx = bx - ax, dy = by - ay;
            const lenSq = dx * dx + dy * dy;
            let t = lenSq > 0 ? ((px - ax) * dx + (pz - ay) * dy) / lenSq : 0;
            t = Math.max(0, Math.min(1, t));
            const cx = ax + t * dx, cy = ay + t * dy;
            const d = Math.sqrt((px - cx) ** 2 + (pz - cy) ** 2);
            if (d < minDist) minDist = d;
        }
        return minDist;
    }

    _computeCornerHeightGrid() {
        const gw = this.width;
        const gh = this.height;
        const hasHills = (this._blueprintHills && this._blueprintHills.length > 0);
        const H = [];
        for (let j = 0; j <= gh; j++) {
            const row = [];
            for (let i = 0; i <= gw; i++) {
                // Base elevation from blueprint hills (if available)
                let baseElev = hasHills ? this._computeElevationFromHills(i, j) : 0;

                // Check tile data for overrides
                let sum = 0, n = 0;
                let maxBuildingElev = null;
                for (const [tx, ty] of [[i - 1, j - 1], [i, j - 1], [i - 1, j], [i, j]]) {
                    if (tx >= 0 && ty >= 0 && tx < gw && ty < gh) {
                        const t = this.grid[ty][tx];
                        const e = t && t.elevation != null ? Number(t.elevation) : 0;
                        if (Number.isFinite(e)) {
                            sum += e;
                            n++;
                            if (t.terrain === "building") {
                                if (maxBuildingElev === null || e > maxBuildingElev) {
                                    maxBuildingElev = e;
                                }
                            }
                        }
                    }
                }
                // Buildings get flat platforms at their elevation
                if (maxBuildingElev !== null) {
                    row.push(maxBuildingElev);
                } else if (n > 0 && !hasHills) {
                    // No hills data: use tile averages (original behavior)
                    row.push(sum / n);
                } else {
                    // Hills data: use gaussian elevation
                    row.push(baseElev);
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
        // Elevation-band vertex coloring
        const colWater = new THREE.Color(0x2a5a6a);
        const colLowland = new THREE.Color(0x5a7a4a);
        const colMidGreen = new THREE.Color(0x7a9a5a);
        const colHillBrown = new THREE.Color(0x9a7b52);
        const colPeak = new THREE.Color(0xb0a080);
        const colBase = new THREE.Color(earthColor);
        const hasHills = (this._blueprintHills && this._blueprintHills.length > 0);
        const tmpCol = new THREE.Color();
        for (let j = 0; j <= gh; j++) {
            for (let i = 0; i <= gw; i++) {
                const hTile = H[j][i];
                const y = hTile * S;
                positions.push(i * S, y, j * S);
                uvs.push(i / Math.max(1, gw), j / Math.max(1, gh));
                if (hasHills) {
                    // Elevation-band coloring when terrain data available
                    if (hTile < -0.3) {
                        tmpCol.copy(colWater);
                    } else if (hTile < 0.5) {
                        const t = Math.max(0, (hTile + 0.3) / 0.8);
                        tmpCol.copy(colWater).lerp(colLowland, t);
                    } else if (hTile < 2.0) {
                        const t = (hTile - 0.5) / 1.5;
                        tmpCol.copy(colLowland).lerp(colMidGreen, t);
                    } else if (hTile < 4.0) {
                        const t = (hTile - 2.0) / 2.0;
                        tmpCol.copy(colMidGreen).lerp(colHillBrown, t);
                    } else {
                        const t = Math.min(1, (hTile - 4.0) / 3.0);
                        tmpCol.copy(colHillBrown).lerp(colPeak, t);
                    }
                } else {
                    // Fallback: simple two-color blend (original behavior)
                    const hSpan = maxH > minH ? maxH - minH : 1;
                    const t = Math.max(0, Math.min(1, (hTile - minH) / hSpan));
                    tmpCol.copy(colBase).multiplyScalar(0.78 + t * 0.36);
                }
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
        matParams.side = THREE.DoubleSide;  // Visible from below at oblique camera angles
        const mat = new THREE.MeshStandardMaterial(matParams);
        const mesh = new THREE.Mesh(geom, mat);
        mesh.receiveShadow = true;
        mesh.castShadow = false;
        mesh.frustumCulled = false;  // Never cull the terrain — always visible
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
        // Load world origin offset for growing worlds
        this.minX = worldState.min_x || 0;
        this.minY = worldState.min_y || 0;
        this._anchorFootprints.clear();
        if (Array.isArray(worldState.tiles)) {
            for (const t of worldState.tiles) {
                if (t.x >= 0 && t.y >= 0 && t.x < this.width && t.y < this.height) {
                    this.grid[t.y][t.x] = t;
                    this._updateAnchorIndex(t);
                }
            }
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
            const shadowMapSize = 4096; // Always high-quality shadow maps
            if (this._sunLight.shadow.map) {
                this._sunLight.shadow.map.dispose();
                this._sunLight.shadow.map = null;
            }
            this._sunLight.shadow.mapSize.set(shadowMapSize, shadowMapSize);
        }

        this._resetCameraPoseToMapCenter(midY, mapW, mapH);

        // Atmospheric fog — very subtle warm haze at extreme distances only
        // Tuned so nearby terrain is always crisp; only very distant objects fade
        const fogDensity = 0.00012 / Math.max(1, diagonal / 4000);
        this.scene.fog = new THREE.FogExp2(0xc4b498, fogDensity);

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
        water.frustumCulled = false;
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

        // Create atmospheric dust mote particles
        this._createDustParticles(mapW, mapH, midY);
    }

    // ─── Particle System (Dust Motes) ───

    /** Create lightweight particle system for atmospheric dust motes. */
    _createDustParticles(mapW, mapH, midY) {
        const count = 250;
        const positions = new Float32Array(count * 3);
        const velocities = new Float32Array(count * 3);

        for (let i = 0; i < count; i++) {
            positions[i * 3] = Math.random() * mapW;
            positions[i * 3 + 1] = midY + Math.random() * 80 + 10;
            positions[i * 3 + 2] = Math.random() * mapH;
            // Slow brownian drift velocities
            velocities[i * 3] = (Math.random() - 0.5) * 0.15;
            velocities[i * 3 + 1] = (Math.random() - 0.5) * 0.05;
            velocities[i * 3 + 2] = (Math.random() - 0.5) * 0.15;
        }

        const geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));

        const mat = new THREE.PointsMaterial({
            color: 0xffe8b0,        // warm golden
            size: 1.5,
            transparent: true,
            opacity: 0.0,           // starts invisible; setTimeOfDay controls visibility
            depthWrite: false,
            sizeAttenuation: true,
        });

        const points = new THREE.Points(geo, mat);
        points.frustumCulled = false;
        points.visible = false; // Controlled by setTimeOfDay
        this.scene.add(points);

        this._dustParticles = points;
        this._dustVelocities = velocities;
        this._dustBoundsW = mapW;
        this._dustBoundsH = mapH;
        this._dustMidY = midY;
    }

    updateTiles(tiles) {
        if (!this.grid) return;
        // Auto-expand grid if tiles arrive outside current bounds
        let needsExpand = false;
        let newW = this.width, newH = this.height;
        for (const tile of tiles) {
            if (tile.x >= newW) { newW = tile.x + 1; needsExpand = true; }
            if (tile.y >= newH) { newH = tile.y + 1; needsExpand = true; }
        }
        if (needsExpand) {
            this._expandGrid(newW, newH);
        }

        // Recompute terrain heightfield with new tile elevations so buildings sit on ground
        let heightsDirty = false;
        for (const tile of tiles) {
            if (tile.x >= 0 && tile.y >= 0 && tile.x < this.width && tile.y < this.height) {
                const old = this.grid[tile.y][tile.x];
                if (old.elevation !== tile.elevation) heightsDirty = true;
                this.grid[tile.y][tile.x] = tile;
                this._updateAnchorIndex(tile);  // Maintain spatial index
                if (tile.terrain && tile.terrain !== "empty") {
                    const anchor = tile.spec && tile.spec.anchor;
                    if (anchor && (tile.x !== anchor.x || tile.y !== anchor.y)) {
                        if (anchor.y < this.height && anchor.x < this.width) {
                            const anchorTile = this.grid[anchor.y][anchor.x];
                            if (anchorTile && anchorTile.terrain && anchorTile.terrain !== "empty") {
                                this._buildFromSpec(anchorTile, true);
                            }
                        }
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

    // O(1) anchor footprint lookup — maintained via _updateAnchorIndex on every tile update
    _getAnchorFootprint(anchor) {
        const key = `${anchor.x},${anchor.y}`;
        return this._anchorFootprints.get(key)
            || { minX: anchor.x, maxX: anchor.x, minY: anchor.y, maxY: anchor.y };
    }

    // Dynamically expand the dense grid to accommodate tiles beyond current bounds.
    // Preserves all existing tile data and building groups.
    _expandGrid(newWidth, newHeight) {
        const emptyTile = { terrain: "empty", elevation: 0 };
        const oldW = this.width, oldH = this.height;
        // Extend existing rows
        for (let y = 0; y < oldH; y++) {
            for (let x = oldW; x < newWidth; x++) {
                this.grid[y].push({ ...emptyTile, x, y });
            }
        }
        // Add new rows
        for (let y = oldH; y < newHeight; y++) {
            const row = [];
            for (let x = 0; x < newWidth; x++) row.push({ ...emptyTile, x, y });
            this.grid.push(row);
        }
        this.width = newWidth;
        this.height = newHeight;
        // Rebuild terrain heightfield for the expanded area
        this._cornerHeights = this._computeCornerHeightGrid();
        // Rebuild terrain mesh to cover new area
        if (this._terrainMesh) {
            this.scene.remove(this._terrainMesh);
            this._terrainMesh.geometry.dispose();
            const S = TILE_SIZE;
            let minH = 0, maxH = 0;
            for (const row of this._cornerHeights) {
                for (const v of row) {
                    if (v < minH) minH = v;
                    if (v > maxH) maxH = v;
                }
            }
            this._terrainMesh = this._buildTerrainHeightfieldMesh(S, 0x9a7b52, minH, maxH);
            this.scene.add(this._terrainMesh);
        }
        console.log(`Grid expanded: ${oldW}×${oldH} → ${newWidth}×${newHeight}`);
    }

    _updateAnchorIndex(tile) {
        const anchor = tile.spec && tile.spec.anchor;
        if (!anchor) return;
        const key = `${anchor.x},${anchor.y}`;
        const fp = this._anchorFootprints.get(key)
            || { minX: anchor.x, maxX: anchor.x, minY: anchor.y, maxY: anchor.y };
        fp.minX = Math.min(fp.minX, tile.x);
        fp.maxX = Math.max(fp.maxX, tile.x);
        fp.minY = Math.min(fp.minY, tile.y);
        fp.maxY = Math.max(fp.maxY, tile.y);
        this._anchorFootprints.set(key, fp);
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

            // Grammar engine integration — expand grammar specs into procedural parts
            // Grammar data may be in spec.grammar (canonical) or tile.grammar (legacy/direct)
            const grammarId = spec.grammar || tile.grammar;
            const grammarParams = spec.params || tile.grammar_params || {};
            if (!resolvedComponents && grammarId && window.EternalCities?.GrammarEngine) {
                try {
                    const grammarShapes = window.EternalCities.GrammarEngine.expand(grammarId, grammarParams);
                    const allShapes = grammarShapes.concat(spec.overrides || []);
                    // Grammar shapes already use {shape, position, size, color} format.
                    // Normalize any legacy {type, pos} keys to {shape, position} for _buildProcedural.
                    const parts = allShapes.map(s => {
                        const part = { ...s };
                        if (s.type && !s.shape) { part.shape = s.type; delete part.type; }
                        if (s.pos && !s.position) { part.position = s.pos; delete part.pos; }
                        return part;
                    });
                    // Wrap as a single procedural component for the builder system
                    resolvedComponents = [{ type: "procedural", stack_role: "structural", parts }];
                } catch (e) {
                    const msg = e && e.message ? e.message : String(e);
                    console.warn(`Grammar expansion failed for tile (${tile.x},${tile.y}): ${msg}`);
                }
            }

            // Dense array format — expand via grammar engine, wrap as procedural
            if (!resolvedComponents && spec.shapes && Array.isArray(spec.shapes) && spec.shapes.length > 0 && Array.isArray(spec.shapes[0])) {
                if (window.EternalCities?.GrammarEngine?.expandDenseShapes) {
                    try {
                        const denseShapes = window.EternalCities.GrammarEngine.expandDenseShapes(spec.shapes);
                        const parts = denseShapes.map(s => {
                            const part = { ...s };
                            if (s.type && !s.shape) { part.shape = s.type; delete part.type; }
                            if (s.pos && !s.position) { part.position = s.pos; delete part.pos; }
                            return part;
                        });
                        resolvedComponents = [{ type: "procedural", stack_role: "structural", parts }];
                    } catch (e) {
                        const msg = e && e.message ? e.message : String(e);
                        console.warn(`Dense shape expansion failed for tile (${tile.x},${tile.y}): ${msg}`);
                    }
                }
            }
        }

        if (!isTerrainMesh) {
            if (!spec || !resolvedComponents || resolvedComponents.length === 0) {
                const err = `Building tile (${tile.x},${tile.y}) requires spec.components or spec.template with a known id; terrain=${JSON.stringify(terrain)}`;
                this._emitRenderError(err, tile, key);
                return { ok: false, error: err, key };
            }
            // Per-instance deterministic variation — same-type buildings differ in
            // colors, stories, windows, column counts, heights. Stable per tile.
            const anchorKey = spec.anchor
                ? spec.anchor.x * 73856093 + spec.anchor.y * 19349663
                : tile.x * 73856093 + tile.y * 19349663;
            resolvedComponents = this._applyInstanceVariation(
                resolvedComponents, anchorKey, tile.building_type
            );
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
            this._constructingGroups.delete(oldGroup);
        }

        group.traverse((c) => {
            if (c.isMesh) {
                c.userData.tile = tile;
                // Skip shadow casting for tiny meshes, water, and Phase 4 decoration
                const isWater = c.userData && c.userData.isWater;
                const isPhase4 = c.userData && c.userData.phase4Decor;
                if (isWater) {
                    c.castShadow = false;
                    c.receiveShadow = false;
                } else if (isPhase4) {
                    c.castShadow = false;
                    c.receiveShadow = true;
                } else {
                    c.receiveShadow = true;
                    if (c.geometry && c.geometry.boundingSphere) {
                        c.geometry.computeBoundingSphere();
                        c.castShadow = c.geometry.boundingSphere.radius > 0.04;
                    } else {
                        c.castShadow = true;
                    }
                }
            }
        });

        group.traverse((c) => {
            if (c.userData && c.userData.isWater) this._waterMeshes.push(c);
        });

        if (animate && !isTerrainMesh) {
            // Construction animation: reveal bottom-up with staggered timing
            const constructNow = Date.now();
            group.traverse(c => {
                if (c.isMesh) {
                    const baseY = c.position.y;
                    c.userData._constructY = baseY;
                    c.userData._constructStart = constructNow + Math.max(0, baseY) * 200; // Stagger by height
                    c.userData._constructDuration = 1500;
                    c.scale.y = 0.01; // Start flat
                    c.position.y = 0;  // Start at ground
                }
            });
            this._constructingGroups.add(group);
        } else if (animate) {
            // Terrain tiles keep the old drop-in animation
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

    /** Parse road type from tile.description (format: "Name (via|vicus|semita)"). */
    _parseRoadType(tile) {
        const desc = tile.description || "";
        const m = desc.match(/\((via|vicus|semita)\)\s*$/i);
        return m ? m[1].toLowerCase() : "vicus";
    }

    /** Check if a neighbor tile at (gx,gy) is a road or forum. */
    _isRoadNeighbor(gx, gy) {
        const t = this._tileAt(gx, gy);
        return t && (t.terrain === "road" || t.terrain === "forum");
    }

    _buildTerrain(g, tile, spec) {
        const terrain = tile.terrain;
        const sc = this._sceneryFromSpec(spec);
        const tseed = tile.x * 131 + tile.y * 97;
        if (terrain === "road") {
            const roadType = this._parseRoadType(tile);
            const hasName = !!(tile.building_name && tile.building_name.length > 0);

            // ── VIA — major thoroughfare: lighter stone slabs, curbs, drainage channel ──
            if (roadType === "via") {
                const baseColor = hasName ? "#AAA8A0" : "#A0A0A0";
                const road = new THREE.Mesh(
                    new THREE.BoxGeometry(0.98, 0.06, 0.98),
                    this._mat(baseColor, 0.82)
                );
                road.position.y = 0.03;
                g.add(road);

                // Large stone slabs in a 2x2 grid pattern (max 4 slabs)
                const slabCols = 2, slabRows = 2;
                const slabW = 0.40, slabD = 0.40, slabH = 0.012;
                const gapW = (0.90 - slabCols * slabW) / (slabCols + 1);
                const gapD = (0.90 - slabRows * slabD) / (slabRows + 1);
                for (let r = 0; r < slabRows; r++) {
                    for (let c = 0; c < slabCols; c++) {
                        const si = r * slabCols + c;
                        // Subtle shade variation per slab
                        const shade = 0xA0 + Math.floor((this._terrainRand01(tseed, si, 50) - 0.5) * 20);
                        const hex = "#" + shade.toString(16) + shade.toString(16) + (shade - 8).toString(16);
                        const slab = new THREE.Mesh(
                            new THREE.BoxGeometry(slabW - 0.01, slabH, slabD - 0.01),
                            this._mat(hex, 0.78)
                        );
                        const sx = -0.45 + gapW + slabW / 2 + c * (slabW + gapW);
                        const sz = -0.45 + gapD + slabD / 2 + r * (slabD + gapD);
                        slab.position.set(sx, 0.06 + slabH / 2, sz);
                        g.add(slab);
                    }
                }

                // Central drainage channel — thin dark groove running along Z axis
                const drain = new THREE.Mesh(
                    new THREE.BoxGeometry(0.04, 0.008, 0.92),
                    this._mat("#4a4a4a", 0.95)
                );
                drain.position.set(0, 0.061, 0);
                g.add(drain);

                // Raised curb stones on edges where road meets non-road (max 2 curbs
                // — only on the 2 longest non-road edges to stay within mesh budget)
                const curbH = 0.035, curbW = 0.06, curbD = 0.98;
                const neighbors = [
                    { dx: -1, dy: 0, px: -0.47, pz: 0, rw: curbW, rd: curbD },  // west
                    { dx: 1,  dy: 0, px: 0.47,  pz: 0, rw: curbW, rd: curbD },  // east
                    { dx: 0,  dy: -1, px: 0, pz: -0.47, rw: curbD, rd: curbW }, // north
                    { dx: 0,  dy: 1,  px: 0, pz: 0.47,  rw: curbD, rd: curbW }, // south
                ];
                let curbCount = 0;
                for (const nb of neighbors) {
                    if (curbCount >= 2) break;  // max 2 curbs to cap total meshes at 8
                    if (!this._isRoadNeighbor(tile.x + nb.dx, tile.y + nb.dy)) {
                        const curb = new THREE.Mesh(
                            new THREE.BoxGeometry(nb.rw, curbH, nb.rd),
                            this._mat("#B8B0A8", 0.8)
                        );
                        curb.position.set(nb.px, 0.06 + curbH / 2, nb.pz);
                        g.add(curb);
                        curbCount++;
                    }
                }

            // ── VICUS — secondary street: cobblestones with per-stone color variation ──
            } else if (roadType === "vicus") {
                const baseColor = hasName ? "#8C8880" : "#808080";
                const road = new THREE.Mesh(
                    new THREE.BoxGeometry(0.96, 0.05, 0.96),
                    this._mat("#504840", 0.92)  // dark mortar visible between stones
                );
                road.position.y = 0.025;
                g.add(road);

                // Cobblestone pattern: 4-6 stones in a scattered grid
                const pave = sc.pavement_detail != null ? sc.pavement_detail : 0.5;
                const nStone = Math.min(6, Math.max(4, Math.floor(3 + pave * 3) + (tseed % 2)));
                for (let i = 0; i < nStone; i++) {
                    // Each stone: slight size and color variation
                    const sw = 0.14 + this._terrainRand01(tseed, i, 1) * 0.10;
                    const sd = 0.14 + this._terrainRand01(tseed, i, 2) * 0.10;
                    const sh = 0.012 + this._terrainRand01(tseed, i, 8) * 0.006;
                    const grey = 0x70 + Math.floor(this._terrainRand01(tseed, i, 9) * 0x28);
                    const hex = "#" + grey.toString(16) + grey.toString(16) + grey.toString(16);
                    const stone = new THREE.Mesh(
                        new THREE.BoxGeometry(sw, sh, sd),
                        this._mat(hex, 0.85)
                    );
                    // Arrange in a loose grid with jitter
                    const col = i % 3, row = Math.floor(i / 3);
                    const bx = -0.30 + col * 0.28 + (this._terrainRand01(tseed, i, 3) - 0.5) * 0.08;
                    const bz = -0.30 + row * 0.28 + (this._terrainRand01(tseed, i, 4) - 0.5) * 0.08;
                    stone.position.set(bx, 0.05 + sh / 2, bz);
                    // Slight random rotation for organic feel
                    stone.rotation.y = (this._terrainRand01(tseed, i, 5) - 0.5) * 0.15;
                    g.add(stone);
                }

            // ── SEMITA — narrow path: packed earth, irregular surface ──
            } else {
                const baseColor = hasName ? "#A09478" : "#9A8A70";
                const road = new THREE.Mesh(
                    new THREE.BoxGeometry(0.80, 0.04, 0.80),
                    this._mat(baseColor, 0.92)
                );
                road.position.y = 0.02;
                g.add(road);

                // A few irregular earth patches/ruts for texture
                const nPatch = 2 + (tseed % 3);
                for (let i = 0; i < nPatch; i++) {
                    const pw = 0.12 + this._terrainRand01(tseed, i, 20) * 0.16;
                    const pd = 0.08 + this._terrainRand01(tseed, i, 21) * 0.10;
                    const brownShift = Math.floor(this._terrainRand01(tseed, i, 22) * 20);
                    const rc = 0x8A + brownShift, gc = 0x7A + brownShift, bc = 0x60 + brownShift;
                    const hex = "#" + rc.toString(16) + gc.toString(16) + bc.toString(16);
                    const patch = new THREE.Mesh(
                        new THREE.BoxGeometry(pw, 0.005, pd),
                        this._mat(hex, 0.95)
                    );
                    patch.position.set(
                        (this._terrainRand01(tseed, i, 23) - 0.5) * 0.5,
                        0.043,
                        (this._terrainRand01(tseed, i, 24) - 0.5) * 0.5
                    );
                    patch.rotation.y = this._terrainRand01(tseed, i, 25) * Math.PI;
                    g.add(patch);
                }

                // Occasional loose stones on packed earth
                const nLoose = 1 + (tseed % 2);
                for (let i = 0; i < nLoose; i++) {
                    const ls = 0.04 + this._terrainRand01(tseed, i, 30) * 0.04;
                    const looseStone = new THREE.Mesh(
                        new THREE.BoxGeometry(ls, 0.008, ls * 0.8),
                        this._mat("#7a7060", 0.9)
                    );
                    looseStone.position.set(
                        (this._terrainRand01(tseed, i, 31) - 0.5) * 0.55,
                        0.045,
                        (this._terrainRand01(tseed, i, 32) - 0.5) * 0.55
                    );
                    looseStone.rotation.y = this._terrainRand01(tseed, i, 33) * Math.PI;
                    g.add(looseStone);
                }
            }

        } else if (terrain === "forum") {
            // ── FORUM — marble-paved public space with regular slab grid ──
            const baseGround = new THREE.Mesh(
                new THREE.BoxGeometry(0.98, 0.04, 0.98),
                this._mat(spec.color || "#E8DCC8", 0.7)  // cream/white marble base
            );
            baseGround.position.y = 0.02;
            g.add(baseGround);

            // Regular grid of marble slabs (2 cols x 3 rows = 6 slabs max)
            const pave = sc.pavement_detail != null ? sc.pavement_detail : 0.6;
            const nCols = 2, nRows = Math.min(3, Math.max(2, Math.floor(2 + pave)));
            const colSpan = 0.88 / nCols, rowSpan = 0.88 / nRows;
            const slabGap = 0.015;
            let slabIdx = 0;
            for (let r = 0; r < nRows; r++) {
                for (let c = 0; c < nCols; c++) {
                    // Alternating cream/white shades for a polished look
                    const shade = (r + c) % 2 === 0 ? "#DDD4C4" : "#E5DDD0";
                    const variation = Math.floor((this._terrainRand01(tseed, slabIdx, 40) - 0.5) * 10);
                    const rv = parseInt(shade.slice(1, 3), 16) + variation;
                    const gv = parseInt(shade.slice(3, 5), 16) + variation;
                    const bv = parseInt(shade.slice(5, 7), 16) + variation;
                    const hex = "#" + Math.min(255, rv).toString(16).padStart(2, "0")
                                    + Math.min(255, gv).toString(16).padStart(2, "0")
                                    + Math.min(255, bv).toString(16).padStart(2, "0");
                    const sw = colSpan - slabGap, sd = rowSpan - slabGap;
                    const slab = new THREE.Mesh(
                        new THREE.BoxGeometry(sw, 0.01, sd),
                        this._mat(hex, 0.6, 0.03)
                    );
                    const sx = -0.44 + colSpan / 2 + c * colSpan;
                    const sz = -0.44 + rowSpan / 2 + r * rowSpan;
                    slab.position.set(sx, 0.04 + 0.005, sz);
                    g.add(slab);
                    slabIdx++;
                }
            }

        } else if (terrain === "water") {
            // ── WATER — transparent blue with edge foam ──
            const murk = sc.water_murk != null ? sc.water_murk : 0.35;
            const opacity = 0.55 + murk * 0.38;
            const water = new THREE.Mesh(
                new THREE.BoxGeometry(0.98, 0.06, 0.98),
                new THREE.MeshStandardMaterial({ color: 0x2980b9, transparent: true, opacity, roughness: 0.05 })
            );
            water.position.y = -0.03;
            water.userData.isWater = true;
            g.add(water);

            // Foam edges where water meets land (check cardinal neighbors)
            const foamNeighbors = [
                { dx: -1, dy: 0, px: -0.46, pz: 0, fw: 0.06, fd: 0.90 },
                { dx: 1,  dy: 0, px: 0.46,  pz: 0, fw: 0.06, fd: 0.90 },
                { dx: 0,  dy: -1, px: 0, pz: -0.46, fw: 0.90, fd: 0.06 },
                { dx: 0,  dy: 1,  px: 0, pz: 0.46,  fw: 0.90, fd: 0.06 },
            ];
            for (const nb of foamNeighbors) {
                const nt = this._tileAt(tile.x + nb.dx, tile.y + nb.dy);
                if (nt && nt.terrain !== "water") {
                    const foam = new THREE.Mesh(
                        new THREE.BoxGeometry(nb.fw, 0.01, nb.fd),
                        new THREE.MeshStandardMaterial({
                            color: 0xc8dde8, transparent: true, opacity: 0.45, roughness: 0.2
                        })
                    );
                    foam.position.set(nb.px, 0.0, nb.pz);
                    foam.userData.isWater = true;
                    g.add(foam);
                }
            }

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
                // Compound: ring of columns using InstancedMesh (1 draw call instead of N)
                const ringR = Number(p.radius || 0.2);
                const ringH = Number(p.height || 0.3);
                const nCols = Math.max(4, Math.min(24, Math.round(p.column_count || 8)));
                const colR = Number(p.column_radius || 0.012);
                const colGeom = new THREE.CylinderGeometry(colR, colR * 1.1, ringH, 6);
                const colInst = new THREE.InstancedMesh(colGeom, mat, nCols);
                colInst.castShadow = true;
                colInst.receiveShadow = true;
                const ringDummy = this._instDummy;
                for (let ci = 0; ci < nCols; ci++) {
                    const angle = (ci / nCols) * Math.PI * 2;
                    ringDummy.position.set(
                        px + Math.cos(angle) * ringR,
                        anchorY + py + ringH / 2,
                        pz + Math.sin(angle) * ringR
                    );
                    ringDummy.rotation.set(0, 0, 0);
                    ringDummy.scale.set(1, 1, 1);
                    ringDummy.updateMatrix();
                    colInst.setMatrixAt(ci, ringDummy.matrix);
                }
                colInst.instanceMatrix.needsUpdate = true;
                group.add(colInst);
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
            } else if (p.shape === "barrel_roof") {
                // Curved barrel/semicircular roof — like Roman baths or vaulted ceilings
                // params: width, depth, height, segments
                const bw = Number(p.width || w * 0.9);
                const bd = Number(p.depth || d * 0.9);
                const bh = Number(p.height || bw * 0.3);
                const segs = capSeg(p.segments ?? 12, 6, 24);
                // Build from triangle strips (half-cylinder)
                const positions = [];
                for (let si = 0; si < segs; si++) {
                    const a0 = (si / segs) * Math.PI;
                    const a1 = ((si + 1) / segs) * Math.PI;
                    const x0 = Math.cos(a0) * bw / 2, y0 = Math.sin(a0) * bh;
                    const x1 = Math.cos(a1) * bw / 2, y1 = Math.sin(a1) * bh;
                    // Front and back triangles for this strip
                    positions.push(
                        x0, y0, -bd / 2,  x1, y1, -bd / 2,  x1, y1, bd / 2,
                        x0, y0, -bd / 2,  x1, y1, bd / 2,   x0, y0, bd / 2
                    );
                }
                // End caps (semicircles)
                for (const zSide of [-bd / 2, bd / 2]) {
                    for (let si = 0; si < segs; si++) {
                        const a0 = (si / segs) * Math.PI;
                        const a1 = ((si + 1) / segs) * Math.PI;
                        const sign = zSide < 0 ? 1 : -1;
                        positions.push(
                            0, 0, zSide,
                            Math.cos(a0 * sign + (sign < 0 ? 0 : Math.PI)) * bw / 2 * sign, Math.sin(a0) * bh, zSide,
                            Math.cos(a1 * sign + (sign < 0 ? 0 : Math.PI)) * bw / 2 * sign, Math.sin(a1) * bh, zSide
                        );
                    }
                }
                const brGeo = new THREE.BufferGeometry();
                brGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(positions), 3));
                brGeo.computeVertexNormals();
                mesh = new THREE.Mesh(brGeo, mat);
                mesh.position.set(px, anchorY + py, pz);
                maxTop = Math.max(maxTop, anchorY + py + bh);
            } else if (p.shape === "buttress") {
                // External triangular support wall
                // params: width, height, depth (projection), side: left|right|front|back
                const bw = Number(p.width || 0.04);
                const bh = Number(p.height || 0.3);
                const bd = Number(p.depth || 0.08);
                // Triangular profile: box that tapers from full depth at base to zero at top
                const verts = new Float32Array([
                    // Front face (triangle)
                    -bw/2, 0, 0,   bw/2, 0, 0,   bw/2, 0, -bd,
                    -bw/2, 0, 0,   bw/2, 0, -bd,  -bw/2, 0, -bd,
                    // Top face (triangle)
                    -bw/2, bh, 0,   bw/2, bh, 0,   bw/2, 0, -bd,
                    -bw/2, bh, 0,   bw/2, 0, -bd,  -bw/2, 0, -bd,
                    // Side faces
                    -bw/2, 0, 0,   -bw/2, bh, 0,  -bw/2, 0, -bd,
                     bw/2, 0, 0,    bw/2, 0, -bd,   bw/2, bh, 0,
                ]);
                const bGeo = new THREE.BufferGeometry();
                bGeo.setAttribute("position", new THREE.BufferAttribute(verts, 3));
                bGeo.computeVertexNormals();
                mesh = new THREE.Mesh(bGeo, mat);
                mesh.position.set(px, anchorY + py, pz);
                maxTop = Math.max(maxTop, anchorY + py + bh);
            } else if (p.shape === "apse") {
                // Semicircular extension (church/temple apse)
                // params: radius, height, segments
                const ar = Number(p.radius || 0.15);
                const ah = Number(p.height || 0.3);
                const asegs = capSeg(p.segments ?? 12, 6, 24);
                // Half-cylinder (open on one side)
                const apseMesh = new THREE.Mesh(
                    new THREE.CylinderGeometry(ar, ar, ah, asegs, 1, true, 0, Math.PI),
                    mat
                );
                apseMesh.position.set(px, anchorY + py + ah / 2, pz);
                apseMesh.castShadow = true;
                group.add(apseMesh);
                // Half-dome cap
                const domeMesh = new THREE.Mesh(
                    new THREE.SphereGeometry(ar, asegs, 8, 0, Math.PI, 0, Math.PI / 2),
                    mat
                );
                domeMesh.position.set(px, anchorY + py + ah, pz);
                domeMesh.castShadow = true;
                group.add(domeMesh);
                maxTop = Math.max(maxTop, anchorY + py + ah + ar * 0.5);
                mesh = null;
            } else if (p.shape === "tower") {
                // Tall narrow structure with a conical/pyramidal cap
                // params: base_width, base_depth, height, cap_height, cap_style (cone|pyramid|dome)
                const tw = Number(p.base_width || p.width || 0.12);
                const td = Number(p.base_depth || p.depth || tw);
                const th = Number(p.height || 0.6);
                const capH = Number(p.cap_height || th * 0.15);
                const capStyle = p.cap_style || "cone";
                // Tower shaft
                const shaft = new THREE.Mesh(
                    new THREE.BoxGeometry(tw, th - capH, td), mat
                );
                shaft.position.set(px, anchorY + py + (th - capH) / 2, pz);
                shaft.castShadow = true;
                group.add(shaft);
                // Cap
                let capMesh;
                if (capStyle === "dome") {
                    capMesh = new THREE.Mesh(
                        new THREE.SphereGeometry(Math.max(tw, td) / 2, 12, 8, 0, Math.PI * 2, 0, Math.PI / 2),
                        mat
                    );
                } else if (capStyle === "pyramid") {
                    capMesh = new THREE.Mesh(
                        new THREE.ConeGeometry(Math.max(tw, td) * 0.6, capH, 4), mat
                    );
                    capMesh.rotation.y = Math.PI / 4;
                } else {
                    capMesh = new THREE.Mesh(
                        new THREE.ConeGeometry(Math.max(tw, td) * 0.55, capH, 8), mat
                    );
                }
                capMesh.position.set(px, anchorY + py + th - capH / 2, pz);
                capMesh.castShadow = true;
                group.add(capMesh);
                maxTop = Math.max(maxTop, anchorY + py + th);
                mesh = null;
            } else if (p.shape === "shed_roof") {
                // Single-slope lean-to roof — common on workshops, additions
                // params: width, depth, height_high, height_low
                const srw = Number(p.width || w * 0.9);
                const srd = Number(p.depth || d * 0.9);
                const hHigh = Number(p.height_high || 0.15);
                const hLow = Number(p.height_low || 0.04);
                const srVerts = new Float32Array([
                    // Top surface (quad as 2 triangles)
                    -srw/2, hHigh, -srd/2,   srw/2, hHigh, -srd/2,   srw/2, hLow, srd/2,
                    -srw/2, hHigh, -srd/2,   srw/2, hLow, srd/2,    -srw/2, hLow, srd/2,
                    // High side (front gable)
                    -srw/2, 0, -srd/2,       srw/2, 0, -srd/2,       srw/2, hHigh, -srd/2,
                    -srw/2, 0, -srd/2,       srw/2, hHigh, -srd/2,  -srw/2, hHigh, -srd/2,
                    // Low side (back)
                    -srw/2, 0, srd/2,        -srw/2, hLow, srd/2,    srw/2, hLow, srd/2,
                    -srw/2, 0, srd/2,         srw/2, hLow, srd/2,    srw/2, 0, srd/2,
                    // Left gable
                    -srw/2, 0, -srd/2,  -srw/2, hHigh, -srd/2,  -srw/2, hLow, srd/2,
                    -srw/2, 0, -srd/2,  -srw/2, hLow, srd/2,    -srw/2, 0, srd/2,
                    // Right gable
                     srw/2, 0, -srd/2,   srw/2, hLow, srd/2,     srw/2, hHigh, -srd/2,
                     srw/2, 0, -srd/2,   srw/2, 0, srd/2,        srw/2, hLow, srd/2,
                ]);
                const srGeo = new THREE.BufferGeometry();
                srGeo.setAttribute("position", new THREE.BufferAttribute(srVerts, 3));
                srGeo.computeVertexNormals();
                mesh = new THREE.Mesh(srGeo, mat);
                mesh.position.set(px, anchorY + py, pz);
                maxTop = Math.max(maxTop, anchorY + py + hHigh);
            } else if (p.shape === "balustrade") {
                // Railing with balusters — for terraces, parapets, galleries
                // params: width, height, baluster_count, baluster_radius
                const brw = Number(p.width || 0.5);
                const brh = Number(p.height || 0.08);
                const nBal = Math.max(3, Math.min(20, Math.round(p.baluster_count || 6)));
                const bRad = Number(p.baluster_radius || 0.008);
                const spacing = brw / (nBal + 1);
                for (let bi = 0; bi < nBal; bi++) {
                    const bx = px - brw / 2 + spacing * (bi + 1);
                    const bal = new THREE.Mesh(
                        new THREE.CylinderGeometry(bRad, bRad * 1.15, brh, 6), mat
                    );
                    bal.position.set(bx, anchorY + py + brh / 2, pz);
                    bal.castShadow = true;
                    group.add(bal);
                }
                // Top rail
                const rail = new THREE.Mesh(
                    new THREE.BoxGeometry(brw + 0.02, bRad * 2, bRad * 3), mat
                );
                rail.position.set(px, anchorY + py + brh, pz);
                rail.castShadow = true;
                group.add(rail);
                // Bottom rail
                const bRail = new THREE.Mesh(
                    new THREE.BoxGeometry(brw + 0.01, bRad * 1.5, bRad * 2.5), mat
                );
                bRail.position.set(px, anchorY + py + brh * 0.1, pz);
                group.add(bRail);
                maxTop = Math.max(maxTop, anchorY + py + brh);
                mesh = null;
            } else if (p.shape === "hemisphere") {
                // Explicit half-sphere (igloos, stupas, domes, beehive huts)
                // params: radius, segments
                const hr = Number(p.radius || 0.2);
                const hsegs = capSeg(p.segments ?? 16, 8, 32);
                mesh = new THREE.Mesh(
                    new THREE.SphereGeometry(hr, hsegs, hsegs / 2, 0, Math.PI * 2, 0, Math.PI / 2),
                    mat
                );
                mesh.position.set(px, anchorY + py, pz);
                maxTop = Math.max(maxTop, anchorY + py + hr);
            } else if (p.shape === "platform") {
                // Raised platform on stilts (stilt houses, palafitos, granaries)
                // params: width, depth, height, stilt_count, stilt_radius
                const plW = Number(p.width || 0.5);
                const plD = Number(p.depth || 0.5);
                const plH = Number(p.height || 0.3);
                const nStilts = Math.max(4, Math.min(12, Math.round(p.stilt_count || 4)));
                const stR = Number(p.stilt_radius || 0.015);
                // Platform deck
                const deck = new THREE.Mesh(
                    new THREE.BoxGeometry(plW, 0.025, plD), mat
                );
                deck.position.set(px, anchorY + py + plH, pz);
                deck.castShadow = true;
                group.add(deck);
                // Stilts — distributed in a grid pattern
                const cols = Math.ceil(Math.sqrt(nStilts));
                const rows = Math.ceil(nStilts / cols);
                for (let si = 0; si < nStilts; si++) {
                    const col = si % cols;
                    const row = Math.floor(si / cols);
                    const sx = px - plW / 2 + (plW / (cols + 1)) * (col + 1);
                    const sz = pz - plD / 2 + (plD / (rows + 1)) * (row + 1);
                    const stilt = new THREE.Mesh(
                        new THREE.CylinderGeometry(stR, stR * 1.2, plH, 6), mat
                    );
                    stilt.position.set(sx, anchorY + py + plH / 2, sz);
                    stilt.castShadow = true;
                    group.add(stilt);
                }
                maxTop = Math.max(maxTop, anchorY + py + plH + 0.025);
                mesh = null;
            } else if (p.shape === "lattice_screen") {
                // Perforated wall (jali, mashrabiya, Roman cancellum)
                // params: width, height, depth, grid_x, grid_y
                const lw = Number(p.width || 0.3);
                const lh = Number(p.height || 0.25);
                const ld = Number(p.depth || 0.015);
                const gx = Math.max(2, Math.min(10, Math.round(p.grid_x || 4)));
                const gy = Math.max(2, Math.min(10, Math.round(p.grid_y || 5)));
                const cellW = lw / gx;
                const cellH = lh / gy;
                const barW = cellW * 0.25;
                const barH = cellH * 0.25;
                // Horizontal bars
                for (let yi = 0; yi <= gy; yi++) {
                    const bar = new THREE.Mesh(
                        new THREE.BoxGeometry(lw, barH, ld), mat
                    );
                    bar.position.set(px, anchorY + py + yi * cellH, pz);
                    group.add(bar);
                }
                // Vertical bars
                for (let xi = 0; xi <= gx; xi++) {
                    const bar = new THREE.Mesh(
                        new THREE.BoxGeometry(barW, lh, ld), mat
                    );
                    bar.position.set(px - lw / 2 + xi * cellW, anchorY + py + lh / 2, pz);
                    group.add(bar);
                }
                maxTop = Math.max(maxTop, anchorY + py + lh);
                mesh = null;
            } else if (p.shape === "wedge") {
                // Triangular prism — for pediments, ramps, gable ends
                // params: width, height, depth
                const ww = Number(p.width || 0.3);
                const wh = Number(p.height || 0.15);
                const wd = Number(p.depth || 0.3);
                // Triangular cross-section along Z: base at bottom, apex at top center
                const verts = new Float32Array([
                    // Front triangle
                    -ww/2, 0, -wd/2,   ww/2, 0, -wd/2,   0, wh, -wd/2,
                    // Back triangle
                    -ww/2, 0, wd/2,    0, wh, wd/2,       ww/2, 0, wd/2,
                    // Left slope
                    -ww/2, 0, -wd/2,   0, wh, -wd/2,     0, wh, wd/2,
                    -ww/2, 0, -wd/2,   0, wh, wd/2,      -ww/2, 0, wd/2,
                    // Right slope
                    ww/2, 0, -wd/2,    0, wh, wd/2,       0, wh, -wd/2,
                    ww/2, 0, -wd/2,    ww/2, 0, wd/2,     0, wh, wd/2,
                    // Bottom
                    -ww/2, 0, -wd/2,   -ww/2, 0, wd/2,    ww/2, 0, wd/2,
                    -ww/2, 0, -wd/2,    ww/2, 0, wd/2,    ww/2, 0, -wd/2,
                ]);
                const wGeo = new THREE.BufferGeometry();
                wGeo.setAttribute("position", new THREE.BufferAttribute(verts, 3));
                wGeo.computeVertexNormals();
                mesh = new THREE.Mesh(wGeo, mat);
                mesh.position.set(px, anchorY + py, pz);
                maxTop = Math.max(maxTop, anchorY + py + wh);
            } else if (p.shape === "stairs") {
                // Procedural stepped boxes — for staircases, temple steps
                // params: width, depth, height, steps, direction (front|back|left|right)
                const stW = Number(p.width || 0.3);
                const stD = Number(p.depth || 0.3);
                const stH = Number(p.height || 0.2);
                const nSteps = Math.max(2, Math.min(20, Math.round(p.steps || 5)));
                const stepH = stH / nSteps;
                const stepD = stD / nSteps;
                for (let si = 0; si < nSteps; si++) {
                    const sw = stW;
                    const sd = stepD;
                    const step = new THREE.Mesh(
                        new THREE.BoxGeometry(sw, stepH, sd), mat
                    );
                    step.position.set(
                        px,
                        anchorY + py + si * stepH + stepH / 2,
                        pz - stD / 2 + si * stepD + stepD / 2
                    );
                    step.castShadow = true;
                    step.receiveShadow = true;
                    group.add(step);
                }
                maxTop = Math.max(maxTop, anchorY + py + stH);
                mesh = null;
            } else if (p.shape === "ring") {
                // Annular cylinder — two concentric cylinders for ring walls, wells, amphitheaters
                // params: outerRadius, innerRadius, height, segments
                const outerR = Number(p.outerRadius || p.radius || 0.2);
                const innerR = Number(p.innerRadius || outerR * 0.7);
                const ringH = Number(p.height || 0.1);
                const rSegs = capSeg(p.segments ?? 24, 8, 48);
                // Outer cylinder
                const outerGeo = new THREE.CylinderGeometry(outerR, outerR, ringH, rSegs, 1, true);
                const outerMesh = new THREE.Mesh(outerGeo, mat);
                outerMesh.position.set(px, anchorY + py + ringH / 2, pz);
                outerMesh.castShadow = true;
                group.add(outerMesh);
                // Inner cylinder (inverted normals for inside view)
                const innerGeo = new THREE.CylinderGeometry(innerR, innerR, ringH, rSegs, 1, true);
                // Flip normals for inner surface
                const innerPositions = innerGeo.getAttribute("position");
                const innerNormals = innerGeo.getAttribute("normal");
                if (innerNormals) {
                    for (let ni = 0; ni < innerNormals.count; ni++) {
                        innerNormals.setXYZ(ni,
                            -innerNormals.getX(ni),
                            -innerNormals.getY(ni),
                            -innerNormals.getZ(ni)
                        );
                    }
                    innerNormals.needsUpdate = true;
                }
                // Reverse face winding
                const innerIdx = innerGeo.index;
                if (innerIdx) {
                    const arr = innerIdx.array;
                    for (let fi = 0; fi < arr.length; fi += 3) {
                        const tmp = arr[fi];
                        arr[fi] = arr[fi + 2];
                        arr[fi + 2] = tmp;
                    }
                    innerIdx.needsUpdate = true;
                }
                const innerMesh = new THREE.Mesh(innerGeo, mat);
                innerMesh.position.set(px, anchorY + py + ringH / 2, pz);
                group.add(innerMesh);
                // Top ring (annulus)
                const topGeo = new THREE.RingGeometry(innerR, outerR, rSegs);
                const topMesh = new THREE.Mesh(topGeo, mat);
                topMesh.rotation.x = -Math.PI / 2;
                topMesh.position.set(px, anchorY + py + ringH, pz);
                topMesh.castShadow = true;
                group.add(topMesh);
                // Bottom ring
                const botGeo = new THREE.RingGeometry(innerR, outerR, rSegs);
                const botMesh = new THREE.Mesh(botGeo, mat);
                botMesh.rotation.x = Math.PI / 2;
                botMesh.position.set(px, anchorY + py, pz);
                group.add(botMesh);
                maxTop = Math.max(maxTop, anchorY + py + ringH);
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

    // Deterministic per-building variation — jitters colors AND structural numbers
    // so same-type buildings don't look identical. Keyed on anchor hash (stable).
    _applyInstanceVariation(components, seed, buildingType) {
        // LCG producing a stable sequence of numbers in [0, 1) from seed.
        let s = (seed | 0) >>> 0;
        const rand = () => {
            s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
            return s / 0xFFFFFFFF;
        };
        const rAsym = () => rand() * 2 - 1;           // [-1, 1)
        const jitterInt = (v, amount) => {
            if (!Number.isFinite(v)) return v;
            const delta = Math.round(rAsym() * amount);
            return Math.max(1, v + delta);
        };
        const jitterFloat = (v, frac) => {
            if (!Number.isFinite(v)) return v;
            return Math.max(0.001, v * (1 + rAsym() * frac));
        };
        // Per-building uniform color shift — aggressive to make buildings visibly distinct
        // All hex colors shifted together so the building feels like one palette.
        const lightShift = rAsym() * 0.28;     // ±28% lightness (was ±14%)
        const hueShift = rAsym() * 0.055;      // ±20° (was ±8°)
        const satShift = rAsym() * 0.22;       // ±22% saturation (was ±12%)

        const tmpColor = new THREE.Color();
        const jitterHex = (hex) => {
            if (typeof hex !== "string" || !/^#[0-9a-fA-F]{6}$/.test(hex)) return hex;
            tmpColor.set(hex);
            const hsl = { h: 0, s: 0, l: 0 };
            tmpColor.getHSL(hsl);
            hsl.h = (hsl.h + hueShift + 1) % 1;
            hsl.s = Math.max(0, Math.min(1, hsl.s + satShift));
            hsl.l = Math.max(0.05, Math.min(0.95, hsl.l + lightShift));
            tmpColor.setHSL(hsl.h, hsl.s, hsl.l);
            return "#" + tmpColor.getHexString();
        };

        // Whether this building-type allows columns (sacred/civic only).
        // Utilitarian types get colonnade/pediment STRIPPED even if the AI emits them.
        const btype = (buildingType || "").toLowerCase();
        const SACRED_OR_CIVIC = new Set(["temple", "basilica", "monument", "stoa"]);
        const stripColumns = btype && !SACRED_OR_CIVIC.has(btype)
            && !btype.includes("temple") && !btype.includes("forum");

        const out = [];
        for (const c of components) {
            if (!c || typeof c !== "object") { out.push(c); continue; }
            const type = String(c.type || "").toLowerCase();

            // Enforce building-type rules — drop components that don't belong
            if (stripColumns && (type === "colonnade" || type === "pediment")) {
                continue;
            }

            const clone = Object.assign({}, c);

            // Jitter color fields
            for (const key of ["color", "pedestalColor", "windowColor", "postColor",
                               "architraveColor", "corniceColor", "gableColor",
                               "parapetColor", "trimColor", "frameColor"]) {
                if (typeof clone[key] === "string") clone[key] = jitterHex(clone[key]);
            }

            // STRUCTURAL jitter — per-component-type
            if (type === "block") {
                if (Number.isFinite(clone.stories)) clone.stories = jitterInt(clone.stories, 1);
                if (Number.isFinite(clone.windows)) clone.windows = jitterInt(clone.windows, 2);
                if (Number.isFinite(clone.storyHeight)) clone.storyHeight = jitterFloat(clone.storyHeight, 0.12);
                // Variant selector for visual diversity — 7 different facade treatments
                const variants = ["plain", "arched_windows", "articulated", "tabernae_ground", "balconies", "courtyard", "overhang"];
                clone._variant = variants[Math.floor(rand() * variants.length)];
                // Rustication on base floor
                clone._rusticated = rand() < 0.35;
                // Which story gets a balcony (if balconies variant)
                clone._balconyStory = Math.max(1, Math.floor(rand() * 3) + 1);
                // Overhang amount (upper stories project beyond ground floor)
                clone._overhangAmount = 0.02 + rand() * 0.03;
                // Window shutters (40% of buildings get shutters — adds huge visual variety)
                clone._hasShutters = rand() < 0.40;
                // Chimney (25% chance for non-temple utilitarian buildings)
                clone._hasChimney = stripColumns && rand() < 0.25;
            } else if (type === "colonnade") {
                if (Number.isFinite(clone.columns)) {
                    clone.columns = Math.max(4, jitterInt(clone.columns, 2));
                }
                if (Number.isFinite(clone.height)) clone.height = jitterFloat(clone.height, 0.08);
            } else if (type === "podium") {
                if (Number.isFinite(clone.steps)) clone.steps = Math.max(1, jitterInt(clone.steps, 1));
                if (Number.isFinite(clone.height)) clone.height = jitterFloat(clone.height, 0.1);
            } else if (type === "arcade") {
                if (Number.isFinite(clone.arches)) clone.arches = Math.max(1, jitterInt(clone.arches, 1));
                if (Number.isFinite(clone.height)) clone.height = jitterFloat(clone.height, 0.08);
            } else if (type === "pilasters") {
                if (Number.isFinite(clone.count)) clone.count = Math.max(2, jitterInt(clone.count, 1));
            } else if (type === "tiled_roof" || type === "pediment" || type === "flat_roof" || type === "dome" || type === "hipped_roof") {
                if (Number.isFinite(clone.height)) clone.height = jitterFloat(clone.height, 0.1);
                if (Number.isFinite(clone.radius)) clone.radius = jitterFloat(clone.radius, 0.08);
                // For non-temple utilitarian/residential buildings, 35% chance to swap
                // tiled_roof → hipped_roof for skyline diversity.
                if (type === "tiled_roof" && stripColumns && rand() < 0.35) {
                    clone.type = "hipped_roof";
                }
            } else if (type === "walls") {
                if (Number.isFinite(clone.height)) clone.height = jitterFloat(clone.height, 0.1);
            }

            // Jitter roughness/surface_detail for material variety
            if (Number.isFinite(clone.roughness)) {
                clone.roughness = Math.max(0.05, Math.min(0.98, clone.roughness + rAsym() * 0.08));
            }
            if (Number.isFinite(clone.surface_detail)) {
                clone.surface_detail = Math.max(0, Math.min(1, clone.surface_detail + rAsym() * 0.08));
            }

            // Recursively jitter procedural parts (colors only — positions would break layout)
            if (Array.isArray(clone.parts)) {
                clone.parts = clone.parts.map((p) => {
                    if (!p || typeof p !== "object") return p;
                    const pClone = Object.assign({}, p);
                    if (typeof pClone.color === "string") pClone.color = jitterHex(pClone.color);
                    return pClone;
                });
            }
            out.push(clone);
        }

        // Auto-inject staircase onto insulae/residential buildings (30% chance)
        // This adds external stairs on the side for accessing upper floors.
        if (stripColumns && rand() < 0.30) {
            const hasBlock = out.some(c => c.type === "block" && (c.stories || 2) >= 2);
            if (hasBlock) {
                const block = out.find(c => c.type === "block");
                out.push({
                    type: "staircase",
                    height: (block.stories || 2) * (block.storyHeight || 0.3) * 0.7,
                    color: jitterHex("#808080"),
                    side: rand() < 0.5 ? "left" : "right",
                    stack_role: "decorative",
                });
            }
        }
        return out;
    }

    // Fluted column shaft — classical Greek/Roman detail
    // Applies a cosine wave to the radius around the circumference to create vertical flutes.
    _buildFlutedShaftGeometry(rTop, rBot, h, segments, flutes, fluteDepth) {
        const geom = new THREE.CylinderGeometry(rTop, rBot, h, segments);
        const pos = geom.attributes.position;
        for (let i = 0; i < pos.count; i++) {
            const x = pos.getX(i);
            const z = pos.getZ(i);
            const theta = Math.atan2(z, x);
            const r = Math.hypot(x, z);
            if (r < 0.0001) continue;
            // cos(flutes * theta): +1 at fillets (ridges between flutes), -1 at flute valleys
            // We want the radius to dip slightly at the flute valleys:
            const wave = Math.cos(flutes * theta);
            const newR = r - fluteDepth * (1 - wave) * 0.5;
            pos.setX(i, Math.cos(theta) * newR);
            pos.setZ(i, Math.sin(theta) * newR);
        }
        geom.computeVertexNormals();
        return geom;
    }

    // Stepped platform with edge moldings
    _buildPodium(group, comp, baseY, w, d) {
        const steps = comp.steps || 3;
        const totalH = comp.height || steps * 0.06;
        const stepH = totalH / steps;
        const color = comp.color || "#c8b88a";
        const mat = this._matPBR(comp, color, 0.75);
        const topMat = this._matPBR(comp, color, 0.65);

        // Top molding is drawn slightly darker/lighter for contrast
        for (let i = 0; i < steps; i++) {
            const shrink = (i / steps) * 0.08;
            const step = new THREE.Mesh(
                new THREE.BoxGeometry(w - shrink, stepH, d - shrink),
                i === steps - 1 ? topMat : mat
            );
            step.position.y = baseY + i * stepH + stepH / 2;
            group.add(step);
        }

        // Top crown molding (small projecting lip at the top — defines the podium cap)
        const crownH = Math.min(0.015, stepH * 0.3);
        const lastShrink = ((steps - 1) / steps) * 0.08;
        const crownW = w - lastShrink + 0.012;
        const crownD = d - lastShrink + 0.012;
        const crown = new THREE.Mesh(
            new THREE.BoxGeometry(crownW, crownH, crownD),
            topMat
        );
        crown.position.y = baseY + totalH + crownH / 2;
        group.add(crown);

        return baseY + totalH + crownH;
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
        // Cap segments: very thin columns need few; thick columns cap at 48 for GPU cost
        // Higher segment count is needed for fluting
        const shaftSegs = Math.min(48, Math.max(12, Math.round(r * 300)));
        // Number of flutes: Doric=20, Ionic/Corinthian=24 (historical standard)
        const numFlutes = style === "doric" ? 20 : 24;
        // Flute depth as fraction of radius (shallow grooves)
        const fluteDepth = r * 0.06;
        const shaftGeom = this._buildFlutedShaftGeometry(r * 0.83, r, colH, shaftSegs, numFlutes, fluteDepth);
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
        // Architrave band (horizontal entablature below the pediment)
        const archH = peakH * 0.28;
        const archMat = this._matPBR(comp, comp.architraveColor || color, 0.7);
        const architrave = new THREE.Mesh(
            new THREE.BoxGeometry(w + 0.04, archH, d + 0.04),
            archMat
        );
        architrave.position.set(0, baseY + archH / 2, 0);
        group.add(architrave);

        const pedBaseY = baseY + archH;
        const hw = w / 2, hd = d / 2;

        // Gabled roof: ridge runs along Z, slopes on left/right, gables on front/back
        const verts = new Float32Array([
            // Left slope (two triangles)
            -hw, pedBaseY, -hd,   -hw, pedBaseY, hd,   0, pedBaseY + peakH, hd,
            -hw, pedBaseY, -hd,   0, pedBaseY + peakH, hd,   0, pedBaseY + peakH, -hd,
            // Right slope (two triangles)
            hw, pedBaseY, hd,   hw, pedBaseY, -hd,   0, pedBaseY + peakH, -hd,
            hw, pedBaseY, hd,   0, pedBaseY + peakH, -hd,   0, pedBaseY + peakH, hd,
            // Front gable (tympanum — the decorative triangle)
            -hw, pedBaseY, -hd,   0, pedBaseY + peakH, -hd,   hw, pedBaseY, -hd,
            // Back gable
            hw, pedBaseY, hd,   0, pedBaseY + peakH, hd,   -hw, pedBaseY, hd,
        ]);
        const geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(verts, 3));
        geo.computeVertexNormals();
        const roofMat = this._matPBR(comp, color, 0.65);
        group.add(new THREE.Mesh(geo, roofMat));

        // Raking cornices along both slope edges (front gable)
        const slopeLen = Math.hypot(peakH, hw);
        const slopeAngleZ = Math.atan2(peakH, hw);
        const corniceMat = this._matPBR(comp, comp.corniceColor || color, 0.6);
        for (const xSide of [-1, 1]) {
            for (const zSide of [-1, 1]) {
                const rc = new THREE.Mesh(
                    new THREE.BoxGeometry(slopeLen + 0.02, 0.025, 0.035),
                    corniceMat
                );
                rc.position.set(xSide * hw / 2, pedBaseY + peakH / 2, zSide * (hd + 0.01));
                rc.rotation.z = -xSide * slopeAngleZ;
                group.add(rc);
            }
        }

        // Ridge beam along the peak (Z direction)
        const ridge = new THREE.Mesh(
            new THREE.BoxGeometry(0.035, 0.035, d + 0.06),
            corniceMat
        );
        ridge.position.set(0, pedBaseY + peakH, 0);
        group.add(ridge);

        return pedBaseY + peakH;
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
        const variant = comp._variant || "plain";
        const rusticated = comp._rusticated === true;

        // Base plinth — darker, slightly wider band at ground level
        // Thicker if rusticated
        const plinthH = Math.min(rusticated ? 0.06 : 0.04, storyH * (rusticated ? 0.22 : 0.15));
        const plinth = new THREE.Mesh(
            new THREE.BoxGeometry(w + (rusticated ? 0.035 : 0.02), plinthH, d + (rusticated ? 0.035 : 0.02)),
            this._matPBR(comp, color, rusticated ? 0.92 : 0.85)
        );
        plinth.position.y = baseY + plinthH / 2;
        plinth.material.color.multiplyScalar(rusticated ? 0.68 : 0.75); // Darker base
        group.add(plinth);

        // Courtyard variant: central light-well cut out from upper stories
        const hasCourtyard = variant === "courtyard" && stories >= 2 && w > 1.2 && d > 1.2;
        const courtyardW = hasCourtyard ? w * 0.28 : 0;
        const courtyardD = hasCourtyard ? d * 0.28 : 0;

        // --- Window instancing: pre-count total windows across all stories ---
        let totalFrontBackWins = 0;
        let totalSideWins = 0;
        for (let s = 0; s < stories; s++) {
            const isGround = s === 0;
            const shrinkPre = (variant === "overhang" && s > 0) ? -(( comp._overhangAmount || 0.03) * Math.min(s, 2)) : s * 0.015;
            const swPre = w - shrinkPre, sdPre = d - shrinkPre;
            const winWPre = Math.max(0.035, swPre * 0.05);
            const numWinPre = comp.windows || Math.max(1, Math.floor(swPre / (winWPre * 3.0)));
            const isTabGround = variant === "tabernae_ground" && isGround;
            const actualNumWinPre = isTabGround ? Math.max(1, Math.floor(numWinPre * 0.65)) : numWinPre;
            totalFrontBackWins += actualNumWinPre * 2;  // front + back
            const sideWinCountPre = Math.max(1, Math.floor(actualNumWinPre * (sdPre / swPre)));
            totalSideWins += sideWinCountPre * 2;  // left + right
        }

        // Create instanced meshes for window panes and frames (front/back)
        const paneGeom = new THREE.BoxGeometry(1, 1, 0.015);  // unit-sized, scaled per instance
        const paneMat = this._mat(windowColor, 0.38, 0.12);
        const paneInst = new THREE.InstancedMesh(paneGeom, paneMat, totalFrontBackWins);
        let paneIdx = 0;

        // Single frame backing per window (replaces 4-piece frame)
        const frameGeom = new THREE.BoxGeometry(1, 1, 0.025);  // unit-sized, scaled per instance
        const frameMatShared = this._matPBR(comp, color, 0.8);
        frameMatShared.color.multiplyScalar(0.82);
        const frameInst = new THREE.InstancedMesh(frameGeom, frameMatShared, totalFrontBackWins);
        let frameIdx = 0;

        // Side window panes (oriented along Z axis, so geometry is rotated)
        const sidePaneGeom = new THREE.BoxGeometry(0.015, 1, 1);  // unit-sized, scaled per instance
        const sidePaneInst = new THREE.InstancedMesh(sidePaneGeom, paneMat, totalSideWins);
        let sidePaneIdx = 0;

        const dummy = this._instDummy;

        for (let s = 0; s < stories; s++) {
            // Overhang variant: upper floors project outward (negative shrink)
            const isOverhang = variant === "overhang" && s > 0;
            const ovh = comp._overhangAmount || 0.03;
            const shrink = isOverhang ? -(ovh * Math.min(s, 2)) : s * 0.015;
            const sw = w - shrink, sd = d - shrink;
            const storyBaseY = baseY + s * storyH;

            if (hasCourtyard && s > 0) {
                // Build 4 wall segments around the courtyard instead of a solid block
                const wt = (sw - courtyardW) / 2; // wall thickness around courtyard
                const dt = (sd - courtyardD) / 2;
                const wallMat = this._matPBR(comp, color, 0.72);
                const wallH = storyH - 0.01;
                const wy = storyBaseY + storyH / 2;
                // Front slab (between front edge and courtyard)
                const fs = new THREE.Mesh(new THREE.BoxGeometry(sw, wallH, dt), wallMat);
                fs.position.set(0, wy, -sd / 2 + dt / 2);
                group.add(fs);
                // Back slab
                const bs = new THREE.Mesh(new THREE.BoxGeometry(sw, wallH, dt), wallMat);
                bs.position.set(0, wy, sd / 2 - dt / 2);
                group.add(bs);
                // Left slab (fills between front and back slabs, minus courtyard)
                const ls = new THREE.Mesh(new THREE.BoxGeometry(wt, wallH, courtyardD), wallMat);
                ls.position.set(-sw / 2 + wt / 2, wy, 0);
                group.add(ls);
                // Right slab
                const rs = new THREE.Mesh(new THREE.BoxGeometry(wt, wallH, courtyardD), wallMat);
                rs.position.set(sw / 2 - wt / 2, wy, 0);
                group.add(rs);
            } else {
                const wall = new THREE.Mesh(
                    new THREE.BoxGeometry(sw, storyH - 0.01, sd),
                    this._matPBR(comp, color, 0.72)
                );
                wall.position.y = storyBaseY + storyH / 2;
                group.add(wall);
            }

            // Windows — instanced panes and frames for performance
            const winW = Math.max(0.035, sw * 0.05);
            const winH = storyH * 0.4;
            const frameT = Math.max(0.008, winW * 0.12);
            const numWin = comp.windows || Math.max(1, Math.floor(sw / (winW * 3.0)));

            // Ground floor windows are taller (shops); upper floors smaller (apartments)
            const isGround = s === 0;
            const isTabGround = variant === "tabernae_ground" && isGround;
            const winHActual = isTabGround
                ? storyH * 0.75
                : (isGround ? winH * 1.3 : winH * (0.85 + (s % 3) * 0.08));
            const winYOffset = isTabGround
                ? storyH * 0.45
                : (isGround ? storyH * 0.5 : storyH * 0.6);
            const actualNumWin = isTabGround ? Math.max(1, Math.floor(numWin * 0.65)) : numWin;
            const actualWinW = isTabGround ? winW * 2.2 : winW;
            const actualSpacing = sw / (actualNumWin + 1);
            const isArched = variant === "arched_windows" && s > 0;

            // frameMat only needed for non-instanced items (arches, shutters, articulated panels)
            const frameMat = this._matPBR(comp, color, 0.8);
            frameMat.color.multiplyScalar(0.82);

            for (let wi = 0; wi < actualNumWin; wi++) {
                const wx = -sw / 2 + actualSpacing * (wi + 1);
                const wy = storyBaseY + winYOffset;

                // Front and back windows — instanced panes and frames
                for (const zSide of [-1, 1]) {
                    const wz = zSide * (sd / 2 + 0.003);

                    // Window pane instance (unit geom scaled to actual size)
                    dummy.position.set(wx, wy, wz - zSide * 0.012);
                    dummy.rotation.set(0, 0, 0);
                    dummy.scale.set(actualWinW, winHActual, 1);
                    dummy.updateMatrix();
                    paneInst.setMatrixAt(paneIdx++, dummy.matrix);

                    // Frame backing instance (slightly larger than pane, behind it)
                    dummy.position.set(wx, wy, wz);
                    dummy.rotation.set(0, 0, 0);
                    dummy.scale.set(actualWinW + frameT * 2, winHActual + frameT * 2, 1);
                    dummy.updateMatrix();
                    frameInst.setMatrixAt(frameIdx++, dummy.matrix);

                    // Arched variant: add half-torus above window (low count, keep as Mesh)
                    if (isArched) {
                        const archR = actualWinW / 2 + frameT;
                        const archTube = frameT * 0.7;
                        const arch = new THREE.Mesh(
                            new THREE.TorusGeometry(archR, archTube, 4, 10, Math.PI),
                            frameMat
                        );
                        arch.rotation.x = -Math.PI / 2;
                        arch.rotation.z = 0;
                        arch.position.set(wx, wy + winHActual / 2 + frameT, wz);
                        group.add(arch);
                    }
                    // Window shutters (low count, keep as Mesh)
                    if (comp._hasShutters && !isTabGround) {
                        const shutterW = actualWinW * 0.5;
                        const shutterH = winHActual * 0.95;
                        const shutterMat = this._matPBR(comp, comp.windowColor || "#6B4226", 0.72);
                        for (const xSide of [-1, 1]) {
                            const shutter = new THREE.Mesh(
                                new THREE.BoxGeometry(shutterW, shutterH, 0.012),
                                shutterMat
                            );
                            const sx = wx + xSide * (actualWinW / 2 + shutterW / 2 * 0.7);
                            shutter.position.set(sx, wy, wz + zSide * 0.018);
                            shutter.rotation.y = xSide * 0.5;
                            group.add(shutter);
                        }
                    }
                }
            }

            // Side windows (left and right) — instanced panes
            const sideWinCount = Math.max(1, Math.floor(actualNumWin * (sd / sw)));
            const sideWinSpacing = sd / (sideWinCount + 1);
            for (let wi = 0; wi < sideWinCount; wi++) {
                const wz = -sd / 2 + sideWinSpacing * (wi + 1);
                const wy = storyBaseY + winYOffset;
                for (const xSide of [-1, 1]) {
                    const wx = xSide * (sw / 2 + 0.003);
                    dummy.position.set(wx - xSide * 0.012, wy, wz);
                    dummy.rotation.set(0, 0, 0);
                    dummy.scale.set(1, winHActual, actualWinW);
                    dummy.updateMatrix();
                    sidePaneInst.setMatrixAt(sidePaneIdx++, dummy.matrix);
                }
            }

            // Articulated variant: add blind arch/panel between windows
            if (variant === "articulated" && s < stories - 1) {
                for (let wi = 0; wi < actualNumWin; wi++) {
                    const wx = -sw / 2 + actualSpacing * (wi + 1);
                    for (const zSide of [-1, 1]) {
                        const panel = new THREE.Mesh(
                            new THREE.BoxGeometry(actualSpacing * 0.55, storyH * 0.12, 0.015),
                            frameMat
                        );
                        panel.position.set(wx - actualSpacing / 2, storyBaseY + storyH * 0.88, zSide * (sd / 2 + 0.002));
                        group.add(panel);
                    }
                }
            }

            // Balconies variant: projecting balcony on the front face at a specific story
            if (variant === "balconies" && s === (comp._balconyStory || 1) && s < stories) {
                const balconyD = 0.055;
                const balconyH = 0.04;
                const balconyY = storyBaseY + storyH * 0.05;
                const balconyW = sw * 0.7;
                // Balcony slab (projects outward from front face)
                const slab = new THREE.Mesh(
                    new THREE.BoxGeometry(balconyW, balconyH, balconyD),
                    frameMat
                );
                slab.position.set(0, balconyY + balconyH / 2, -sd / 2 - balconyD / 2 + 0.003);
                group.add(slab);
                // Railing balusters
                const railH = storyH * 0.22;
                const numBalusters = Math.max(4, Math.floor(balconyW / 0.08));
                for (let b = 0; b <= numBalusters; b++) {
                    const bx = -balconyW / 2 + (balconyW / numBalusters) * b;
                    const baluster = new THREE.Mesh(
                        new THREE.BoxGeometry(0.015, railH, 0.015),
                        frameMat
                    );
                    baluster.position.set(bx, balconyY + balconyH + railH / 2, -sd / 2 - balconyD + 0.01);
                    group.add(baluster);
                }
                // Top rail
                const topRail = new THREE.Mesh(
                    new THREE.BoxGeometry(balconyW + 0.02, 0.012, 0.025),
                    frameMat
                );
                topRail.position.set(0, balconyY + balconyH + railH, -sd / 2 - balconyD + 0.01);
                group.add(topRail);
                // Support brackets under the balcony
                for (const bx of [-balconyW * 0.35, 0, balconyW * 0.35]) {
                    const bracket = new THREE.Mesh(
                        new THREE.BoxGeometry(0.02, 0.03, balconyD * 0.9),
                        frameMat
                    );
                    bracket.position.set(bx, balconyY - 0.015, -sd / 2 - balconyD / 2 + 0.003);
                    group.add(bracket);
                }
            }

            // Cornice between stories — stronger profile than flat ledge
            if (s > 0) {
                const corniceH = 0.022;
                const cornice = new THREE.Mesh(
                    new THREE.BoxGeometry(sw + 0.04, corniceH, sd + 0.04),
                    this._matPBR(comp, color, 0.7)
                );
                cornice.position.y = storyBaseY;
                group.add(cornice);
                // Shadow-line under cornice
                const shadow = new THREE.Mesh(
                    new THREE.BoxGeometry(sw + 0.02, 0.008, sd + 0.02),
                    this._matPBR(comp, color, 0.85)
                );
                shadow.material.color.multiplyScalar(0.72);
                shadow.position.y = storyBaseY - corniceH / 2 - 0.004;
                group.add(shadow);
            }
        }

        // --- Finalize window instanced meshes and add to group ---
        if (paneIdx > 0) {
            paneInst.count = paneIdx;  // trim to actual count used
            paneInst.instanceMatrix.needsUpdate = true;
            group.add(paneInst);
        }
        if (frameIdx > 0) {
            frameInst.count = frameIdx;
            frameInst.instanceMatrix.needsUpdate = true;
            group.add(frameInst);
        }
        if (sidePaneIdx > 0) {
            sidePaneInst.count = sidePaneIdx;
            sidePaneInst.instanceMatrix.needsUpdate = true;
            group.add(sidePaneInst);
        }

        // Top cornice — strong profile before roof
        const topCorniceH = 0.03;
        const topCornice = new THREE.Mesh(
            new THREE.BoxGeometry(w + 0.05, topCorniceH, d + 0.05),
            this._matPBR(comp, color, 0.65)
        );
        topCornice.position.y = baseY + totalH + topCorniceH / 2;
        group.add(topCornice);

        // Chimney — small cylinder on one corner of the roof
        if (comp._hasChimney) {
            const chimneyH = Math.max(0.06, totalH * 0.12);
            const chimneyR = 0.022;
            const chimney = new THREE.Mesh(
                new THREE.CylinderGeometry(chimneyR, chimneyR * 1.1, chimneyH, 6),
                this._matPBR(comp, "#4A4A4A", 0.88)
            );
            chimney.position.set(w * 0.3, baseY + totalH + topCorniceH + chimneyH / 2, d * 0.25);
            group.add(chimney);
            // Chimney cap
            const cap = new THREE.Mesh(
                new THREE.BoxGeometry(chimneyR * 3, 0.008, chimneyR * 3),
                this._matPBR(comp, "#4A4A4A", 0.7)
            );
            cap.position.set(w * 0.3, baseY + totalH + topCorniceH + chimneyH + 0.004, d * 0.25);
            group.add(cap);
        }

        return baseY + totalH + topCorniceH;
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
        const mat = this._matPBR(comp, color, 0.72);
        const trimMat = this._matPBR(comp, comp.trimColor || color, 0.65);

        // Pillars between arches (with wider base plinth)
        const plinthH = Math.min(0.04, pillarH * 0.08);
        for (let i = 0; i <= numArches; i++) {
            const px = -w / 2 + i * archSpacing;
            // Base plinth
            const plinth = new THREE.Mesh(
                new THREE.BoxGeometry(pillarW + 0.02, plinthH, d + 0.01),
                trimMat
            );
            plinth.position.set(px, baseY + plinthH / 2, 0);
            group.add(plinth);
            // Main pillar shaft
            const pillar = new THREE.Mesh(
                new THREE.BoxGeometry(pillarW, pillarH - plinthH, d),
                mat
            );
            pillar.position.set(px, baseY + plinthH + (pillarH - plinthH) / 2, 0);
            group.add(pillar);
            // Impost block (cap on pier where arch springs from)
            const impostH = Math.min(0.025, pillarH * 0.07);
            const impost = new THREE.Mesh(
                new THREE.BoxGeometry(pillarW + 0.015, impostH, d + 0.005),
                trimMat
            );
            impost.position.set(px, baseY + pillarH - impostH / 2, 0);
            group.add(impost);
        }

        // Arch semicircles on front and back faces + spandrel fill
        const archTube = Math.max(0.022, pillarW * 0.28);
        for (let i = 0; i < numArches; i++) {
            const cx = -w / 2 + (i + 0.5) * archSpacing;
            for (const z of [-d / 2, d / 2]) {
                // Arch voussoir ring
                const arch = new THREE.Mesh(
                    new THREE.TorusGeometry(archR, archTube, 6, 16, Math.PI),
                    trimMat
                );
                arch.rotation.x = -Math.PI / 2;
                arch.position.set(cx, baseY + pillarH, z);
                group.add(arch);
                // Keystone (wedge at top of arch)
                const keyW = Math.max(0.025, archR * 0.12);
                const keystone = new THREE.Mesh(
                    new THREE.BoxGeometry(keyW, archTube * 2.2, archTube * 1.5),
                    trimMat
                );
                keystone.position.set(cx, baseY + pillarH + archR - archTube * 0.2, z);
                group.add(keystone);
            }
            // Spandrel (flat wall filling the space above the arch between piers)
            const spandrelW = archSpacing - pillarW;
            const spandrelH = Math.max(0.01, totalH - pillarH - archR);
            if (spandrelH > 0.005) {
                const spandrel = new THREE.Mesh(
                    new THREE.BoxGeometry(spandrelW, spandrelH, d + 0.005),
                    mat
                );
                spandrel.position.set(cx, baseY + pillarH + archR + spandrelH / 2, 0);
                group.add(spandrel);
            }
        }

        // Top cornice beam
        const beamH = 0.04;
        const beam = new THREE.Mesh(
            new THREE.BoxGeometry(w + 0.03, beamH, d + 0.03),
            trimMat
        );
        beam.position.y = baseY + totalH + beamH / 2;
        group.add(beam);

        return baseY + totalH + beamH;
    }

    // Angled tile roof
    _buildTiledRoof(group, comp, baseY, w, d) {
        const color = comp.color || "#b5651d";
        const peakH = comp.height || w * 0.2;
        const slopeAngle = Math.atan2(peakH, d * 0.5);
        const slopeLen = Math.hypot(peakH, d * 0.5);

        // Two sloped surfaces — each panel spans eave to ridge
        for (const side of [-1, 1]) {
            const slope = new THREE.Mesh(
                new THREE.BoxGeometry(w + 0.04, 0.025, slopeLen),
                this._matPBR(comp, color, 0.75)
            );
            slope.position.set(0, baseY + peakH / 2, side * d / 4);
            slope.rotation.x = side * slopeAngle;
            group.add(slope);
        }

        // Gable end triangles close the roof at the left/right faces
        const hw = w / 2, hd = d / 2;
        const gableVerts = new Float32Array([
            // Left gable (x = -hw)
            -hw, baseY, -hd,  -hw, baseY, hd,  -hw, baseY + peakH, 0,
            // Right gable (x = +hw) — reversed winding so normal faces +X
             hw, baseY, hd,   hw, baseY, -hd,  hw, baseY + peakH, 0,
        ]);
        const gableGeo = new THREE.BufferGeometry();
        gableGeo.setAttribute("position", new THREE.BufferAttribute(gableVerts, 3));
        gableGeo.computeVertexNormals();
        const gableColor = comp.gableColor || "#d4a373";
        group.add(new THREE.Mesh(gableGeo, this._matPBR(comp, gableColor, 0.75)));

        // Tile course ridges (imbrex pattern) — horizontal ridges running along the slope
        const numCourses = Math.max(3, Math.min(8, Math.round(slopeLen / 0.08)));
        const courseSpacing = slopeLen / (numCourses + 1);
        const tileRidgeMat = this._matPBR(comp, color, 0.7);
        tileRidgeMat.color.multiplyScalar(0.88);
        for (const side of [-1, 1]) {
            for (let i = 1; i <= numCourses; i++) {
                const frac = i / (numCourses + 1);
                const courseY = baseY + peakH * (1 - frac);
                const courseZ = side * (d / 2) * frac;
                const courseLen = w + 0.02;
                const ridge = new THREE.Mesh(
                    new THREE.BoxGeometry(courseLen, 0.012, 0.018),
                    tileRidgeMat
                );
                ridge.position.set(0, courseY, courseZ);
                ridge.rotation.x = side * slopeAngle;
                group.add(ridge);
            }
        }

        // Ridge beam along the peak (runs along X)
        const ridge = new THREE.Mesh(
            new THREE.BoxGeometry(w + 0.06, 0.04, 0.045),
            this._matPBR(comp, color, 0.55)
        );
        ridge.position.y = baseY + peakH + 0.005;
        group.add(ridge);

        // Eave cornice along front and back edges
        for (const side of [-1, 1]) {
            const eave = new THREE.Mesh(
                new THREE.BoxGeometry(w + 0.08, 0.025, 0.06),
                this._matPBR(comp, color, 0.8)
            );
            eave.position.set(0, baseY - 0.005, side * (d * 0.5 + 0.015));
            group.add(eave);
        }

        return baseY + peakH;
    }

    // Four-slope hipped roof (pyramidal / half-hipped)
    // Completely different silhouette from tiled_roof — 4 sloped faces meeting at a ridge.
    _buildHippedRoof(group, comp, baseY, w, d) {
        const color = comp.color || "#b5651d";
        const peakH = comp.height || Math.min(w, d) * 0.25;
        const hw = w / 2 + 0.02, hd = d / 2 + 0.02;
        const ridgeRunsX = w >= d;
        // Ridge length: half the shorter footprint dimension (creates 4 proper slopes)
        const ridgeHalfLen = ridgeRunsX
            ? Math.max(0, (hw - hd) * 0.9)
            : Math.max(0, (hd - hw) * 0.9);
        const topY = baseY + peakH;
        // 4 eave corners
        const fl = { x: -hw, y: baseY, z: -hd };
        const fr = { x:  hw, y: baseY, z: -hd };
        const br = { x:  hw, y: baseY, z:  hd };
        const bl = { x: -hw, y: baseY, z:  hd };
        // Ridge endpoints
        const r1 = ridgeRunsX
            ? { x: -ridgeHalfLen, y: topY, z: 0 }
            : { x: 0, y: topY, z: -ridgeHalfLen };
        const r2 = ridgeRunsX
            ? { x:  ridgeHalfLen, y: topY, z: 0 }
            : { x: 0, y: topY, z:  ridgeHalfLen };

        // Triangles for the 4 slopes. Each slope is two triangles (or one if it degenerates to a hip).
        const verts = [];
        const push = (a, b, c) => {
            verts.push(a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z);
        };
        if (ridgeRunsX) {
            // Front slope (z=-hd → ridge): fl→fr→r2, fl→r2→r1
            push(fl, fr, r2); push(fl, r2, r1);
            // Back slope: br→bl→r1, br→r1→r2
            push(br, bl, r1); push(br, r1, r2);
            // Left hip (single triangle): bl→fl→r1
            push(bl, fl, r1);
            // Right hip (single triangle): fr→br→r2
            push(fr, br, r2);
        } else {
            // Z-ridge: left/right are trapezoids, front/back are triangles
            // Left slope: fl→bl→r2, fl→r2→r1
            push(fl, bl, r2); push(fl, r2, r1);
            // Right slope: br→fr→r1, br→r1→r2
            push(br, fr, r1); push(br, r1, r2);
            // Front hip (triangle): fl→fr→r1? No, we need normal facing -Z.
            // fl=(−hw,−hd), fr=(+hw,−hd), r1=(0,+topY,−ridgeHalfLen). Winding: fr,fl,r1 → normal -Z
            push(fr, fl, r1);
            // Back hip: bl→br→r2
            push(bl, br, r2);
        }

        const geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(verts), 3));
        geo.computeVertexNormals();
        group.add(new THREE.Mesh(geo, this._matPBR(comp, color, 0.72)));

        // Ridge beam
        if (ridgeHalfLen > 0.02) {
            const ridgeMat = this._matPBR(comp, color, 0.58);
            const ridge = new THREE.Mesh(
                ridgeRunsX
                    ? new THREE.BoxGeometry(ridgeHalfLen * 2 + 0.03, 0.03, 0.045)
                    : new THREE.BoxGeometry(0.045, 0.03, ridgeHalfLen * 2 + 0.03),
                ridgeMat
            );
            ridge.position.set(0, topY + 0.005, 0);
            group.add(ridge);
        }

        // Eave cornice around the perimeter (bottom edge of all 4 slopes)
        const eaveMat = this._matPBR(comp, color, 0.8);
        const perim = [[fl, fr], [fr, br], [br, bl], [bl, fl]];
        for (const [a, b] of perim) {
            const dx = b.x - a.x, dz = b.z - a.z;
            const len = Math.hypot(dx, dz);
            const eave = new THREE.Mesh(
                new THREE.BoxGeometry(len, 0.022, 0.055),
                eaveMat
            );
            eave.position.set((a.x + b.x) / 2, baseY - 0.005, (a.z + b.z) / 2);
            eave.rotation.y = Math.atan2(-dz, dx);
            group.add(eave);
        }

        return topY;
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

    // Figure on pedestal — stepped pedestal with draped figure, obelisk, or equestrian
    _buildStatue(group, comp, baseY) {
        const totalH = comp.height || 0.5;
        const color = comp.color || "#c0b090";
        const pedColor = comp.pedestalColor || "#8a7e6e";
        const pedH = totalH * 0.35;
        const figH = totalH * 0.6;
        const figMat = this._matPBR(comp, color, 0.4, 0.08);
        const pedMat = this._matPBR(comp, pedColor, 0.82);
        const pedCapMat = this._matPBR(comp, pedColor, 0.72);

        // Stepped pedestal — three tiers
        const pedBase = 0.16;
        const pedMid = 0.13;
        const pedTop = 0.115;
        const tier1H = pedH * 0.25;
        const tier2H = pedH * 0.6;
        const tier3H = pedH * 0.15;
        // Base block
        const b1 = new THREE.Mesh(new THREE.BoxGeometry(pedBase, tier1H, pedBase), pedMat);
        b1.position.y = baseY + tier1H / 2;
        group.add(b1);
        // Main die (inscription block)
        const b2 = new THREE.Mesh(new THREE.BoxGeometry(pedMid, tier2H, pedMid), pedMat);
        b2.position.y = baseY + tier1H + tier2H / 2;
        group.add(b2);
        // Cornice cap
        const b3 = new THREE.Mesh(new THREE.BoxGeometry(pedBase, tier3H, pedBase), pedCapMat);
        b3.position.y = baseY + tier1H + tier2H + tier3H / 2;
        group.add(b3);

        const figBaseY = baseY + pedH;
        const shape = comp.shape || "figure";

        if (shape === "obelisk" || shape === "column") {
            // Tall tapered obelisk with pyramidal cap
            const obH = figH * 1.4;
            const obBot = 0.05;
            const obTop = 0.028;
            const obelisk = new THREE.Mesh(
                new THREE.CylinderGeometry(obTop, obBot, obH * 0.88, 4),
                figMat
            );
            obelisk.position.y = figBaseY + obH * 0.44;
            obelisk.rotation.y = Math.PI / 4;
            group.add(obelisk);
            // Pyramidion (capstone)
            const pyramidion = new THREE.Mesh(
                new THREE.ConeGeometry(obTop * 1.1, obH * 0.12, 4),
                this._matPBR(comp, "#DAA520", 0.3, 0.25)
            );
            pyramidion.position.y = figBaseY + obH * 0.88 + obH * 0.06;
            pyramidion.rotation.y = Math.PI / 4;
            group.add(pyramidion);
        } else if (shape === "equestrian") {
            // Horse + rider
            const horseW = 0.12, horseH = 0.075, horseD = 0.05;
            const body = new THREE.Mesh(new THREE.BoxGeometry(horseW, horseH, horseD), figMat);
            body.position.y = figBaseY + figH * 0.35;
            group.add(body);
            // Horse neck/head
            const neck = new THREE.Mesh(new THREE.BoxGeometry(0.025, 0.05, 0.04), figMat);
            neck.position.set(horseW / 2 - 0.01, figBaseY + figH * 0.45, 0);
            neck.rotation.z = -0.4;
            group.add(neck);
            const hHead = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.025, 0.03), figMat);
            hHead.position.set(horseW / 2 + 0.015, figBaseY + figH * 0.55, 0);
            group.add(hHead);
            // Legs
            for (const dx of [-horseW / 2 + 0.015, horseW / 2 - 0.015]) {
                for (const dz of [-horseD / 2 + 0.008, horseD / 2 - 0.008]) {
                    const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, figH * 0.3, 4), figMat);
                    leg.position.set(dx, figBaseY + figH * 0.15, dz);
                    group.add(leg);
                }
            }
            // Rider torso
            const rider = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.022, figH * 0.32, 6), figMat);
            rider.position.y = figBaseY + figH * 0.58;
            group.add(rider);
            // Rider head
            const rHead = new THREE.Mesh(new THREE.SphereGeometry(totalH * 0.055, 8, 6), figMat);
            rHead.position.y = figBaseY + figH * 0.78;
            group.add(rHead);
            // Cape/cloak suggestion (angled slab behind rider)
            const cape = new THREE.Mesh(new THREE.BoxGeometry(0.04, figH * 0.25, 0.01), figMat);
            cape.position.set(-0.015, figBaseY + figH * 0.58, 0);
            cape.rotation.z = 0.2;
            group.add(cape);
        } else {
            // Standing figure with draped toga — suggested by cylinders with varying radii
            const legH = figH * 0.48;
            const torsoH = figH * 0.32;
            const headR = totalH * 0.07;
            // Toga/robe (wider at base, narrower at torso — classical drapery shape)
            const toga = new THREE.Mesh(
                new THREE.CylinderGeometry(0.035, 0.055, legH, 10),
                figMat
            );
            toga.position.y = figBaseY + legH / 2;
            group.add(toga);
            // Torso
            const torso = new THREE.Mesh(
                new THREE.CylinderGeometry(0.032, 0.038, torsoH, 8),
                figMat
            );
            torso.position.y = figBaseY + legH + torsoH / 2;
            group.add(torso);
            // Shoulders
            const shoulders = new THREE.Mesh(new THREE.BoxGeometry(0.085, 0.018, 0.04), figMat);
            shoulders.position.y = figBaseY + legH + torsoH - 0.015;
            group.add(shoulders);
            // Arms hanging at sides
            for (const side of [-1, 1]) {
                const arm = new THREE.Mesh(
                    new THREE.CylinderGeometry(0.008, 0.009, torsoH * 0.85, 6),
                    figMat
                );
                arm.position.set(side * 0.038, figBaseY + legH + torsoH * 0.45, 0.005);
                group.add(arm);
            }
            // Neck
            const neck = new THREE.Mesh(
                new THREE.CylinderGeometry(0.014, 0.016, 0.02, 6),
                figMat
            );
            neck.position.y = figBaseY + legH + torsoH + 0.01;
            group.add(neck);
            // Head
            const head = new THREE.Mesh(new THREE.SphereGeometry(headR, 10, 8), figMat);
            head.position.y = figBaseY + legH + torsoH + 0.02 + headR;
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
        const postColor = comp.postColor || "#6B4226";
        const awningW = w * 0.85;
        const awningD = d * 0.4;
        const awningY = baseY - 0.05;
        const extZ = -d / 2 - d * 0.15;

        // Tilted fabric/canopy
        const awning = new THREE.Mesh(
            new THREE.BoxGeometry(awningW, 0.018, awningD),
            this._matPBR(comp, color, 0.78)
        );
        awning.position.set(0, awningY, extZ);
        awning.rotation.x = 0.18;
        group.add(awning);

        // Top trim (darker edge where awning meets wall)
        const trim = new THREE.Mesh(
            new THREE.BoxGeometry(awningW + 0.02, 0.012, 0.02),
            this._matPBR(comp, color, 0.65)
        );
        trim.material.color.multiplyScalar(0.75);
        trim.position.set(0, awningY + 0.01, extZ + awningD / 2 - 0.01);
        group.add(trim);

        // Support posts at the outer corners
        const postH = Math.max(0.1, awningY - baseY + awningD * 0.2);
        const postR = 0.012;
        const postMat = this._matPBR(comp, postColor, 0.8);
        const postZ = extZ - awningD / 2 + 0.02;
        for (const side of [-1, 1]) {
            const post = new THREE.Mesh(
                new THREE.CylinderGeometry(postR, postR, postH, 6),
                postMat
            );
            post.position.set(side * awningW / 2, baseY + postH / 2, postZ);
            group.add(post);
        }
        return baseY;
    }

    // Crenellated wall top — runs around all four sides
    _buildBattlements(group, comp, baseY, w, d) {
        const color = comp.color || "#c8b88a";
        const merlonH = comp.height || 0.1;
        const merlonW = 0.06;
        const merlonT = 0.04;
        const mat = this._matPBR(comp, color, 0.72);

        // Front + back faces (run along X)
        const nX = Math.max(2, Math.floor(w / (merlonW * 2)));
        const spacingX = w / nX;
        for (let i = 0; i < nX; i++) {
            if (i % 2 === 0) {
                for (const z of [-d / 2 + merlonT / 2, d / 2 - merlonT / 2]) {
                    const m = new THREE.Mesh(new THREE.BoxGeometry(merlonW, merlonH, merlonT), mat);
                    m.position.set(-w / 2 + spacingX * (i + 0.5), baseY + merlonH / 2, z);
                    group.add(m);
                }
            }
        }
        // Left + right faces (run along Z)
        const nZ = Math.max(2, Math.floor(d / (merlonW * 2)));
        const spacingZ = d / nZ;
        for (let i = 0; i < nZ; i++) {
            if (i % 2 === 0) {
                for (const x of [-w / 2 + merlonT / 2, w / 2 - merlonT / 2]) {
                    const m = new THREE.Mesh(new THREE.BoxGeometry(merlonT, merlonH, merlonW), mat);
                    m.position.set(x, baseY + merlonH / 2, -d / 2 + spacingZ * (i + 0.5));
                    group.add(m);
                }
            }
        }
        return baseY + merlonH;
    }

    // Amphitheater seating tier — elliptical bench with seating rows
    _buildTier(group, comp, baseY, w, d) {
        const h = comp.height || 0.15;
        const color = comp.color || "#c4a860";
        const mat = this._matPBR(comp, color, 0.68);
        // Use the FULL footprint (elliptical) for proper amphitheater shape
        const rx = w * 0.48;
        const rz = d * 0.48;
        const thickness = h * 0.18;  // structural ring depth
        const rows = comp.rows || 3;
        const rowStep = thickness / rows;

        // Stack of concentric elliptical bands, each stepping up and slightly inward
        for (let row = 0; row < rows; row++) {
            const frac = row / rows;
            const innerRx = rx - rowStep * (row + 1);
            const innerRz = rz - rowStep * (row + 1);
            const outerRx = rx - rowStep * row;
            const outerRz = rz - rowStep * row;
            const rowH = h / rows;
            const rowY = baseY + row * rowH + rowH / 2;

            // Approximate ellipse as a ring using RingGeometry, extruded via many tiny arcs
            // Use TorusGeometry scaled to ellipse for efficiency
            const avgOuter = (outerRx + outerRz) / 2;
            const avgInner = (innerRx + innerRz) / 2;
            const tubeR = (avgOuter - avgInner) / 2;
            const centerR = (avgOuter + avgInner) / 2;
            const torus = new THREE.Mesh(
                new THREE.TorusGeometry(centerR, Math.max(0.012, tubeR), 6, 28),
                mat
            );
            torus.rotation.x = Math.PI / 2;
            torus.scale.set(avgOuter > 0 ? outerRx / avgOuter : 1, avgOuter > 0 ? outerRz / avgOuter : 1, 1);
            torus.position.y = rowY;
            group.add(torus);
        }
        // Darker shadow line under the tier (row riser)
        const riser = new THREE.Mesh(
            new THREE.TorusGeometry((rx + rz) / 2, h * 0.05, 4, 28),
            this._matPBR(comp, color, 0.82)
        );
        riser.material.color.multiplyScalar(0.7);
        riser.rotation.x = Math.PI / 2;
        riser.scale.set(1, rz / ((rx + rz) / 2), 1);
        riser.position.y = baseY + h * 0.05;
        group.add(riser);

        return baseY + h;
    }

    // Entrance (decorative — does not advance Y)
    _buildDoor(group, comp, baseY) {
        const doorW = comp.width || 0.1;
        const doorH = comp.height || 0.2;
        const color = comp.color || "#3a2510";
        const frameColor = comp.frameColor || "#8a7e6e";
        const frameT = Math.max(0.012, doorW * 0.14);
        const x = comp.x || 0, z = comp.z || 0;

        // Recessed door panel (slightly inset)
        const doorMat = this._matPBR(comp, color, 0.55);
        const door = new THREE.Mesh(
            new THREE.BoxGeometry(doorW, doorH, 0.025),
            doorMat
        );
        door.position.set(x, baseY + doorH / 2, z);
        group.add(door);

        // Vertical plank line down the middle (double door look)
        const plankLine = new THREE.Mesh(
            new THREE.BoxGeometry(0.004, doorH * 0.92, 0.03),
            this._matPBR(comp, color, 0.7)
        );
        plankLine.material.color.multiplyScalar(0.7);
        plankLine.position.set(x, baseY + doorH / 2, z);
        group.add(plankLine);

        // Door frame (lintel on top, jambs on sides)
        const frameMat = this._matPBR(comp, frameColor, 0.55);
        const lintel = new THREE.Mesh(
            new THREE.BoxGeometry(doorW + frameT * 2, frameT, 0.04),
            frameMat
        );
        lintel.position.set(x, baseY + doorH + frameT / 2, z);
        group.add(lintel);

        const jambL = new THREE.Mesh(
            new THREE.BoxGeometry(frameT, doorH + frameT, 0.04),
            frameMat
        );
        jambL.position.set(x - doorW / 2 - frameT / 2, baseY + (doorH + frameT) / 2, z);
        group.add(jambL);

        const jambR = new THREE.Mesh(
            new THREE.BoxGeometry(frameT, doorH + frameT, 0.04),
            frameMat
        );
        jambR.position.set(x + doorW / 2 + frameT / 2, baseY + (doorH + frameT) / 2, z);
        group.add(jambR);

        // Threshold stone at the base
        const threshold = new THREE.Mesh(
            new THREE.BoxGeometry(doorW + frameT * 2, 0.01, 0.045),
            frameMat
        );
        threshold.position.set(x, baseY + 0.005, z);
        group.add(threshold);

        return baseY;
    }

    // Flat pilasters on walls (decorative — does not advance Y)
    _buildPilasters(group, comp, baseY, w, d) {
        const count = comp.count || 4;
        const h = comp.height || 0.5;
        const color = comp.color || "#e0d8c8";
        const placement = comp.placement || "sides"; // "sides" | "front" | "all"
        const pilW = comp.pilasterWidth || Math.max(0.035, h * 0.08);
        const pilT = 0.045;
        const shaftMat = this._matPBR(comp, color, 0.45);
        const trimMat = this._matPBR(comp, color, 0.55);
        trimMat.color.multiplyScalar(0.92);

        // Column-like proportions: base=8%, shaft=82%, capital=10% of height
        const baseH = h * 0.08;
        const capH = h * 0.1;
        const shaftH = h - baseH - capH;

        const addPilaster = (x, y, z, rotY) => {
            const grp = new THREE.Group();
            // Base (slightly wider)
            const base = new THREE.Mesh(
                new THREE.BoxGeometry(pilW + 0.012, baseH, pilT + 0.008),
                trimMat
            );
            base.position.y = y + baseH / 2;
            grp.add(base);
            // Shaft
            const shaft = new THREE.Mesh(
                new THREE.BoxGeometry(pilW, shaftH, pilT),
                shaftMat
            );
            shaft.position.y = y + baseH + shaftH / 2;
            grp.add(shaft);
            // Capital (wider, acts as top crown)
            const cap = new THREE.Mesh(
                new THREE.BoxGeometry(pilW + 0.02, capH, pilT + 0.015),
                trimMat
            );
            cap.position.y = y + baseH + shaftH + capH / 2;
            grp.add(cap);
            grp.position.set(x, 0, z);
            if (rotY) grp.rotation.y = rotY;
            group.add(grp);
        };

        if (placement === "front" || placement === "all") {
            // Pilasters on front AND back faces (if "all") or just front
            const spacing = w / (count + 1);
            for (let i = 0; i < count; i++) {
                const x = -w / 2 + spacing * (i + 1);
                addPilaster(x, baseY, -d / 2 - 0.005, 0);
                if (placement === "all") {
                    addPilaster(x, baseY, d / 2 + 0.005, 0);
                }
            }
        }
        if (placement === "sides" || placement === "all") {
            // Pilasters on left/right faces
            const spacing = d / (count + 1);
            for (const side of [-1, 1]) {
                for (let i = 0; i < count; i++) {
                    const z = -d / 2 + spacing * (i + 1);
                    addPilaster(side * (w / 2 + 0.005), baseY, z, Math.PI / 2);
                }
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

    // Flat slab roof with parapet edge
    _buildFlatRoof(group, comp, baseY, w, d) {
        const color = comp.color || "#c8b88a";
        const overhang = comp.overhang || 0.04;
        const thickness = 0.04;
        const parapetH = comp.parapetHeight || 0.05;
        const parapetT = 0.025;
        const rw = w + overhang, rd = d + overhang;

        // Main roof slab
        const roof = new THREE.Mesh(
            new THREE.BoxGeometry(rw, thickness, rd),
            this._matPBR(comp, color, 0.72)
        );
        roof.position.y = baseY + thickness / 2;
        group.add(roof);

        // Parapet walls around the perimeter (skip if explicitly disabled)
        if (comp.parapet !== false) {
            const parapetColor = comp.parapetColor || color;
            const pMat = this._matPBR(comp, parapetColor, 0.8);
            const py = baseY + thickness + parapetH / 2;
            // Front + back
            const pf = new THREE.Mesh(new THREE.BoxGeometry(rw, parapetH, parapetT), pMat);
            pf.position.set(0, py, -rd / 2 + parapetT / 2);
            group.add(pf);
            const pb = new THREE.Mesh(new THREE.BoxGeometry(rw, parapetH, parapetT), pMat);
            pb.position.set(0, py, rd / 2 - parapetT / 2);
            group.add(pb);
            // Left + right
            const pl = new THREE.Mesh(new THREE.BoxGeometry(parapetT, parapetH, rd - parapetT * 2), pMat);
            pl.position.set(-rw / 2 + parapetT / 2, py, 0);
            group.add(pl);
            const pr = new THREE.Mesh(new THREE.BoxGeometry(parapetT, parapetH, rd - parapetT * 2), pMat);
            pr.position.set(rw / 2 - parapetT / 2, py, 0);
            group.add(pr);
            return baseY + thickness + parapetH;
        }

        return baseY + thickness;
    }

    // Temple inner chamber
    _buildCella(group, comp, baseY, w, d) {
        const h = comp.height || 0.6;
        const cellaW = comp.width || w * 0.6;
        const cellaD = comp.depth || d * 0.7;
        const color = comp.color || "#e8e0d0";
        const mat = this._matPBR(comp, color, 0.72);
        const trimMat = this._matPBR(comp, color, 0.62);
        trimMat.color.multiplyScalar(0.9);

        // Base moulding (wider band at bottom)
        const baseMouldH = Math.min(0.025, h * 0.08);
        const baseMould = new THREE.Mesh(
            new THREE.BoxGeometry(cellaW + 0.02, baseMouldH, cellaD + 0.02),
            trimMat
        );
        baseMould.position.set(0, baseY + baseMouldH / 2, 0);
        group.add(baseMould);

        // Main wall block
        const wallH = h - baseMouldH * 2;
        const wall = new THREE.Mesh(
            new THREE.BoxGeometry(cellaW, wallH, cellaD),
            mat
        );
        wall.position.set(0, baseY + baseMouldH + wallH / 2, 0);
        group.add(wall);

        // Top cornice (projects slightly outward)
        const topH = Math.min(0.03, h * 0.1);
        const top = new THREE.Mesh(
            new THREE.BoxGeometry(cellaW + 0.025, topH, cellaD + 0.025),
            trimMat
        );
        top.position.set(0, baseY + h - topH / 2, 0);
        group.add(top);

        // Narrow entrance opening on the front face (decorative recess)
        const doorW = cellaW * 0.22;
        const doorH = wallH * 0.65;
        const entrance = new THREE.Mesh(
            new THREE.BoxGeometry(doorW, doorH, 0.01),
            this._mat("#2a1810", 0.85, 0.02)
        );
        entrance.position.set(0, baseY + baseMouldH + doorH / 2, -cellaD / 2 - 0.002);
        group.add(entrance);

        return baseY + h;
    }

    // Perimeter walls with base plinth and top cornice
    _buildWalls(group, comp, baseY, w, d) {
        const h = comp.height || 0.5;
        const t = comp.thickness || 0.06;
        const color = comp.color || "#d4a373";
        const hasTrim = comp.trim !== false;
        const plinthH = hasTrim ? Math.min(0.035, h * 0.12) : 0;
        const corniceH = hasTrim ? Math.min(0.025, h * 0.08) : 0;
        const wallH = h - plinthH - corniceH;
        const wallY = baseY + plinthH;

        const wallMat = this._matPBR(comp, color, 0.72);

        const front = new THREE.Mesh(new THREE.BoxGeometry(w, wallH, t), wallMat);
        front.position.set(0, wallY + wallH / 2, -d / 2 + t / 2);
        group.add(front);

        const back = new THREE.Mesh(new THREE.BoxGeometry(w, wallH, t), wallMat);
        back.position.set(0, wallY + wallH / 2, d / 2 - t / 2);
        group.add(back);

        const left = new THREE.Mesh(new THREE.BoxGeometry(t, wallH, d), wallMat);
        left.position.set(-w / 2 + t / 2, wallY + wallH / 2, 0);
        group.add(left);

        const right = new THREE.Mesh(new THREE.BoxGeometry(t, wallH, d), wallMat);
        right.position.set(w / 2 - t / 2, wallY + wallH / 2, 0);
        group.add(right);

        if (hasTrim) {
            // Base plinth — slightly wider, darker band at ground
            const plinthMat = this._matPBR(comp, color, 0.85);
            plinthMat.color.multiplyScalar(0.75);
            // Front plinth
            const pf = new THREE.Mesh(new THREE.BoxGeometry(w + 0.02, plinthH, t + 0.02), plinthMat);
            pf.position.set(0, baseY + plinthH / 2, -d / 2 + t / 2);
            group.add(pf);
            // Back
            const pb = new THREE.Mesh(new THREE.BoxGeometry(w + 0.02, plinthH, t + 0.02), plinthMat);
            pb.position.set(0, baseY + plinthH / 2, d / 2 - t / 2);
            group.add(pb);
            // Left
            const pl = new THREE.Mesh(new THREE.BoxGeometry(t + 0.02, plinthH, d + 0.02), plinthMat);
            pl.position.set(-w / 2 + t / 2, baseY + plinthH / 2, 0);
            group.add(pl);
            // Right
            const pr = new THREE.Mesh(new THREE.BoxGeometry(t + 0.02, plinthH, d + 0.02), plinthMat);
            pr.position.set(w / 2 - t / 2, baseY + plinthH / 2, 0);
            group.add(pr);

            // Top cornice — projects slightly outward
            const corniceMat = this._matPBR(comp, color, 0.68);
            const cy = baseY + plinthH + wallH + corniceH / 2;
            const cf = new THREE.Mesh(new THREE.BoxGeometry(w + 0.03, corniceH, t + 0.02), corniceMat);
            cf.position.set(0, cy, -d / 2 + t / 2);
            group.add(cf);
            const cb = new THREE.Mesh(new THREE.BoxGeometry(w + 0.03, corniceH, t + 0.02), corniceMat);
            cb.position.set(0, cy, d / 2 - t / 2);
            group.add(cb);
            const cl = new THREE.Mesh(new THREE.BoxGeometry(t + 0.02, corniceH, d + 0.03), corniceMat);
            cl.position.set(-w / 2 + t / 2, cy, 0);
            group.add(cl);
            const cr = new THREE.Mesh(new THREE.BoxGeometry(t + 0.02, corniceH, d + 0.03), corniceMat);
            cr.position.set(w / 2 - t / 2, cy, 0);
            group.add(cr);
        }

        return baseY + h;
    }

    // External staircase — attached to the side of a building (insulae, domus)
    _buildStaircase(group, comp, baseY, w, d) {
        const totalH = comp.height || 0.5;
        const color = comp.color || "#808080";
        const steps = comp.steps || Math.max(6, Math.round(totalH / 0.04));
        const stairW = comp.width || Math.min(0.15, w * 0.25);
        const stairD = comp.depth || Math.min(0.3, d * 0.6);
        const side = comp.side || "left"; // "left" | "right"
        const stepH = totalH / steps;
        const stepD = stairD / steps;
        const mat = this._matPBR(comp, color, 0.78);
        const xOff = side === "right" ? w / 2 + stairW / 2 + 0.005 : -w / 2 - stairW / 2 - 0.005;

        // Individual treads rising from front to back
        for (let i = 0; i < steps; i++) {
            const tread = new THREE.Mesh(
                new THREE.BoxGeometry(stairW, stepH * 0.6, stepD * 1.05),
                mat
            );
            const sy = baseY + i * stepH + stepH * 0.3;
            const sz = -stairD / 2 + stepD * (i + 0.5);
            tread.position.set(xOff, sy, sz);
            group.add(tread);
        }

        // Side wall (simple thin panel along the stair edge)
        const wallH = totalH * 0.7;
        const wallMat = this._matPBR(comp, color, 0.82);
        const wallX = side === "right" ? xOff + stairW / 2 + 0.008 : xOff - stairW / 2 - 0.008;
        const sideWall = new THREE.Mesh(
            new THREE.BoxGeometry(0.02, wallH, stairD + 0.02),
            wallMat
        );
        sideWall.position.set(wallX, baseY + wallH / 2, 0);
        group.add(sideWall);

        // Top landing platform
        const landing = new THREE.Mesh(
            new THREE.BoxGeometry(stairW + 0.04, 0.025, stepD * 1.5),
            mat
        );
        landing.position.set(xOff, baseY + totalH - 0.012, stairD / 2);
        group.add(landing);

        return baseY; // decorative — does not advance Y
    }

    // ─── Hover / Click ───

    /**
     * Set time of day (0 = midnight, 0.25 = sunrise, 0.5 = noon, 0.75 = sunset, 1 = midnight).
     * Adjusts sun position, color temperature, shadow intensity, and fog.
     */
    setTimeOfDay(t) {
        this._timeOfDay = t;
        const angle = (t - 0.25) * Math.PI; // 0.25=horizon, 0.5=zenith, 0.75=horizon
        const sunY = Math.sin(angle);
        const sunX = Math.cos(angle);
        const sunAlt = Math.max(0, sunY); // 0 at horizon, 1 at noon

        // Sun position orbits east to west
        this._sunLight.position.set(sunX * 500, sunAlt * 600 + 20, 250);

        // Sun intensity: maintain decent minimum for visibility
        this._sunLight.intensity = Math.max(0.3, 0.2 + sunAlt * 0.8);

        // Color temperature: warm at sunrise/sunset, neutral at noon
        const warmth = 1 - sunAlt; // 1 at horizon, 0 at noon
        const r = 1.0;
        const g = 0.88 + sunAlt * 0.12; // 0.88-1.0
        const b = 0.7 + sunAlt * 0.25 - warmth * 0.15; // warmer at edges
        this._sunLight.color.setRGB(r, g, b);

        // Ambient adapts: dimmer at night, bluer at night
        const nightFactor = Math.max(0, -sunY); // >0 when sun below horizon
        this._ambientLight.intensity = Math.max(0.2, 0.2 + sunAlt * 0.2 + nightFactor * 0.05);
        this._ambientLight.color.setRGB(
            0.85 - nightFactor * 0.4,
            0.88 - nightFactor * 0.2,
            1.0
        );

        // Hemisphere light: sky blue shifts to deep blue at night
        this._hemiLight.intensity = 0.2 + sunAlt * 0.3;
        this._hemiLight.color.setRGB(
            0.66 + sunAlt * 0.2 - nightFactor * 0.3,
            0.78 + sunAlt * 0.1 - nightFactor * 0.2,
            0.94 - nightFactor * 0.2
        );
        this._hemiLight.groundColor.setRGB(
            0.48 + warmth * 0.15,
            0.43 + warmth * 0.08,
            0.32 - nightFactor * 0.1
        );

        // Fill light: stronger at golden hour
        this._fillLight.intensity = 0.05 + warmth * 0.15 * sunAlt;
        this._fillLight.color.setRGB(1.0, 0.8 + warmth * 0.1, 0.6 + warmth * 0.1);

        // Fog density increases at dawn/dusk (haze)
        if (this.scene.fog) {
            const baseDensity = this.scene.fog._baseDensity || this.scene.fog.density;
            if (!this.scene.fog._baseDensity) this.scene.fog._baseDensity = baseDensity;
            this.scene.fog.density = baseDensity * (1 + warmth * 0.2 + nightFactor * 0.15);
            // Fog color: warm at golden hour, blue-gray at night
            this.scene.fog.color.setRGB(
                0.77 - nightFactor * 0.3 + warmth * 0.1,
                0.71 - nightFactor * 0.25,
                0.60 - nightFactor * 0.1 + warmth * 0.05
            );
        }

        // Tone mapping exposure: tight range to avoid both washout and too-dark
        this.renderer3d.toneMappingExposure = Math.max(0.6, Math.min(0.95, 0.8 + sunAlt * 0.1 - nightFactor * 0.15));

        // Update dynamic Sky shader if present
        this._updateSkyForTimeOfDay(t);

        // Update dust particle visibility — only visible near golden hour
        if (this._dustParticles) {
            const goldenHourFactor = Math.max(0,
                1 - Math.abs(t - 0.3) * 5, // morning golden hour ~0.25-0.35
                1 - Math.abs(t - 0.7) * 5  // evening golden hour ~0.65-0.75
            );
            this._dustParticles.material.opacity = goldenHourFactor * 0.4;
            this._dustParticles.visible = goldenHourFactor > 0.05;
        }
    }

    _getMeshList() {
        if (this._meshListDirty) {
            this._meshList = [];
            this.buildingGroups.forEach(g => g.traverse(c => { if (c.isMesh) this._meshList.push(c); }));
            this._meshListDirty = false;
        }
        return this._meshList;
    }

    _updateHover(e) {
        // Throttle hover checks to 50ms to avoid excessive raycasting
        const now = Date.now();
        if (now - (this._lastHoverTime || 0) < 50) return;
        this._lastHoverTime = now;

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

    // ─── District Labels ───

    /**
     * Create a floating text sprite label for a district.
     * @param {string} name  — district display name
     * @param {object} region — {x1, y1, x2, y2} in tile coordinates
     */
    addDistrictLabel(name, region) {
        if (!name || !region) return;
        // Avoid duplicate labels for the same district
        for (const child of this._districtLabelsGroup.children) {
            if (child.userData && child.userData.districtName === name) return;
        }

        const S = TILE_SIZE;
        // Center of the region in world coordinates
        const cx = ((region.x1 + region.x2) / 2 + 0.5) * S;
        const cz = ((region.y1 + region.y2) / 2 + 0.5) * S;
        // Elevation at center + offset above buildings
        const groundY = this._surfaceYAtWorldXZ ? this._surfaceYAtWorldXZ(cx, cz) : 0;
        const labelY = groundY + S * 6;

        // Canvas text texture
        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d");
        const fontSize = 48;
        ctx.font = `bold ${fontSize}px sans-serif`;
        const textWidth = ctx.measureText(name).width;
        const padding = 24;
        canvas.width = textWidth + padding * 2;
        canvas.height = fontSize + padding * 2;

        // Semi-transparent dark background
        ctx.fillStyle = "rgba(15, 15, 30, 0.7)";
        const radius = 12;
        this._roundRect(ctx, 0, 0, canvas.width, canvas.height, radius);
        ctx.fill();

        // Border
        ctx.strokeStyle = "rgba(200, 180, 120, 0.5)";
        ctx.lineWidth = 2;
        this._roundRect(ctx, 1, 1, canvas.width - 2, canvas.height - 2, radius);
        ctx.stroke();

        // Text
        ctx.font = `bold ${fontSize}px sans-serif`;
        ctx.fillStyle = "#ffffff";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(name, canvas.width / 2, canvas.height / 2);

        const texture = new THREE.CanvasTexture(canvas);
        texture.minFilter = THREE.LinearFilter;
        texture.magFilter = THREE.LinearFilter;

        const spriteMat = new THREE.SpriteMaterial({
            map: texture,
            transparent: true,
            depthTest: false,
            sizeAttenuation: true,
        });
        const sprite = new THREE.Sprite(spriteMat);
        sprite.position.set(cx, labelY, cz);

        // Scale: proportional to region size so labels are readable at city-wide zoom
        const regionSpan = Math.max(
            (region.x2 - region.x1) * S,
            (region.y2 - region.y1) * S,
            S * 6
        );
        const aspect = canvas.width / canvas.height;
        const baseScale = regionSpan * 0.4;
        sprite.scale.set(baseScale * aspect, baseScale, 1);

        sprite.userData = { districtName: name, region };
        sprite.renderOrder = 999;

        this._districtLabelsGroup.add(sprite);
    }

    /** Helper: draw a rounded rectangle path on a canvas 2D context. */
    _roundRect(ctx, x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + w - r, y);
        ctx.quadraticCurveTo(x + w, y, x + w, y + r);
        ctx.lineTo(x + w, y + h - r);
        ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
        ctx.lineTo(x + r, y + h);
        ctx.quadraticCurveTo(x, y + h, x, y + h - r);
        ctx.lineTo(x, y + r);
        ctx.quadraticCurveTo(x, y, x + r, y);
        ctx.closePath();
    }

    /** Toggle visibility of all district labels (Shift+L). */
    toggleDistrictLabels() {
        this._districtLabelsVisible = !this._districtLabelsVisible;
        this._districtLabelsGroup.visible = this._districtLabelsVisible;
    }

    /** Remove all district labels (called on scene clear / reset). */
    clearDistrictLabels() {
        while (this._districtLabelsGroup.children.length > 0) {
            const child = this._districtLabelsGroup.children[0];
            if (child.material) {
                if (child.material.map) child.material.map.dispose();
                child.material.dispose();
            }
            this._districtLabelsGroup.remove(child);
        }
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
        // Animate only groups that are currently dropping in (terrain tiles)
        for (const group of this._animatingGroups) {
            const t = Math.min(1, (now - group.userData.animStart) / 600);
            const ease = 1 - Math.pow(1 - t, 3);
            group.position.y = group.userData.animStartY + (group.userData.animTargetY - group.userData.animStartY) * ease;
            if (t >= 1) { delete group.userData.animStart; this._animatingGroups.delete(group); }
        }
        // Construction animation: bottom-up reveal for building groups
        if (this._constructingGroups.size > 0) {
            const cDone = [];
            for (const group of this._constructingGroups) {
                let allDone = true;
                group.traverse(c => {
                    if (c.isMesh && c.userData._constructStart != null) {
                        const elapsed = now - c.userData._constructStart;
                        if (elapsed < 0) { allDone = false; return; } // Stagger not started yet
                        const t = Math.min(1, elapsed / c.userData._constructDuration);
                        const ease = 1 - Math.pow(1 - t, 3); // ease-out-cubic
                        c.scale.y = 0.01 + ease * 0.99;
                        c.position.y = c.userData._constructY * ease;
                        if (t < 1) { allDone = false; }
                        else {
                            delete c.userData._constructStart;
                            delete c.userData._constructDuration;
                            delete c.userData._constructY;
                        }
                    }
                });
                if (allDone) cDone.push(group);
            }
            for (let i = 0; i < cDone.length; i++) this._constructingGroups.delete(cDone[i]);
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

        // Animate dust particles — slow brownian drift
        if (this._dustParticles && this._dustParticles.visible && this._dustVelocities) {
            const posAttr = this._dustParticles.geometry.getAttribute("position");
            const pos = posAttr.array;
            const vel = this._dustVelocities;
            const bw = this._dustBoundsW || 500;
            const bh = this._dustBoundsH || 500;
            const my = this._dustMidY || 0;
            for (let i = 0; i < pos.length; i += 3) {
                // Add small random jitter to velocity (brownian motion)
                vel[i] += (Math.random() - 0.5) * 0.02;
                vel[i + 1] += (Math.random() - 0.5) * 0.01;
                vel[i + 2] += (Math.random() - 0.5) * 0.02;
                // Dampen velocity
                vel[i] *= 0.98;
                vel[i + 1] *= 0.98;
                vel[i + 2] *= 0.98;
                // Update position
                pos[i] += vel[i];
                pos[i + 1] += vel[i + 1];
                pos[i + 2] += vel[i + 2];
                // Wrap around bounds
                if (pos[i] < 0) pos[i] = bw;
                if (pos[i] > bw) pos[i] = 0;
                if (pos[i + 2] < 0) pos[i + 2] = bh;
                if (pos[i + 2] > bh) pos[i + 2] = 0;
                if (pos[i + 1] < my + 5) pos[i + 1] = my + 80;
                if (pos[i + 1] > my + 90) pos[i + 1] = my + 10;
            }
            posAttr.needsUpdate = true;
        }

        this._projScreenMatrix.multiplyMatrices(this.camera.projectionMatrix, this.camera.matrixWorldInverse);
        this._frustum.setFromProjectionMatrix(this._projScreenMatrix);
        const camPos = this.camera.position;
        this.buildingGroups.forEach((group) => {
            if (group.userData.cullRadius == null) return;
            this._cullCenter.set(
                group.position.x,
                group.position.y + (group.userData.cullCenterOffsetY || 0),
                group.position.z
            );
            this._cullSphere.set(this._cullCenter, group.userData.cullRadius);
            const inFrustum = this._frustum.intersectsSphere(this._cullSphere);
            group.visible = inFrustum;
            if (!inFrustum) return;

            // Distance-based LOD — reduce quality for distant buildings
            const dx = group.position.x - camPos.x;
            const dz = group.position.z - camPos.z;
            const distSq = dx * dx + dz * dz;
            const LOD_MID = 600 * 600;    // Distance² where shadows turn off
            const LOD_FAR = 1200 * 1200;  // Distance² where small meshes hide

            group.traverse((child) => {
                if (!child.isMesh) return;
                if (distSq > LOD_FAR) {
                    // Far: hide tiny decorative meshes (windows, frames, signs)
                    if (child.geometry && child.geometry.boundingSphere) {
                        child.visible = child.geometry.boundingSphere.radius > 0.06;
                    }
                    child.castShadow = false;
                } else if (distSq > LOD_MID) {
                    // Mid: disable shadow casting to reduce shadow map cost
                    child.visible = true;
                    child.castShadow = false;
                } else {
                    // Near: full detail + shadows
                    child.visible = true;
                }
            });
        });

        // Use post-processing composer if available, otherwise direct render
        if (this.composer) {
            this.composer.render();
        } else {
            this.renderer3d.render(this.scene, this.camera);
        }
    }
}
