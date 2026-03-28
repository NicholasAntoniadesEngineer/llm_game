// Roma Aeterna — Parametric building generation using Vitruvian proportions
// The LLM specifies WHAT to build. This code handles HOW it looks.
//
// Each generator takes high-level params (column count, style, material)
// and derives all spatial dimensions from classical architectural ratios.

const ROMAN_MATERIALS = {
    // Stone & Masonry
    "marble":       { color: "#F0F0F0", roughness: 0.25 },
    "travertine":   { color: "#F5E6C8", roughness: 0.6 },
    "tufa":         { color: "#C8B070", roughness: 0.8 },
    "brick":        { color: "#B85C3A", roughness: 0.85 },
    "concrete":     { color: "#A09880", roughness: 0.9 },
    "stucco":       { color: "#F0EAD6", roughness: 0.7 },
    "basalt":       { color: "#3A3A3A", roughness: 0.8 },
    // Colored marbles
    "numidian":     { color: "#D4A017", roughness: 0.3 },
    "porphyry":     { color: "#6D1A36", roughness: 0.3 },
    "cipollino":    { color: "#4A7A5B", roughness: 0.3 },
    "granite":      { color: "#808080", roughness: 0.5 },
    // Functional
    "terracotta":   { color: "#C45A3C", roughness: 0.85 },
    "bronze":       { color: "#8B6914", roughness: 0.2 },
    "wood":         { color: "#6B4226", roughness: 0.9 },
    "water":        { color: "#2980B9", roughness: 0.05 },
    "dark":         { color: "#1A1008", roughness: 0.95 },
    // Pompeian painted
    "red_paint":    { color: "#8E2323", roughness: 0.7 },
    "yellow_paint": { color: "#CEAC5E", roughness: 0.7 },
    "black_paint":  { color: "#1A1110", roughness: 0.7 },
    "awning_red":   { color: "#CC3333", roughness: 0.8 },
};

// Column order proportions from Vitruvius
const COLUMN_ORDERS = {
    tuscan:     { hd: 7, entRatio: 0.25, capH: 0.5, hasBase: false, segments: 6 },
    doric:      { hd: 7.5, entRatio: 0.25, capH: 1.0, hasBase: false, segments: 8 },
    ionic:      { hd: 8.5, entRatio: 0.2, capH: 0.33, hasBase: true, segments: 10 },
    corinthian: { hd: 9.5, entRatio: 0.2, capH: 1.17, hasBase: true, segments: 12 },
    composite:  { hd: 10, entRatio: 0.2, capH: 1.17, hasBase: true, segments: 12 },
};

function getMaterial(name) {
    return ROMAN_MATERIALS[name] || ROMAN_MATERIALS["travertine"];
}

// ═══════════════════════════════════════════════
// PARAMETRIC GENERATORS
// Each returns a components array with correct proportions.
// All heights are relative to tile width (w ≈ 0.9 for single tile).
// ═══════════════════════════════════════════════

const ParametricBuilders = {

    // ─── TEMPLE ───
    // Vitruvian hexastyle prostyle/peripteral temple
    temple(params, w, d) {
        const p = Object.assign({
            columns: 6, style: "ionic", material: "travertine",
            columnMaterial: "marble", roofMaterial: "terracotta",
        }, params);

        const order = COLUMN_ORDERS[p.style] || COLUMN_ORDERS.ionic;
        const colDiam = w / (p.columns * 2.5); // diameter from fitting columns across width
        const colH = colDiam * order.hd * 0.08; // scale to tile units
        const podH = colH * 0.25; // podium = 1/4 column height
        const entH = colH * order.entRatio;
        const pedH = w * 0.11; // pediment rise ≈ 1/9 of width (15-18 degree pitch)
        const cellaW = w * 0.55;
        const cellaD = d * 0.65;
        const cellaH = colH * 0.85;

        const mat = getMaterial(p.material);
        const colMat = getMaterial(p.columnMaterial);
        const roofMat = getMaterial(p.roofMaterial);

        return [
            { type: "podium", steps: 3, height: podH, color: mat.color },
            { type: "colonnade", columns: p.columns, style: p.style,
              height: colH, color: colMat.color, radius: colDiam / 2 },
            { type: "cella", height: cellaH, width: cellaW, depth: cellaD, color: mat.color },
            { type: "pediment", height: pedH, color: roofMat.color },
        ];
    },

    // ─── BASILICA ───
    basilica(params, w, d) {
        const p = Object.assign({
            style: "corinthian", material: "travertine",
            columnMaterial: "marble", roofMaterial: "terracotta",
        }, params);

        const wallH = d * 0.45;
        const colH = wallH * 0.8;
        const podH = 0.06;
        const mat = getMaterial(p.material);
        const colMat = getMaterial(p.columnMaterial);
        const roofMat = getMaterial(p.roofMaterial);

        return [
            { type: "podium", steps: 2, height: podH, color: mat.color },
            { type: "block", stories: 1, storyHeight: wallH, color: mat.color, windows: Math.max(2, Math.floor(w / 0.15)) },
            { type: "colonnade", columns: Math.max(4, Math.floor(w / 0.12)),
              style: p.style, height: colH, color: colMat.color, peripteral: false },
            { type: "tiled_roof", height: w * 0.12, color: roofMat.color },
        ];
    },

    // ─── INSULA (apartment block) ───
    // Augustan height limit ≈ 20m. At 10m/tile, that's 2.0 tile units.
    // For our scale (~0.9 tile width), 3-5 stories at 0.15-0.2 each.
    insula(params, w, d) {
        const p = Object.assign({
            stories: 4, material: "brick", roofMaterial: "terracotta",
        }, params);

        const stories = Math.min(p.stories, 6);
        // Story heights decrease going up (ground floor taller)
        const groundH = 0.22;
        const upperH = 0.16;
        const mat = getMaterial(p.material);
        const roofMat = getMaterial(p.roofMaterial);
        const stuccoMat = getMaterial("stucco");

        const comps = [];
        // Ground floor (shops/tabernae)
        comps.push({ type: "block", stories: 1, storyHeight: groundH,
                     color: mat.color, windows: Math.max(2, Math.floor(w / 0.12)),
                     windowColor: getMaterial("dark").color });
        // Upper floors (stuccoed)
        if (stories > 1) {
            comps.push({ type: "block", stories: stories - 1, storyHeight: upperH,
                         color: stuccoMat.color, windows: Math.max(2, Math.floor(w / 0.1)),
                         windowColor: getMaterial("dark").color });
        }
        comps.push({ type: "tiled_roof", height: w * 0.1, color: roofMat.color });
        return comps;
    },

    // ─── DOMUS (private house) ───
    domus(params, w, d) {
        const p = Object.assign({
            material: "stucco", roofMaterial: "terracotta",
        }, params);

        const wallH = 0.35;
        const mat = getMaterial(p.material);
        const roofMat = getMaterial(p.roofMaterial);

        return [
            { type: "walls", height: wallH, color: mat.color, thickness: 0.06 },
            { type: "atrium", height: wallH * 0.7, color: mat.color },
            { type: "tiled_roof", height: w * 0.1, color: roofMat.color },
            { type: "door", width: 0.1, height: 0.2, color: getMaterial("wood").color },
        ];
    },

    // ─── THERMAE (baths) ───
    thermae(params, w, d) {
        const p = Object.assign({
            material: "brick", domeMaterial: "concrete",
        }, params);

        const wallH = Math.max(w, d) * 0.4;
        const domeR = Math.min(w, d) * 0.35;
        const mat = getMaterial(p.material);
        const domeMat = getMaterial(p.domeMaterial);

        return [
            { type: "podium", steps: 2, height: 0.06, color: getMaterial("travertine").color },
            { type: "block", stories: 1, storyHeight: wallH, color: mat.color,
              windows: Math.max(2, Math.floor(w / 0.15)) },
            { type: "dome", radius: domeR, color: domeMat.color },
        ];
    },

    // ─── AMPHITHEATER ───
    amphitheater(params, w, d) {
        const p = Object.assign({
            tiers: 3, material: "travertine",
        }, params);

        const arcadeH = Math.min(w, d) * 0.25;
        const tierH = 0.12;
        const mat = getMaterial(p.material);

        const comps = [
            { type: "arcade", arches: Math.max(3, Math.floor(w / 0.12)),
              height: arcadeH, color: mat.color },
        ];
        for (let i = 0; i < p.tiers; i++) {
            comps.push({ type: "tier", height: tierH, color: getMaterial("travertine").color });
        }
        return comps;
    },

    // ─── AQUEDUCT ───
    aqueduct(params, w, d) {
        const p = Object.assign({
            arches: 3, material: "tufa",
        }, params);

        const archH = Math.max(w, d) * 0.6;
        const mat = getMaterial(p.material);

        return [
            { type: "arcade", arches: p.arches, height: archH, color: mat.color },
            { type: "flat_roof", color: mat.color },
        ];
    },

    // ─── MARKET ───
    market(params, w, d) {
        const p = Object.assign({
            material: "brick",
        }, params);

        const mat = getMaterial(p.material);

        return [
            { type: "block", stories: 1, storyHeight: 0.3, color: mat.color,
              windows: Math.max(1, Math.floor(w / 0.15)) },
            { type: "awning", color: getMaterial("awning_red").color },
            { type: "flat_roof", color: getMaterial("travertine").color },
        ];
    },

    // ─── TABERNA (shop) ───
    taberna(params, w, d) {
        const p = Object.assign({ material: "brick" }, params);
        const mat = getMaterial(p.material);

        return [
            { type: "block", stories: 1, storyHeight: 0.25, color: mat.color, windows: 1 },
            { type: "awning", color: getMaterial("awning_red").color },
            { type: "flat_roof", color: getMaterial("concrete").color },
            { type: "door", width: 0.12, height: 0.18, color: getMaterial("wood").color },
        ];
    },

    // ─── WAREHOUSE ───
    warehouse(params, w, d) {
        const p = Object.assign({ material: "brick" }, params);
        const mat = getMaterial(p.material);

        return [
            { type: "block", stories: 1, storyHeight: 0.4, color: mat.color, windows: 0 },
            { type: "flat_roof", color: getMaterial("concrete").color },
            { type: "door", width: 0.15, height: 0.25, color: getMaterial("wood").color },
        ];
    },

    // ─── GATE ───
    gate(params, w, d) {
        const p = Object.assign({ material: "travertine" }, params);
        const mat = getMaterial(p.material);
        const archH = Math.max(w, d) * 0.5;

        return [
            { type: "arcade", arches: 1, height: archH, color: mat.color },
            { type: "battlements", height: 0.1, color: mat.color },
        ];
    },

    // ─── MONUMENT ───
    monument(params, w, d) {
        const p = Object.assign({
            material: "marble",
        }, params);
        const mat = getMaterial(p.material);

        return [
            { type: "podium", steps: 4, height: 0.2, color: mat.color },
            { type: "statue", height: 0.4, color: getMaterial("bronze").color,
              pedestalColor: mat.color },
        ];
    },

    // ─── WALL ───
    wall(params, w, d) {
        const p = Object.assign({ material: "tufa" }, params);
        const mat = getMaterial(p.material);

        return [
            { type: "walls", height: 0.5, color: mat.color, thickness: 0.12 },
            { type: "battlements", height: 0.1, color: mat.color },
        ];
    },

    // ─── BRIDGE ───
    bridge(params, w, d) {
        const p = Object.assign({ arches: 3, material: "travertine" }, params);
        const mat = getMaterial(p.material);

        return [
            { type: "arcade", arches: p.arches, height: 0.5, color: mat.color },
            { type: "flat_roof", color: mat.color },
        ];
    },

    // ─── CIRCUS ───
    circus(params, w, d) {
        const p = Object.assign({ material: "travertine" }, params);
        const mat = getMaterial(p.material);

        return [
            { type: "walls", height: 0.25, color: mat.color },
            { type: "tier", height: 0.15, color: mat.color },
            { type: "tier", height: 0.12, color: getMaterial("concrete").color },
        ];
    },
};

// Generate components for a building type with params
function generateParametric(buildingType, params, w, d) {
    const builder = ParametricBuilders[buildingType];
    if (builder) return builder(params || {}, w, d);
    return null;
}
