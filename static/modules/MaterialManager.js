// MaterialManager — Material creation, PBR, caching
// Part of the EternalCities module system

window.EternalCities = window.EternalCities || {};

window.EternalCities.MaterialManager = {
    /** @type {Map<string, THREE.MeshStandardMaterial>} */
    _cache: new Map(),

    /**
     * Get or create a cached PBR material.
     * @param {string} color - CSS hex color
     * @param {number} [roughness=0.75]
     * @returns {THREE.MeshStandardMaterial}
     */
    get(color, roughness) {
        if (roughness === undefined) roughness = 0.75;
        const key = color + ":" + roughness;
        if (!this._cache.has(key)) {
            this._cache.set(key, new THREE.MeshStandardMaterial({
                color: new THREE.Color(color),
                roughness: roughness,
                metalness: 0.02
            }));
        }
        return this._cache.get(key);
    },

    /**
     * Create a special transparent material (not cached).
     * Used for water, glass, etc.
     * @param {number} color - hex color number
     * @param {number} opacity
     * @param {number} [roughness=0.05]
     * @returns {THREE.MeshStandardMaterial}
     */
    transparent(color, opacity, roughness) {
        return new THREE.MeshStandardMaterial({
            color: color,
            transparent: true,
            opacity: opacity,
            roughness: roughness !== undefined ? roughness : 0.05
        });
    },

    /**
     * Clear all cached materials (e.g. on world reset).
     */
    clearCache() {
        this._cache.forEach(m => m.dispose());
        this._cache.clear();
    }
};
