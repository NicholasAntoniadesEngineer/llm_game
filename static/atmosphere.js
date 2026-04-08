// Eternal Cities — Unified Atmosphere System
// Procedural sky, clouds, aerial perspective, weather
// Attaches to window.EternalCities.Atmosphere

(function () {
    "use strict";

    window.EternalCities = window.EternalCities || {};

    // ═══════════════════════════════════════════════════════
    // AtmosphereState — flat data bag for all atmospheric params
    // ═══════════════════════════════════════════════════════

    function AtmosphereState() {
        this.timeOfDay = 0.35;                          // 0 = midnight, 0.25 = sunrise, 0.5 = noon, 0.75 = sunset
        this.sunDirection = new THREE.Vector3(0.5, 0.7, 0.3).normalize();
        this.sunAltitude = 0.6;                         // -1 (below) to 1 (zenith)
        this.fogColor = new THREE.Color(0xc4b8a0);
        this.fogDensity = 0.00012;
        this.fogHeightFalloff = 0.02;
        this.hazeDensity = 0.3;
        this.cloudCoverage = 0.2;
        this.windDirection = new THREE.Vector2(1, 0.3).normalize();
        this.windSpeed = 0.2;
        this.humidity = 0.5;
        this.aridity = 0.3;
    }

    // ═══════════════════════════════════════════════════════
    // Weather Presets — target states for transitions
    // ═══════════════════════════════════════════════════════

    var WeatherPresets = {
        clear: {
            fogDensity: 0.00008,
            fogHeightFalloff: 0.025,
            hazeDensity: 0.15,
            cloudCoverage: 0.1
        },
        hazy: {
            fogDensity: 0.00015,
            fogHeightFalloff: 0.018,
            hazeDensity: 0.4,
            cloudCoverage: 0.2
        },
        overcast: {
            fogDensity: 0.0002,
            fogHeightFalloff: 0.015,
            hazeDensity: 0.5,
            cloudCoverage: 0.7
        },
        fog: {
            fogDensity: 0.0006,
            fogHeightFalloff: 0.008,
            hazeDensity: 0.8,
            cloudCoverage: 0.4
        },
        dustStorm: {
            fogDensity: 0.0005,
            fogHeightFalloff: 0.005,
            hazeDensity: 0.9,
            cloudCoverage: 0.15,
            fogColor: new THREE.Color(0xc4a060)
        },
        heatHaze: {
            fogDensity: 0.0001,
            fogHeightFalloff: 0.03,
            hazeDensity: 0.6,
            cloudCoverage: 0.05,
            fogColor: new THREE.Color(0xd4c890)
        }
    };

    // ═══════════════════════════════════════════════════════
    // CloudPlane — procedural 2D noise cloud layer
    // ═══════════════════════════════════════════════════════

    var cloudVertexShader = [
        "varying vec2 vWorldUV;",
        "varying vec2 vLocalUV;",
        "uniform vec2 uOffset;",
        "",
        "void main() {",
        "    vLocalUV = uv;",
        "    vWorldUV = uv * 6.0 + uOffset;",
        "    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);",
        "}"
    ].join("\n");

    var cloudFragmentShader = [
        "precision highp float;",
        "",
        "varying vec2 vWorldUV;",
        "varying vec2 vLocalUV;",
        "uniform float uCoverage;",
        "uniform float uTime;",
        "uniform vec3 uSunDir;",
        "uniform float uSunAltitude;",
        "",
        // 2D simplex noise (self-contained)
        "vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }",
        "vec2 mod289(vec2 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }",
        "vec3 permute(vec3 x) { return mod289(((x * 34.0) + 1.0) * x); }",
        "",
        "float snoise(vec2 v) {",
        "    const vec4 C = vec4(0.211324865405187, 0.366025403784439,",
        "                        -0.577350269189626, 0.024390243902439);",
        "    vec2 i = floor(v + dot(v, C.yy));",
        "    vec2 x0 = v - i + dot(i, C.xx);",
        "    vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);",
        "    vec4 x12 = x0.xyxy + C.xxzz;",
        "    x12.xy -= i1;",
        "    i = mod289(i);",
        "    vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0)) + i.x + vec3(0.0, i1.x, 1.0));",
        "    vec3 m = max(0.5 - vec3(dot(x0, x0), dot(x12.xy, x12.xy), dot(x12.zw, x12.zw)), 0.0);",
        "    m = m * m;",
        "    m = m * m;",
        "    vec3 x = 2.0 * fract(p * C.www) - 1.0;",
        "    vec3 h = abs(x) - 0.5;",
        "    vec3 ox = floor(x + 0.5);",
        "    vec3 a0 = x - ox;",
        "    m *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);",
        "    vec3 g;",
        "    g.x = a0.x * x0.x + h.x * x0.y;",
        "    g.yz = a0.yz * x12.xz + h.yz * x12.yw;",
        "    return 130.0 * dot(m, g);",
        "}",
        "",
        "float fbm(vec2 p) {",
        "    float f = 0.0;",
        "    f += 0.5000 * snoise(p); p *= 2.02;",
        "    f += 0.2500 * snoise(p); p *= 2.03;",
        "    f += 0.1250 * snoise(p); p *= 2.01;",
        "    f += 0.0625 * snoise(p);",
        "    return f;",
        "}",
        "",
        "void main() {",
        "    vec2 uv = vWorldUV;",
        "    float t = uTime * 0.02;",
        "",
        "    // Layered noise for cloud shapes",
        "    float n = fbm(uv + t * 0.1);",
        "    n += 0.5 * fbm(uv * 2.0 - t * 0.05);",
        "    n = n * 0.5 + 0.5;",
        "",
        "    // Coverage threshold — smoothstep controls cloud density",
        "    float threshold = 1.0 - uCoverage;",
        "    float cloud = smoothstep(threshold - 0.1, threshold + 0.15, n);",
        "",
        "    // Lighting: white in sun, gray underneath",
        "    float sunFactor = max(0.0, uSunAltitude);",
        "    vec3 litColor = mix(vec3(0.85, 0.85, 0.88), vec3(1.0, 0.98, 0.95), sunFactor);",
        "    vec3 shadowColor = mix(vec3(0.25, 0.25, 0.3), vec3(0.55, 0.55, 0.6), sunFactor);",
        "",
        "    // Simple self-shadowing: darker where coverage is high",
        "    float shadow = smoothstep(threshold, threshold + 0.3, n);",
        "    vec3 col = mix(shadowColor, litColor, shadow);",
        "",
        "    // Edge softness",
        "    float alpha = cloud * 0.6;",
        "",
        "    // Fade at edges of plane to avoid hard cutoff",
        "    vec2 edgeUV = abs(vLocalUV - 0.5) * 2.0;",
        "    float edgeFade = 1.0 - smoothstep(0.7, 1.0, max(edgeUV.x, edgeUV.y));",
        "    alpha *= edgeFade;",
        "",
        "    gl_FragColor = vec4(col, alpha);",
        "}"
    ].join("\n");

    function CloudPlane(cityWidth, cityHeight) {
        var size = Math.max(cityWidth, cityHeight, 40) * 3;
        var geo = new THREE.PlaneGeometry(size, size, 1, 1);
        this.uniforms = {
            uOffset: { value: new THREE.Vector2(0, 0) },
            uCoverage: { value: 0.2 },
            uTime: { value: 0 },
            uSunDir: { value: new THREE.Vector3(0.5, 0.7, 0.3) },
            uSunAltitude: { value: 0.6 }
        };
        var mat = new THREE.ShaderMaterial({
            vertexShader: cloudVertexShader,
            fragmentShader: cloudFragmentShader,
            uniforms: this.uniforms,
            transparent: true,
            depthWrite: false,
            side: THREE.DoubleSide
        });
        this.mesh = new THREE.Mesh(geo, mat);
        this.mesh.rotation.x = -Math.PI / 2;
        this.mesh.position.set(cityWidth / 2, 300, cityHeight / 2);
        this.mesh.frustumCulled = false;
    }

    CloudPlane.prototype.update = function (dt, state) {
        this.uniforms.uTime.value += dt;
        this.uniforms.uOffset.value.x += state.windDirection.x * state.windSpeed * dt * 0.01;
        this.uniforms.uOffset.value.y += state.windDirection.y * state.windSpeed * dt * 0.01;
        this.uniforms.uCoverage.value = state.cloudCoverage;
        this.uniforms.uSunDir.value.copy(state.sunDirection);
        this.uniforms.uSunAltitude.value = state.sunAltitude;
    };

    CloudPlane.prototype.dispose = function () {
        if (this.mesh) {
            this.mesh.geometry.dispose();
            this.mesh.material.dispose();
        }
    };

    // ═══════════════════════════════════════════════════════
    // Aerial Perspective — post-processing fog pass
    // ═══════════════════════════════════════════════════════

    var AerialPerspectiveShader = {
        uniforms: {
            tDiffuse: { value: null },
            tDepth: { value: null },
            uFogColor: { value: new THREE.Color(0xc4b8a0) },
            uFogDensity: { value: 0.00012 },
            uFogHeightFalloff: { value: 0.02 },
            uHazeDensity: { value: 0.3 },
            uCameraPos: { value: new THREE.Vector3() },
            uCameraFar: { value: 200.0 },
            uInvProjection: { value: new THREE.Matrix4() },
            uInvView: { value: new THREE.Matrix4() },
            uSunAltitude: { value: 0.6 }
        },
        vertexShader: [
            "varying vec2 vUv;",
            "void main() {",
            "    vUv = uv;",
            "    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);",
            "}"
        ].join("\n"),
        fragmentShader: [
            "precision highp float;",
            "",
            "uniform sampler2D tDiffuse;",
            "uniform sampler2D tDepth;",
            "uniform vec3 uFogColor;",
            "uniform float uFogDensity;",
            "uniform float uFogHeightFalloff;",
            "uniform float uHazeDensity;",
            "uniform vec3 uCameraPos;",
            "uniform float uCameraFar;",
            "uniform mat4 uInvProjection;",
            "uniform mat4 uInvView;",
            "uniform float uSunAltitude;",
            "",
            "varying vec2 vUv;",
            "",
            "void main() {",
            "    vec4 color = texture2D(tDiffuse, vUv);",
            "",
            "    // Reconstruct linear depth from logarithmic depth buffer",
            "    float logDepth = texture2D(tDepth, vUv).r;",
            "    float linearDepth = pow(2.0, logDepth * log2(uCameraFar + 1.0)) - 1.0;",
            "",
            "    // Reconstruct world position from depth",
            "    vec4 clipPos = vec4(vUv * 2.0 - 1.0, logDepth * 2.0 - 1.0, 1.0);",
            "    vec4 viewPos = uInvProjection * clipPos;",
            "    viewPos /= viewPos.w;",
            "",
            "    // Use linear depth for distance",
            "    float dist = linearDepth;",
            "",
            "    // Estimate world height from view-space position",
            "    vec4 worldPos = uInvView * viewPos;",
            "    float worldHeight = uCameraPos.y + viewPos.y * (linearDepth / max(abs(viewPos.z), 0.001));",
            "",
            "    // Height-based fog attenuation: denser at low altitude",
            "    float heightFactor = exp(-max(worldHeight, 0.0) * uFogHeightFalloff);",
            "",
            "    // Exponential distance fog",
            "    float fogAmount = 1.0 - exp(-dist * uFogDensity * (1.0 + heightFactor));",
            "",
            "    // Haze: additional constant-density layer",
            "    float hazeAmount = 1.0 - exp(-dist * uFogDensity * uHazeDensity * 3.0);",
            "",
            "    // Combine fog and haze",
            "    float totalFog = clamp(fogAmount + hazeAmount * 0.3, 0.0, 1.0);",
            "",
            "    // Tint fog color warmer at sunset/sunrise",
            "    float sunWarmth = max(0.0, 1.0 - abs(uSunAltitude - 0.15) * 3.0);",
            "    vec3 warmFog = mix(uFogColor, vec3(1.0, 0.75, 0.5), sunWarmth * 0.3);",
            "",
            "    // Skip sky pixels (very far depth)",
            "    float skyMask = step(linearDepth, uCameraFar * 0.95);",
            "    totalFog *= skyMask;",
            "",
            "    gl_FragColor = vec4(mix(color.rgb, warmFog, totalFog), color.a);",
            "}"
        ].join("\n")
    };

    // ═══════════════════════════════════════════════════════
    // NightSky — star field visible at night
    // ═══════════════════════════════════════════════════════

    function NightSky() {
        var count = 1500;
        var positions = new Float32Array(count * 3);
        var radius = 8000;

        for (var i = 0; i < count; i++) {
            // Random point on sphere
            var theta = Math.random() * Math.PI * 2;
            var phi = Math.acos(2 * Math.random() - 1);
            // Only upper hemisphere for stars
            phi = phi * 0.5;
            positions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
            positions[i * 3 + 1] = radius * Math.cos(phi);
            positions[i * 3 + 2] = radius * Math.sin(phi) * Math.sin(theta);
        }

        var geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));

        this.material = new THREE.PointsMaterial({
            color: 0xffffff,
            size: 1.2,
            sizeAttenuation: false,
            transparent: true,
            opacity: 0,
            depthWrite: false
        });

        this.points = new THREE.Points(geo, this.material);
        this.points.frustumCulled = false;
    }

    NightSky.prototype.update = function (state) {
        // Stars visible only at night (sunAltitude < 0)
        this.material.opacity = Math.max(0, -state.sunAltitude * 3);
        this.material.opacity = Math.min(this.material.opacity, 0.8);
        // Slow rotation
        this.points.rotation.y += 0.0001;
    };

    NightSky.prototype.dispose = function () {
        if (this.points) {
            this.points.geometry.dispose();
            this.material.dispose();
        }
    };

    // ═══════════════════════════════════════════════════════
    // AtmosphereController — the main system
    // ═══════════════════════════════════════════════════════

    function AtmosphereController() {
        this.state = new AtmosphereState();
        this.cloudPlane = null;
        this.nightSky = null;
        this.aerialPass = null;
        this._weatherTarget = null;
        this._weatherTransition = 0;
        this._weatherDuration = 0;
        this._weatherFrom = null;
        this._baseFogColor = new THREE.Color(0xc4b8a0);
    }

    /**
     * Initialize scene objects (cloud plane, night sky).
     * Call after scene and world dimensions are known.
     */
    AtmosphereController.prototype.initScene = function (scene, cityWidth, cityHeight) {
        this.scene = scene;

        // Cloud plane
        this.cloudPlane = new CloudPlane(cityWidth, cityHeight);
        scene.add(this.cloudPlane.mesh);

        // Night sky
        this.nightSky = new NightSky();
        scene.add(this.nightSky.points);
    };

    /**
     * Create and return the aerial perspective ShaderPass.
     * Returns null if THREE.ShaderPass is not available.
     */
    AtmosphereController.prototype.createAerialPass = function (depthTexture, cameraFar) {
        if (typeof THREE.ShaderPass === "undefined") {
            console.warn("Atmosphere: THREE.ShaderPass not available, skipping aerial perspective");
            return null;
        }

        var shader = {
            uniforms: THREE.UniformsUtils.clone(AerialPerspectiveShader.uniforms),
            vertexShader: AerialPerspectiveShader.vertexShader,
            fragmentShader: AerialPerspectiveShader.fragmentShader
        };

        if (depthTexture) {
            shader.uniforms.tDepth.value = depthTexture;
        }
        shader.uniforms.uCameraFar.value = cameraFar || 200.0;

        this.aerialPass = new THREE.ShaderPass(shader);
        this.aerialPass.renderToScreen = false;
        return this.aerialPass;
    };

    /**
     * Update sun position and related atmosphere state from time-of-day.
     * timeOfDay: 0 = midnight, 0.25 = sunrise, 0.5 = noon, 0.75 = sunset, 1 = midnight
     */
    AtmosphereController.prototype.setTimeOfDay = function (t) {
        this.state.timeOfDay = t;

        // Sun altitude: peaks at noon (t=0.5), below horizon at night
        var sunAngle = (t - 0.25) * Math.PI * 2;
        this.state.sunAltitude = Math.sin(sunAngle);

        // Sun direction (azimuth rotates through day)
        var azimuth = t * Math.PI * 2;
        var alt = Math.max(this.state.sunAltitude, -0.2);
        this.state.sunDirection.set(
            Math.cos(azimuth) * Math.cos(alt),
            Math.sin(alt),
            Math.sin(azimuth) * Math.cos(alt)
        ).normalize();

        // Fog color shifts: warm at sunrise/sunset, blue at night, sandy during day
        var dayColor = this._baseFogColor.clone();
        var sunsetColor = new THREE.Color(0xff9966);
        var nightColor = new THREE.Color(0x1a1a2e);

        var dayFactor = Math.max(0, this.state.sunAltitude);
        var sunsetFactor = Math.max(0, 1 - Math.abs(this.state.sunAltitude - 0.1) * 4);
        var nightFactor = Math.max(0, -this.state.sunAltitude);

        this.state.fogColor.copy(dayColor).lerp(sunsetColor, sunsetFactor * 0.4);
        this.state.fogColor.lerp(nightColor, nightFactor * 0.6);
    };

    /**
     * Set climate from city data.
     */
    AtmosphereController.prototype.setClimate = function (climate) {
        if (!climate) return;

        this.state.humidity = climate.humidity !== undefined ? climate.humidity : 0.5;
        this.state.aridity = climate.aridity !== undefined ? climate.aridity : 0.3;

        // Adjust base fog color by aridity (more yellow/sandy for arid climates)
        var baseColor = new THREE.Color(0xc4b8a0);
        var aridColor = new THREE.Color(0xd4c080);
        var humidColor = new THREE.Color(0xa0b8c4);
        this._baseFogColor.copy(baseColor);
        if (this.state.aridity > 0.5) {
            this._baseFogColor.lerp(aridColor, (this.state.aridity - 0.5) * 2);
        } else if (this.state.humidity > 0.5) {
            this._baseFogColor.lerp(humidColor, (this.state.humidity - 0.5) * 0.5);
        }

        // Humidity affects base fog density and cloud coverage
        this.state.fogDensity *= (1 + this.state.humidity * 0.5);
        this.state.cloudCoverage += this.state.humidity * 0.1;

        // Apply default weather if specified
        if (climate.defaultWeather && WeatherPresets[climate.defaultWeather]) {
            this.setWeather(climate.defaultWeather, 0);
        }
    };

    /**
     * Transition to a weather preset over time (seconds).
     * If transitionTime is 0, apply immediately.
     */
    AtmosphereController.prototype.setWeather = function (name, transitionTime) {
        var preset = WeatherPresets[name];
        if (!preset) {
            console.warn("Atmosphere: unknown weather preset '" + name + "'");
            return;
        }

        if (!transitionTime || transitionTime <= 0) {
            // Apply immediately
            for (var key in preset) {
                if (key === "fogColor") {
                    this.state.fogColor.copy(preset.fogColor);
                    this._baseFogColor.copy(preset.fogColor);
                } else if (this.state[key] !== undefined) {
                    this.state[key] = preset[key];
                }
            }
            this._weatherTarget = null;
            return;
        }

        // Start transition
        this._weatherFrom = {};
        for (var k in preset) {
            if (k === "fogColor") {
                this._weatherFrom.fogColor = this._baseFogColor.clone();
            } else if (this.state[k] !== undefined) {
                this._weatherFrom[k] = this.state[k];
            }
        }
        this._weatherTarget = preset;
        this._weatherTransition = 0;
        this._weatherDuration = transitionTime;
    };

    /**
     * Per-frame update. dt in seconds.
     */
    AtmosphereController.prototype.update = function (dt, camera) {
        // Weather transition
        if (this._weatherTarget && this._weatherDuration > 0) {
            this._weatherTransition += dt / this._weatherDuration;
            var t = Math.min(this._weatherTransition, 1);
            // Smooth ease
            t = t * t * (3 - 2 * t);

            for (var key in this._weatherTarget) {
                if (key === "fogColor" && this._weatherFrom.fogColor) {
                    this._baseFogColor.copy(this._weatherFrom.fogColor).lerp(this._weatherTarget.fogColor, t);
                } else if (this._weatherFrom[key] !== undefined) {
                    this.state[key] = this._weatherFrom[key] + (this._weatherTarget[key] - this._weatherFrom[key]) * t;
                }
            }

            if (this._weatherTransition >= 1) {
                this._weatherTarget = null;
            }
        }

        // Update sub-systems
        if (this.cloudPlane) {
            this.cloudPlane.update(dt, this.state);
        }

        if (this.nightSky) {
            this.nightSky.update(this.state);
        }

        // Update aerial perspective uniforms
        if (this.aerialPass && camera) {
            var u = this.aerialPass.uniforms;
            u.uFogColor.value.copy(this.state.fogColor);
            u.uFogDensity.value = this.state.fogDensity;
            u.uFogHeightFalloff.value = this.state.fogHeightFalloff;
            u.uHazeDensity.value = this.state.hazeDensity;
            u.uSunAltitude.value = this.state.sunAltitude;
            u.uCameraPos.value.copy(camera.position);
            u.uInvProjection.value.copy(camera.projectionMatrixInverse);
            u.uInvView.value.copy(camera.matrixWorld);
        }
    };

    /**
     * Clean up all scene objects.
     */
    AtmosphereController.prototype.dispose = function () {
        if (this.cloudPlane) {
            if (this.scene) this.scene.remove(this.cloudPlane.mesh);
            this.cloudPlane.dispose();
            this.cloudPlane = null;
        }
        if (this.nightSky) {
            if (this.scene) this.scene.remove(this.nightSky.points);
            this.nightSky.dispose();
            this.nightSky = null;
        }
        this.aerialPass = null;
    };

    // ═══════════════════════════════════════════════════════
    // Export
    // ═══════════════════════════════════════════════════════

    window.EternalCities.Atmosphere = {
        AtmosphereState: AtmosphereState,
        AtmosphereController: AtmosphereController,
        CloudPlane: CloudPlane,
        NightSky: NightSky,
        AerialPerspectiveShader: AerialPerspectiveShader,
        WeatherPresets: WeatherPresets
    };

})();
