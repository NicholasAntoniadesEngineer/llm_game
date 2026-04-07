// ComponentBuilder — Legacy feature-based building assembly
// Contains the _assembleBuilding_DEAD method with 22 named component builders
// (stepped_base, columns, pediment, dome, flat_roof, tiled_roof, arches, door,
//  battlements, awning, atrium, pilasters, tiers, windows, balconies, stories, etc.)
// Part of the EternalCities module system

window.EternalCities = window.EternalCities || {};

window.EternalCities.ComponentBuilder = {
    /**
     * Legacy feature-based building assembly.
     * Marked _DEAD in original code — kept for reference / potential reactivation.
     * @param {THREE.Group} g - parent group
     * @param {number} w - width
     * @param {number} d - depth
     * @param {number} h - height
     * @param {string} wallColor
     * @param {string} roofColor
     * @param {Array<string>} features - e.g. ["columns:6","dome","stepped_base:3"]
     * @param {object} spec - tile spec
     * @param {function} mat - material getter (color, roughness) => material
     */
    assembleBuilding(g, w, d, h, wallColor, roofColor, features, spec, mat) {
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
                const step = new THREE.Mesh(new THREE.BoxGeometry(sw, 0.08, sd), mat("#c8b88a"));
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
            const storyW = w - s * 0.03;
            const storyD = d - s * 0.03;
            const wall = new THREE.Mesh(
                new THREE.BoxGeometry(storyW, storyH - 0.02, storyD),
                mat(wallColor)
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
                            mat("#1a1008")
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
                    mat("#a08060")
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
                        mat("#f0ead8", 0.3)
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
                        mat("#f0ead8", 0.3)
                    );
                    cap.position.set(col.position.x, currentY + colH + capSize * 0.2, col.position.z);
                    g.add(cap);

                    // Base
                    const base = new THREE.Mesh(
                        new THREE.CylinderGeometry(radius + 0.01, radius + 0.015, 0.04, 8),
                        mat("#e0d8c8")
                    );
                    base.position.set(col.position.x, currentY + 0.02, col.position.z);
                    g.add(base);
                    colIdx++;
                }
            }

            // Entablature if columns present
            const beam = new THREE.Mesh(
                new THREE.BoxGeometry(w + 0.04, 0.06, d + 0.04),
                mat("#e8e0d0", 0.35)
            );
            beam.position.y = topY - 0.03;
            g.add(beam);
        }

        // 4. ROOF
        if (featureSet.has("pediment")) {
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
            g.add(new THREE.Mesh(geo, mat(roofColor)));
        } else if (featureSet.has("dome")) {
            const domeR = Math.min(w, d) * 0.45;
            const dome = new THREE.Mesh(
                new THREE.SphereGeometry(domeR, 16, 10, 0, Math.PI * 2, 0, Math.PI / 2),
                mat(roofColor, 0.4)
            );
            dome.position.y = topY;
            g.add(dome);
        } else if (featureSet.has("flat_roof")) {
            const roof = new THREE.Mesh(
                new THREE.BoxGeometry(w + 0.04, 0.04, d + 0.04),
                mat(roofColor)
            );
            roof.position.y = topY + 0.02;
            g.add(roof);
        } else if (featureSet.has("tiled_roof")) {
            for (const side of [-1, 1]) {
                const slope = new THREE.Mesh(
                    new THREE.BoxGeometry(w + 0.02, 0.04, d * 0.55),
                    mat(roofColor)
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
                    mat(wallColor)
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
                mat("#3a2510")
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
                        mat(wallColor)
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
                mat(spec.awning_color || "#cc3333")
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
                        mat("#e0d8c8", 0.4)
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
                        mat("#c4a860")
                    );
                    tier.rotation.x = Math.PI / 2;
                    tier.position.y = currentY + 0.1 + ti * storyH * 0.3;
                    g.add(tier);
                }
            }
        }
    }
};
