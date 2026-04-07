// SpatialGrid — Tile lookup, occupancy checks, building group management
// Part of the EternalCities module system

window.EternalCities = window.EternalCities || {};

window.EternalCities.SpatialGrid = {
    /**
     * Create a new spatial grid state object.
     * @param {number} width
     * @param {number} height
     * @param {Array<Array<object>>} grid - 2D grid of tile data
     * @returns {object} spatial grid state
     */
    create(width, height, grid) {
        return {
            width: width,
            height: height,
            grid: grid,
            buildingGroups: new Map()
        };
    },

    /**
     * Check if coordinates are within grid bounds.
     * @param {object} state - spatial grid state
     * @param {number} x
     * @param {number} y
     * @returns {boolean}
     */
    inBounds(state, x, y) {
        return x >= 0 && y >= 0 && x < state.width && y < state.height;
    },

    /**
     * Get tile at coordinates.
     * @param {object} state - spatial grid state
     * @param {number} x
     * @param {number} y
     * @returns {object|null}
     */
    getTile(state, x, y) {
        if (!this.inBounds(state, x, y)) return null;
        return state.grid[y][x];
    },

    /**
     * Set tile at coordinates.
     * @param {object} state - spatial grid state
     * @param {number} x
     * @param {number} y
     * @param {object} tile
     */
    setTile(state, x, y, tile) {
        if (this.inBounds(state, x, y)) {
            state.grid[y][x] = tile;
        }
    },

    /**
     * Get the building group key for a tile position.
     * @param {number} x
     * @param {number} y
     * @returns {string}
     */
    key(x, y) {
        return x + "," + y;
    },

    /**
     * Store a building group reference.
     * @param {object} state - spatial grid state
     * @param {string} key
     * @param {THREE.Group} group
     */
    setGroup(state, key, group) {
        state.buildingGroups.set(key, group);
    },

    /**
     * Get a building group reference.
     * @param {object} state - spatial grid state
     * @param {string} key
     * @returns {THREE.Group|undefined}
     */
    getGroup(state, key) {
        return state.buildingGroups.get(key);
    },

    /**
     * Check if a building group exists at position.
     * @param {object} state - spatial grid state
     * @param {string} key
     * @returns {boolean}
     */
    hasGroup(state, key) {
        return state.buildingGroups.has(key);
    },

    /**
     * Remove and dispose a building group from the scene.
     * @param {object} state - spatial grid state
     * @param {THREE.Scene} scene
     * @param {string} key
     */
    removeGroup(state, scene, key) {
        if (state.buildingGroups.has(key)) {
            const old = state.buildingGroups.get(key);
            scene.remove(old);
            old.traverse(c => {
                if (c.geometry) c.geometry.dispose();
                if (c.material) c.material.dispose();
            });
            state.buildingGroups.delete(key);
        }
    },

    /**
     * Iterate over all building groups.
     * @param {object} state - spatial grid state
     * @param {function} callback - (group, key) => void
     */
    forEachGroup(state, callback) {
        state.buildingGroups.forEach(callback);
    }
};
