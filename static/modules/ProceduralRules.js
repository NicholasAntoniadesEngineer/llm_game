// ProceduralRules — Shape-list renderer: box, cylinder, cone, sphere, torus
// AI sculpts each building from 3D primitives — no templates.
// Part of the EternalCities module system

window.EternalCities = window.EternalCities || {};

window.EternalCities.ProceduralRules = {
    /**
     * Render a list of AI-described 3D shape primitives into a group.
     * Supported types: box, cylinder, cone, sphere, torus.
     *
     * @param {THREE.Group} group - parent group
     * @param {Array<object>} shapes - shape descriptors from AI spec
     * @param {function} mat - material getter (color, roughness) => material
     */
    renderShapes(group, shapes, mat) {
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
            const material = mat(color, shape.roughness || 0.75);
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

            mesh = new THREE.Mesh(geo, material);
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
};
