// SceneManager — Scene setup, lighting, fog, sky, shadows, render loop
// Part of the EternalCities module system

window.EternalCities = window.EternalCities || {};

window.EternalCities.SceneManager = {
    /**
     * Create and configure the Three.js scene, renderer, and lighting.
     * @param {HTMLElement} container - DOM element to attach to
     * @returns {{ scene: THREE.Scene, renderer3d: THREE.WebGLRenderer }}
     */
    createScene(container) {
        // Scene — Mediterranean sky
        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0x7EC8E3);
        scene.fog = new THREE.Fog(0x7EC8E3, 70, 140);

        // WebGL renderer
        const renderer3d = new THREE.WebGLRenderer({ antialias: true });
        renderer3d.setSize(container.clientWidth, container.clientHeight);
        renderer3d.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer3d.shadowMap.enabled = true;
        renderer3d.shadowMap.type = THREE.PCFSoftShadowMap;
        renderer3d.toneMapping = THREE.ACESFilmicToneMapping;
        renderer3d.toneMappingExposure = 1.2;
        container.appendChild(renderer3d.domElement);

        return { scene: scene, renderer3d: renderer3d };
    },

    /**
     * Add Mediterranean lighting to the scene.
     * @param {THREE.Scene} scene
     */
    setupLighting(scene) {
        // Ambient
        scene.add(new THREE.AmbientLight(0xffeedd, 0.45));

        // Directional sun with shadows
        const sun = new THREE.DirectionalLight(0xfff8e8, 1.0);
        sun.position.set(30, 40, 20);
        sun.castShadow = true;
        sun.shadow.mapSize.set(2048, 2048);
        const sc = sun.shadow.camera;
        sc.near = 1; sc.far = 120;
        sc.left = -50; sc.right = 50;
        sc.top = 50; sc.bottom = -50;
        scene.add(sun);

        // Hemisphere (sky/ground bounce)
        scene.add(new THREE.HemisphereLight(0x87ceeb, 0x556b2f, 0.25));
    },

    /**
     * Run the animation loop: handles drop-in animations, water animation, and rendering.
     * @param {WorldRenderer} ctx - renderer instance with scene, camera, renderer3d, buildingGroups
     */
    animate(ctx) {
        requestAnimationFrame(() => this.animate(ctx));

        const now = Date.now();

        // Building drop-in animation
        ctx.buildingGroups.forEach(group => {
            if (group.userData.animStart) {
                const t = Math.min(1, (now - group.userData.animStart) / 600);
                const ease = 1 - Math.pow(1 - t, 3);
                group.position.y = group.userData.animStartY +
                    (group.userData.animTargetY - group.userData.animStartY) * ease;
                if (t >= 1) delete group.userData.animStart;
            }
            // Water ripple
            group.traverse(c => {
                if (c.userData && c.userData.isWater) {
                    c.position.y = -0.03 + Math.sin(
                        now * 0.002 + group.position.x * 2 + group.position.z * 3
                    ) * 0.012;
                }
            });
        });

        ctx.renderer3d.render(ctx.scene, ctx.camera);
    }
};
