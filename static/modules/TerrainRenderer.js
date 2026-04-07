// TerrainRenderer — Ground plane, terrain tiles, water, roads, gardens
// Part of the EternalCities module system

window.EternalCities = window.EternalCities || {};

window.EternalCities.TerrainRenderer = {
    /**
     * Create the ground plane and grid overlay.
     * @param {THREE.Scene} scene
     * @param {number} worldWidth
     * @param {number} worldHeight
     */
    createGround(scene, worldWidth, worldHeight) {
        // Ground plane
        const ground = new THREE.Mesh(
            new THREE.PlaneGeometry(worldWidth + 10, worldHeight + 10),
            new THREE.MeshStandardMaterial({ color: 0xC4B17C, roughness: 1.0 })
        );
        ground.rotation.x = -Math.PI / 2;
        ground.position.set(worldWidth / 2, -0.02, worldHeight / 2);
        ground.receiveShadow = true;
        scene.add(ground);

        // Grid overlay
        const gridSize = Math.max(worldWidth, worldHeight);
        const grid = new THREE.GridHelper(gridSize, gridSize, 0x9a8e6b, 0x9a8e6b);
        grid.position.set(worldWidth / 2, 0.005, worldHeight / 2);
        grid.material.opacity = 0.06;
        grid.material.transparent = true;
        scene.add(grid);
    },

    /**
     * Build terrain tile geometry (road, forum, water, garden, grass).
     * @param {THREE.Group} g - parent group for this tile
     * @param {object} tile - tile data
     * @param {object} spec - tile spec
     * @param {function} mat - material getter (color, roughness) => material
     */
    buildTerrain(g, tile, spec, mat) {
        const terrain = tile.terrain || tile.building_type;

        if (terrain === "road") {
            const road = new THREE.Mesh(
                new THREE.BoxGeometry(0.98, 0.05, 0.98),
                mat(spec.color || "#606060", 0.9)
            );
            road.position.y = 0.025;
            g.add(road);
            // Varied cobblestone marks
            const seed = (tile.x * 7 + tile.y * 13) % 5;
            for (let i = 0; i < 2 + seed % 3; i++) {
                const stone = new THREE.Mesh(
                    new THREE.BoxGeometry(0.06 + Math.random() * 0.08, 0.01, 0.06 + Math.random() * 0.08),
                    mat("#7a7a7a")
                );
                stone.position.set(-0.3 + Math.random() * 0.6, 0.055, -0.3 + Math.random() * 0.6);
                g.add(stone);
            }
        } else if (terrain === "forum") {
            g.add(new THREE.Mesh(
                new THREE.BoxGeometry(0.96, 0.03, 0.96),
                mat(spec.color || "#d4c67a")
            ));
        } else if (terrain === "water") {
            const water = new THREE.Mesh(
                new THREE.BoxGeometry(0.98, 0.06, 0.98),
                new THREE.MeshStandardMaterial({
                    color: 0x2980b9,
                    transparent: true,
                    opacity: 0.8,
                    roughness: 0.05
                })
            );
            water.position.y = -0.03;
            water.userData.isWater = true;
            g.add(water);
        } else if (terrain === "garden" || terrain === "grass") {
            g.add(new THREE.Mesh(
                new THREE.BoxGeometry(0.96, 0.04, 0.96),
                mat(spec.color || "#4a8c3f")
            ));
            // Unique vegetation based on position
            const seed = tile.x * 31 + tile.y * 17;
            const numPlants = 1 + seed % 3;
            for (let i = 0; i < numPlants; i++) {
                const px = -0.25 + ((seed * (i + 1) * 7) % 50) / 100;
                const pz = -0.25 + ((seed * (i + 1) * 13) % 50) / 100;
                const treeH = 0.2 + ((seed * (i + 3)) % 30) / 100;
                const trunk = new THREE.Mesh(
                    new THREE.CylinderGeometry(0.015, 0.025, treeH, 5),
                    mat("#6b4226")
                );
                trunk.position.set(px, 0.04 + treeH / 2, pz);
                g.add(trunk);
                const canopySize = 0.08 + ((seed * (i + 7)) % 20) / 200;
                const canopy = new THREE.Mesh(
                    new THREE.SphereGeometry(canopySize, 6, 5),
                    mat("#2d6b1e")
                );
                canopy.position.set(px, 0.04 + treeH + canopySize * 0.5, pz);
                g.add(canopy);
            }
        }
    }
};
