/** Terrain-Aware Object Placement System for Eternal Cities.

Provides sophisticated object placement that respects terrain characteristics,
environmental conditions, and realistic building-terrain interactions.
*/

window.EternalCities = window.EternalCities || {};

window.EternalCities.TerrainAwareRenderer = {
    /**
     * Enhanced terrain-aware object placement system
     */
    TerrainObjectPlacer: class {
        constructor(terrainData = null) {
            this.terrainData = terrainData || {};
            this.placementCache = new Map();
            this.terrainAnalyzer = this._createTerrainAnalyzer();
        }

        /**
         * Calculate realistic object placement based on terrain
         * @param {number} x - Tile X coordinate
         * @param {number} y - Tile Y coordinate
         * @param {string} objectType - Type of object (temple, house, etc.)
         * @param {Object} buildingSpec - Building specification
         * @param {Object} terrainInfo - Terrain information for the tile
         * @returns {Object} Placement specification with position, rotation, scale
         */
        calculatePlacement(x, y, objectType, buildingSpec = {}, terrainInfo = {}) {
            const cacheKey = `${x},${y},${objectType}`;
            if (this.placementCache.has(cacheKey)) {
                return this.placementCache.get(cacheKey);
            }

            // Base position (center of tile)
            let position = {
                x: x + 0.5,
                y: terrainInfo.elevation || 0,
                z: y + 0.5
            };

            let rotation = { x: 0, y: 0, z: 0 };
            let scale = { x: 1, y: 1, z: 1 };

            // Apply terrain-specific adjustments
            const adjustments = this._calculateTerrainAdjustments(x, y, objectType, terrainInfo);
            position = this._applyPositionAdjustments(position, adjustments.position);
            rotation = this._applyRotationAdjustments(rotation, adjustments.rotation);
            scale = this._applyScaleAdjustments(scale, adjustments.scale);

            // Calculate foundation requirements
            const foundation = this._calculateFoundation(objectType, terrainInfo, buildingSpec);

            // Stability and accessibility assessment
            const stability = this._assessPlacementStability(position, objectType, terrainInfo);
            const accessibility = this._assessAccessibility(position, objectType, terrainInfo);

            const placement = {
                position,
                rotation,
                scale,
                foundation,
                stability_score: stability,
                accessibility_score: accessibility,
                adaptations: adjustments.adaptations,
                terrain_type: terrainInfo.terrain_type,
                climate: terrainInfo.climate
            };

            this.placementCache.set(cacheKey, placement);
            return placement;
        }

        _calculateTerrainAdjustments(x, y, objectType, terrainInfo) {
            const adjustments = {
                position: { x: 0, y: 0, z: 0 },
                rotation: { x: 0, y: 0, z: 0 },
                scale: { x: 1, y: 1, z: 1 },
                stability: 1.0,
                accessibility: 1.0,
                adaptations: []
            };

            const terrainType = terrainInfo.terrain_type || 'flat';
            const slope = terrainInfo.slope || 0;
            const aspect = terrainInfo.aspect || 0;
            const roughness = terrainInfo.roughness || 0;

            // Slope-based adjustments
            if (slope > 0.2) {
                // Rotate to follow slope
                adjustments.rotation.z = aspect;
                adjustments.rotation.x = slope * 0.3;

                // Adjust vertical position for slope
                adjustments.position.y += slope * 0.5;

                // Reduce stability on steep slopes
                adjustments.stability *= Math.max(0.3, 1.0 - slope * 0.4);
                adjustments.adaptations.push('slope_compensation');
            }

            // Terrain type specific adjustments
            switch (terrainType) {
                case 'water':
                    adjustments.position.y = Math.max(adjustments.position.y, -0.5);
                    if (objectType === 'bridge' || objectType === 'dock') {
                        adjustments.position.y = -0.2;
                    } else {
                        adjustments.adaptations.push('water_adaptation');
                        adjustments.stability *= 0.3;
                    }
                    break;

                case 'marsh':
                    adjustments.position.y += 0.3;
                    adjustments.scale.y *= 1.1;
                    adjustments.adaptations.push('marsh_elevation');
                    adjustments.stability *= 0.4;
                    break;

                case 'sand':
                    adjustments.position.y += 0.1;
                    if (objectType === 'temple') {
                        adjustments.scale.x *= 1.05;
                        adjustments.scale.z *= 1.05;
                    }
                    adjustments.adaptations.push('sand_settlement');
                    adjustments.stability *= 0.7;
                    break;

                case 'rock':
                    adjustments.position.y += 0.2;
                    adjustments.rotation.y = Math.random() * Math.PI * 2; // Random orientation on rock
                    adjustments.adaptations.push('rock_foundation');
                    adjustments.stability *= 1.2; // Rock is stable
                    break;

                case 'forest':
                    adjustments.scale.x *= 0.9;
                    adjustments.scale.z *= 0.9;
                    adjustments.position.y += (terrainInfo.vegetation_density || 0) * 0.1;
                    adjustments.adaptations.push('forest_clearing');
                    adjustments.accessibility *= 0.7;
                    break;

                case 'mountain':
                case 'cliff':
                    adjustments.position.y += 0.5;
                    adjustments.adaptations.push('mountain_terracing');
                    adjustments.stability *= 0.6;
                    break;
            }

            // Roughness adjustments
            if (roughness > 0.5) {
                // Add slight random variation
                const seed = x * 31 + y * 17;
                const rand1 = this._seededRandom(seed);
                const rand2 = this._seededRandom(seed + 1);

                adjustments.position.x += (rand1 - 0.5) * roughness * 0.2;
                adjustments.position.z += (rand2 - 0.5) * roughness * 0.2;
                adjustments.rotation.x += (rand1 - 0.5) * roughness * 0.1;
                adjustments.rotation.z += (rand2 - 0.5) * roughness * 0.1;

                adjustments.adaptations.push('roughness_compensation');
            }

            return adjustments;
        }

        _calculateFoundation(objectType, terrainInfo, buildingSpec) {
            const foundation = {
                type: "standard",
                depth: 0.5,
                height: 0,
                material: "stone",
                adaptations: []
            };

            const terrainType = terrainInfo.terrain_type || 'flat';
            const slope = terrainInfo.slope || 0;

            // Object type specific foundations
            if (['temple', 'palace', 'monument'].includes(objectType)) {
                foundation.type = "elevated_platform";
                foundation.depth = 1.0;
                foundation.height = 0.4;
                foundation.adaptations.push("ornamental_base");
            } else if (['tower', 'fortress'].includes(objectType)) {
                foundation.type = "deep_foundation";
                foundation.depth = 2.0;
                foundation.adaptations.push("reinforced");
            } else if (objectType === 'bridge') {
                foundation.type = "pile_foundation";
                foundation.depth = 3.0;
                foundation.adaptations.push("waterproofing", "pile_driving");
            }

            // Terrain-specific adaptations
            if (['sand', 'marsh'].includes(terrainType)) {
                foundation.adaptations.push("waterproofing");
                foundation.depth *= 1.5;
            } else if (terrainType === 'rock') {
                foundation.type = "minimal_foundation";
                foundation.depth *= 0.5;
            } else if (slope > 0.5) {
                foundation.adaptations.push("retaining_walls");
                foundation.depth *= 1.2;
            }

            // Climate adaptations
            const climate = terrainInfo.climate || 'temperate';
            if (climate === 'arctic') {
                foundation.adaptations.push("permafrost_protection");
                foundation.depth *= 1.3;
            } else if (climate === 'tropical') {
                foundation.adaptations.push("termite_resistant");
                foundation.type = "raised_foundation";
            }

            return foundation;
        }

        _assessPlacementStability(position, objectType, terrainInfo) {
            let stability = terrainInfo.stability || 1.0;

            // Object type stability requirements
            const stabilityRequirements = {
                "temple": 0.8,
                "palace": 0.8,
                "house": 0.6,
                "tower": 0.9,
                "bridge": 0.7,
                "wall": 0.8,
            };

            const requiredStability = stabilityRequirements[objectType] || 0.5;

            if (stability < requiredStability) {
                // Can be compensated with engineering
                const compensationFactor = Math.min(1.0, stability / requiredStability);
                stability *= compensationFactor;
            }

            return Math.max(0.0, Math.min(1.0, stability));
        }

        _assessAccessibility(position, objectType, terrainInfo) {
            let accessibility = terrainInfo.accessibility || 1.0;

            // Slope affects accessibility
            const slope = terrainInfo.slope || 0;
            if (slope > 0.3) {
                accessibility *= Math.max(0.5, 1.0 - slope * 0.5);
            }

            // Terrain type effects
            const terrainType = terrainInfo.terrain_type || 'flat';
            const terrainAccessibility = {
                'flat': 1.0,
                'gentle_slope': 0.9,
                'steep_slope': 0.6,
                'cliff': 0.2,
                'water': 0.3,
                'marsh': 0.4,
                'forest': 0.7,
                'rock': 0.8,
            };

            accessibility *= terrainAccessibility[terrainType] || 0.8;

            return Math.max(0.0, Math.min(1.0, accessibility));
        }

        _applyPositionAdjustments(basePosition, adjustments) {
            return {
                x: basePosition.x + adjustments.x,
                y: basePosition.y + adjustments.y,
                z: basePosition.z + adjustments.z
            };
        }

        _applyRotationAdjustments(baseRotation, adjustments) {
            return {
                x: baseRotation.x + adjustments.x,
                y: baseRotation.y + adjustments.y,
                z: baseRotation.z + adjustments.z
            };
        }

        _applyScaleAdjustments(baseScale, adjustments) {
            return {
                x: baseScale.x * adjustments.x,
                y: baseScale.y * adjustments.y,
                z: baseScale.z * adjustments.z
            };
        }

        _seededRandom(seed) {
            const x = Math.sin(seed) * 10000;
            return x - Math.floor(x);
        }

        _createTerrainAnalyzer() {
            return {
                classifyTerrain: (elevation, slope, neighbors, moisture = 0.5, temperature = 20.0) => {
                    // Water detection
                    if (elevation < -0.5) return 'water';
                    if (elevation < 0.0 && moisture > 0.8) return 'marsh';

                    // Slope-based classification
                    if (slope > 1.0) {
                        return elevation > 10.0 ? 'cliff' : 'steep_slope';
                    } else if (slope > 0.3) {
                        return 'gentle_slope';
                    }

                    // Elevation-based classification
                    if (elevation > 15.0) return 'plateau';
                    if (elevation < 2.0 && slope < 0.1) return 'valley';

                    // Special conditions
                    if (moisture < 0.2 && temperature > 25.0) return 'sand';
                    if (roughness > 0.7) return 'rock';

                    return 'flat';
                },

                calculateSlope: (elevation, neighbors) => {
                    if (!neighbors || neighbors.length < 3) return [0.0, 0.0];

                    // Calculate gradients
                    const dx = (neighbors[2] - neighbors[0]) / 2.0;
                    const dy = (neighbors[5] - neighbors[3]) / 2.0;

                    const slope = Math.sqrt(dx * dx + dy * dy);
                    const aspect = slope > 0.01 ? Math.atan2(dy, dx) : 0.0;

                    return [slope, aspect];
                },

                calculateRoughness: (elevations) => {
                    if (!elevations || elevations.length < 2) return 0.0;

                    const mean = elevations.reduce((a, b) => a + b, 0) / elevations.length;
                    const variance = elevations.reduce((sum, e) => sum + Math.pow(e - mean, 2), 0) / elevations.length;
                    return Math.sqrt(variance);
                },

                assessStability: (terrainType, slope, soilType = 'loam', moisture = 0.5) => {
                    let stability = 1.0;

                    // Terrain type modifiers
                    const stabilityModifiers = {
                        'flat': 1.0,
                        'gentle_slope': 0.9,
                        'steep_slope': 0.6,
                        'cliff': 0.2,
                        'rock': 1.2,
                        'sand': 0.7,
                        'marsh': 0.4,
                        'water': 0.0,
                    };

                    stability *= stabilityModifiers[terrainType] || 0.8;

                    // Slope penalty
                    if (slope > 0.5) {
                        stability *= Math.max(0.3, 1.0 - slope * 0.4);
                    }

                    // Soil type modifiers
                    const soilModifiers = {
                        'rock': 1.3,
                        'clay': 0.9,
                        'sand': 0.6,
                        'loam': 1.0,
                        'gravel': 0.8,
                    };
                    stability *= soilModifiers[soilType] || 0.8;

                    // Moisture effects
                    if (moisture > 0.8) {
                        stability *= 0.7; // Wet soil is less stable
                    } else if (moisture < 0.2) {
                        stability *= 0.9; // Dry soil can be less cohesive
                    }

                    return Math.max(0.0, Math.min(1.0, stability));
                }
            };
        }

        /**
         * Update terrain data and clear placement cache
         * @param {Object} terrainData - New terrain data
         */
        updateTerrainData(terrainData) {
            this.terrainData = terrainData;
            this.placementCache.clear();
        }
    },

    /**
     * Environmental styling system for material adaptation
     */
    EnvironmentStylingSystem: class {
        constructor() {
            this.materialAdaptations = {
                tropical: {
                    color_shift: [0.1, 0.05, 0.0],
                    roughness_increase: 0.1,
                    detail_increase: 0.2,
                    aging_accelerated: true
                },
                arid: {
                    color_shift: [0.15, -0.05, -0.1],
                    roughness_increase: 0.2,
                    detail_increase: 0.3,
                    cracking_effects: true
                },
                arctic: {
                    color_shift: [0.2, 0.2, 0.3],
                    roughness_increase: 0.15,
                    detail_increase: 0.1,
                    frost_damage: true
                },
                mountain: {
                    color_shift: [0.05, 0.02, -0.05],
                    roughness_increase: 0.25,
                    detail_increase: 0.4,
                    erosion_effects: true
                }
            };
        }

        adaptMaterialForEnvironment(baseMaterial, climate, terrainCell, age = "weathered") {
            const materialProps = {
                name: baseMaterial,
                roughness: 0.7,
                metalness: 0.0,
                color: this._getBaseColor(baseMaterial),
                environmental_effects: []
            };

            // Apply climate adaptations
            if (climate && this.materialAdaptations[climate]) {
                const adaptation = this.materialAdaptations[climate];
                materialProps.roughness += adaptation.roughness_increase || 0;
                materialProps.roughness = Math.max(0.0, Math.min(1.0, materialProps.roughness));

                if (adaptation.cracking_effects) {
                    materialProps.environmental_effects.push("surface_cracking");
                }
                if (adaptation.frost_damage) {
                    materialProps.environmental_effects.push("frost_damage");
                }
                if (adaptation.erosion_effects) {
                    materialProps.environmental_effects.push("erosion_patterns");
                }
            }

            // Apply terrain effects
            if (terrainCell) {
                if (terrainCell.slope > 0.5) {
                    materialProps.roughness += 0.1;
                    materialProps.environmental_effects.push("slope_erosion");
                }

                if (terrainCell.moisture > 0.8) {
                    materialProps.environmental_effects.push("water_staining");
                    materialProps.roughness += 0.05;
                } else if (terrainCell.moisture < 0.2) {
                    materialProps.environmental_effects.push("desiccation_cracks");
                }

                const terrainType = terrainCell.terrain_type;
                if (terrainType === 'rock') {
                    materialProps.roughness += 0.2;
                    materialProps.environmental_effects.push("natural_roughness");
                } else if (terrainType === 'sand') {
                    materialProps.environmental_effects.push("sand_abrasion");
                } else if (terrainType === 'forest') {
                    materialProps.environmental_effects.push("lichen_growth");
                }
            }

            // Apply aging effects
            const agingMultipliers = {
                "pristine": 0.0,
                "weathered": 1.0,
                "ancient": 2.0,
                "ruined": 3.0
            };

            const ageMultiplier = agingMultipliers[age] || 1.0;

            // Accelerated aging in harsh climates
            let climateMultiplier = 1.0;
            if (climate === 'tropical' || climate === 'arid') {
                climateMultiplier = 1.5;
            } else if (climate === 'arctic') {
                climateMultiplier = 0.8;
            }

            const totalAgeMultiplier = ageMultiplier * climateMultiplier;

            // Apply aging effects
            materialProps.roughness += totalAgeMultiplier * 0.1;
            materialProps.roughness = Math.max(0.0, Math.min(1.0, materialProps.roughness));

            if (totalAgeMultiplier > 1.0) {
                materialProps.environmental_effects.push("aging_wear");
            }
            if (totalAgeMultiplier > 2.0) {
                materialProps.environmental_effects.push("structural_degradation");
            }

            return materialProps;
        }

        _getBaseColor(material) {
            const materialColors = {
                "marble": "#F5F5F5",
                "limestone": "#F5F5DC",
                "sandstone": "#DEB887",
                "granite": "#696969",
                "brick": "#CD853F",
                "terracotta": "#D2691E",
                "wood": "#8B4513",
                "thatch": "#228B22",
                "concrete": "#A9A9A9",
                "glass": "#87CEEB",
                "gold": "#FFD700",
                "bronze": "#CD7F32"
            };
            return materialColors[material] || "#C0C0C0";
        }
    },

    /**
     * Initialize the terrain-aware rendering system
     * @param {Object} options - Configuration options
     * @returns {Object} Initialized system components
     */
    initialize: function(options = {}) {
        const terrainPlacer = new this.TerrainObjectPlacer(options.terrainData);
        const stylingSystem = new this.EnvironmentStylingSystem();

        return {
            terrainPlacer,
            stylingSystem,
            updateTerrainData: function(terrainData) {
                terrainPlacer.updateTerrainData(terrainData);
            },
            calculatePlacement: function(x, y, objectType, buildingSpec, terrainInfo) {
                return terrainPlacer.calculatePlacement(x, y, objectType, buildingSpec, terrainInfo);
            },
            adaptMaterial: function(baseMaterial, climate, terrainCell, age) {
                return stylingSystem.adaptMaterialForEnvironment(baseMaterial, climate, terrainCell, age);
            }
        };
    }
};