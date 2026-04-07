// CameraController — Camera setup, orbit controls, zoom, pan
// Part of the EternalCities module system

window.EternalCities = window.EternalCities || {};

window.EternalCities.CameraController = {
    /**
     * Initialize camera state on the renderer instance.
     * @param {WorldRenderer} ctx - the renderer instance (provides .camera, .container, etc.)
     */
    init(ctx) {
        ctx.camera = new THREE.PerspectiveCamera(
            50,
            ctx.container.clientWidth / ctx.container.clientHeight,
            0.1,
            200
        );

        // Orbit state
        ctx.cameraAngle = Math.PI / 4;
        ctx.cameraPitch = 0.5;
        ctx.cameraDistance = 55;
        ctx.cameraTarget = new THREE.Vector3(20, 0, 20);
        ctx.isDragging = false;
        ctx.prevMouse = { x: 0, y: 0 };
    },

    /**
     * Recompute camera position from orbit parameters.
     */
    updateCamera(ctx) {
        const t = ctx.cameraTarget;
        ctx.camera.position.set(
            t.x + ctx.cameraDistance * Math.cos(ctx.cameraPitch) * Math.cos(ctx.cameraAngle),
            t.y + ctx.cameraDistance * Math.sin(ctx.cameraPitch),
            t.z + ctx.cameraDistance * Math.cos(ctx.cameraPitch) * Math.sin(ctx.cameraAngle)
        );
        ctx.camera.lookAt(t);
    },

    /**
     * Handle container resize — update aspect ratio and renderer size.
     */
    onResize(ctx) {
        const w = ctx.container.clientWidth, h = ctx.container.clientHeight;
        ctx.camera.aspect = w / h;
        ctx.camera.updateProjectionMatrix();
        ctx.renderer3d.setSize(w, h);
    },

    /**
     * Center the camera on the world after init.
     */
    centerOn(ctx, worldWidth, worldHeight) {
        ctx.cameraTarget.set(worldWidth / 2, 0, worldHeight / 2);
        this.updateCamera(ctx);
    }
};
