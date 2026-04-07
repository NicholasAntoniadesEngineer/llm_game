// InteractionManager — Mouse/touch interaction, raycasting, selection, tooltips
// Part of the EternalCities module system

window.EternalCities = window.EternalCities || {};

window.EternalCities.InteractionManager = {
    /**
     * Initialize interaction state on the renderer instance.
     * @param {WorldRenderer} ctx - the renderer instance
     */
    init(ctx) {
        ctx.raycaster = new THREE.Raycaster();
        ctx.mouse = new THREE.Vector2();
        ctx.hoveredGroup = null;
    },

    /**
     * Bind mouse/touch controls to the canvas element.
     * Handles orbit dragging, zoom, hover, click, and context menu.
     * @param {WorldRenderer} ctx - the renderer instance
     */
    setupControls(ctx) {
        const el = ctx.renderer3d.domElement;
        const CameraCtrl = window.EternalCities.CameraController;

        el.addEventListener("mousedown", e => {
            ctx.isDragging = true;
            ctx.prevMouse = { x: e.clientX, y: e.clientY };
        });

        el.addEventListener("mousemove", e => {
            if (ctx.isDragging) {
                ctx.cameraAngle -= (e.clientX - ctx.prevMouse.x) * 0.005;
                ctx.cameraPitch = Math.max(0.1, Math.min(1.3,
                    ctx.cameraPitch + (e.clientY - ctx.prevMouse.y) * 0.005));
                ctx.prevMouse = { x: e.clientX, y: e.clientY };
                CameraCtrl.updateCamera(ctx);
            }
            this.updateHover(ctx, e);
        });

        el.addEventListener("mouseup", () => {
            ctx.isDragging = false;
        });

        el.addEventListener("wheel", e => {
            ctx.cameraDistance = Math.max(8, Math.min(100,
                ctx.cameraDistance + e.deltaY * 0.05));
            CameraCtrl.updateCamera(ctx);
            e.preventDefault();
        }, { passive: false });

        el.addEventListener("click", e => this.onClick(ctx, e));
        el.addEventListener("contextmenu", e => e.preventDefault());
    },

    /**
     * Update hover highlight and tooltip.
     * @param {WorldRenderer} ctx
     * @param {MouseEvent} e
     */
    updateHover(ctx, e) {
        const rect = ctx.renderer3d.domElement.getBoundingClientRect();
        ctx.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        ctx.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        ctx.raycaster.setFromCamera(ctx.mouse, ctx.camera);

        const meshes = [];
        ctx.buildingGroups.forEach(g => g.traverse(c => {
            if (c.isMesh) meshes.push(c);
        }));
        const hits = ctx.raycaster.intersectObjects(meshes);

        // Clear previous hover
        if (ctx.hoveredGroup) {
            ctx.hoveredGroup.traverse(c => {
                if (c.isMesh && c.userData._origE !== undefined) {
                    c.material.emissive.setHex(c.userData._origE);
                }
            });
            ctx.hoveredGroup = null;
        }

        const tooltip = document.getElementById("tooltip");
        if (hits.length > 0) {
            const tile = hits[0].object.userData.tile;
            if (tile && tile.terrain !== "empty") {
                const key = tile.x + "," + tile.y;
                const group = ctx.buildingGroups.get(key);
                if (group) {
                    ctx.hoveredGroup = group;
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
    },

    /**
     * Handle click — dispatch tileclick custom event.
     * @param {WorldRenderer} ctx
     * @param {MouseEvent} e
     */
    onClick(ctx, e) {
        const rect = ctx.renderer3d.domElement.getBoundingClientRect();
        ctx.mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        ctx.mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        ctx.raycaster.setFromCamera(ctx.mouse, ctx.camera);

        const meshes = [];
        ctx.buildingGroups.forEach(g => g.traverse(c => {
            if (c.isMesh) meshes.push(c);
        }));
        const hits = ctx.raycaster.intersectObjects(meshes);
        if (hits.length > 0) {
            const tile = hits[0].object.userData.tile;
            if (tile) {
                ctx.renderer3d.domElement.dispatchEvent(
                    new CustomEvent("tileclick", { detail: { x: tile.x, y: tile.y, tile } })
                );
            }
        }
    }
};
