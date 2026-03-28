// Roma Aeterna — Generative building system with Vitruvian constraints
// Uses real architectural proportions as CONSTRAINTS, not templates.
// Every building is unique — position-seeded randomization creates variation
// in proportions, decorative detail, weathering, and features.

// ─── Seeded random for deterministic per-building variation ───
class SeededRandom {
    constructor(seed) { this.s = seed; }
    next() { this.s = (this.s * 16807 + 0) % 2147483647; return this.s / 2147483647; }
    range(min, max) { return min + this.next() * (max - min); }
    int(min, max) { return Math.floor(this.range(min, max + 1)); }
    pick(arr) { return arr[this.int(0, arr.length - 1)]; }
    // Slight color variation — shifts a hex color randomly
    varyColor(hex, amount = 15) {
        const r = parseInt(hex.slice(1,3), 16), g = parseInt(hex.slice(3,5), 16), b = parseInt(hex.slice(5,7), 16);
        const clamp = v => Math.max(0, Math.min(255, v));
        const nr = clamp(r + this.int(-amount, amount));
        const ng = clamp(g + this.int(-amount, amount));
        const nb = clamp(b + this.int(-amount, amount));
        return `#${nr.toString(16).padStart(2,"0")}${ng.toString(16).padStart(2,"0")}${nb.toString(16).padStart(2,"0")}`;
    }
}

// ─── Materials ───
const ROMAN_MATERIALS = {
    "marble":       { color: "#F0F0F0", roughness: 0.25 },
    "travertine":   { color: "#F5E6C8", roughness: 0.6 },
    "tufa":         { color: "#C8B070", roughness: 0.8 },
    "brick":        { color: "#B85C3A", roughness: 0.85 },
    "concrete":     { color: "#A09880", roughness: 0.9 },
    "stucco":       { color: "#F0EAD6", roughness: 0.7 },
    "basalt":       { color: "#3A3A3A", roughness: 0.8 },
    "numidian":     { color: "#D4A017", roughness: 0.3 },
    "porphyry":     { color: "#6D1A36", roughness: 0.3 },
    "cipollino":    { color: "#4A7A5B", roughness: 0.3 },
    "granite":      { color: "#808080", roughness: 0.5 },
    "terracotta":   { color: "#C45A3C", roughness: 0.85 },
    "bronze":       { color: "#8B6914", roughness: 0.2 },
    "wood":         { color: "#6B4226", roughness: 0.9 },
    "water":        { color: "#2980B9", roughness: 0.05 },
    "dark":         { color: "#1A1008", roughness: 0.95 },
    "red_paint":    { color: "#8E2323", roughness: 0.7 },
    "yellow_paint": { color: "#CEAC5E", roughness: 0.7 },
    "awning_red":   { color: "#CC3333", roughness: 0.8 },
};

const COLUMN_ORDERS = {
    tuscan:     { hd: 7, entRatio: 0.25, capH: 0.5, hasBase: false },
    doric:      { hd: 7.5, entRatio: 0.25, capH: 1.0, hasBase: false },
    ionic:      { hd: 8.5, entRatio: 0.2, capH: 0.33, hasBase: true },
    corinthian: { hd: 9.5, entRatio: 0.2, capH: 1.17, hasBase: true },
    composite:  { hd: 10, entRatio: 0.2, capH: 1.17, hasBase: true },
};

function mat(name, rng) {
    const m = ROMAN_MATERIALS[name] || ROMAN_MATERIALS["travertine"];
    return rng ? rng.varyColor(m.color, 12) : m.color;
}

// ═══════════════════════════════════════════════
// GENERATIVE BUILDERS
// Each takes params + tile dimensions + a seeded RNG.
// Returns a rich, varied component list.
// ═══════════════════════════════════════════════

const ParametricBuilders = {

    // ─── TEMPLE ───
    temple(p, w, d, rng) {
        const cols = p.columns || rng.pick([4, 6, 6, 8, 8, 10]);
        const style = p.style || rng.pick(["ionic", "ionic", "corinthian", "doric"]);
        const order = COLUMN_ORDERS[style] || COLUMN_ORDERS.ionic;

        // Derive all dimensions from column diameter (the Vitruvian module)
        const colDiam = w / (cols * rng.range(2.2, 2.8));
        const colH = colDiam * order.hd * rng.range(0.07, 0.09);
        const podH = colH * rng.range(0.2, 0.3);
        const podSteps = rng.int(2, 5);
        const entH = colH * order.entRatio;
        const pedH = w * rng.range(0.09, 0.13);
        const cellaW = w * rng.range(0.5, 0.65);
        const cellaD = d * rng.range(0.55, 0.7);
        const cellaH = colH * rng.range(0.75, 0.9);

        const mainMat = p.material || rng.pick(["travertine", "travertine", "tufa", "marble"]);
        const colMat = p.columnMaterial || rng.pick(["marble", "marble", "granite", "travertine"]);
        const roofMat = p.roofMaterial || "terracotta";

        const comps = [
            { type: "podium", steps: podSteps, height: podH, color: mat(mainMat, rng) },
            { type: "colonnade", columns: cols, style: style,
              height: colH, color: mat(colMat, rng), radius: colDiam / 2,
              peripteral: rng.next() > 0.3 },
            { type: "cella", height: cellaH, width: cellaW, depth: cellaD, color: mat(mainMat, rng) },
            { type: "pediment", height: pedH, color: mat(roofMat, rng) },
            { type: "door", width: cellaW * 0.25, height: cellaH * 0.6, color: mat("wood", rng) },
        ];

        // Decorative variations
        if (rng.next() > 0.4) {
            comps.push({ type: "pilasters", count: rng.int(2, 4), height: cellaH * 0.9, color: mat(colMat, rng) });
        }
        if (rng.next() > 0.7) {
            comps.push({ type: "statue", height: podH * 1.5, color: mat("bronze", rng), pedestalColor: mat(mainMat, rng) });
        }

        return comps;
    },

    // ─── BASILICA ───
    basilica(p, w, d, rng) {
        const style = p.style || rng.pick(["corinthian", "corinthian", "ionic", "composite"]);
        const mainMat = p.material || rng.pick(["travertine", "brick", "marble"]);
        const colMat = p.columnMaterial || rng.pick(["marble", "granite", "cipollino"]);
        const roofMat = p.roofMaterial || "terracotta";

        const wallH = Math.max(w, d) * rng.range(0.35, 0.5);
        const colCount = rng.int(4, 8) * 2; // always even
        const colH = wallH * rng.range(0.7, 0.9);
        const podH = rng.range(0.05, 0.1);
        const windowCount = Math.max(3, Math.floor(w / rng.range(0.1, 0.16)));

        const comps = [
            { type: "podium", steps: rng.int(1, 3), height: podH, color: mat(mainMat, rng) },
            { type: "block", stories: 1, storyHeight: wallH, color: mat(mainMat, rng),
              windows: windowCount, windowColor: mat("dark", rng) },
            { type: "colonnade", columns: colCount, style: style,
              height: colH, color: mat(colMat, rng), peripteral: false },
        ];

        // Roof type varies
        if (rng.next() > 0.6) {
            comps.push({ type: "vault", height: w * rng.range(0.15, 0.25), color: mat("concrete", rng) });
        } else {
            comps.push({ type: "tiled_roof", height: w * rng.range(0.08, 0.14), color: mat(roofMat, rng) });
        }

        // Decorative
        comps.push({ type: "door", width: 0.12, height: wallH * 0.5, color: mat("wood", rng) });
        if (rng.next() > 0.5) {
            comps.push({ type: "pilasters", count: rng.int(3, 6), height: wallH * 0.85, color: mat(colMat, rng) });
        }

        return comps;
    },

    // ─── INSULA ───
    insula(p, w, d, rng) {
        const stories = p.stories || rng.int(3, 5);
        const mainMat = p.material || rng.pick(["brick", "brick", "brick", "stucco"]);
        const roofMat = p.roofMaterial || "terracotta";

        // Ground floor is taller (shops), upper floors shorter
        const groundH = rng.range(0.2, 0.26);
        const upperH = rng.range(0.14, 0.19);
        const windowsPerFloor = Math.max(2, Math.floor(w / rng.range(0.08, 0.14)));

        // Facade color varies by floor (ground = brick shops, upper = stucco)
        const groundColor = mat(rng.pick(["brick", "tufa"]), rng);
        const upperMats = [
            rng.pick(["stucco", "stucco", "yellow_paint", "red_paint"]),
        ];

        const comps = [
            // Ground floor shops
            { type: "block", stories: 1, storyHeight: groundH,
              color: groundColor, windows: windowsPerFloor,
              windowColor: mat("dark", rng) },
        ];

        // Upper floors — each can have slightly different stucco color
        for (let i = 1; i < stories; i++) {
            const floorMat = rng.pick(upperMats);
            comps.push({ type: "block", stories: 1, storyHeight: upperH + rng.range(-0.02, 0.02),
                         color: mat(floorMat, rng),
                         windows: windowsPerFloor + rng.int(-1, 1),
                         windowColor: mat("dark", rng) });
        }

        comps.push({ type: "tiled_roof", height: w * rng.range(0.07, 0.12), color: mat(roofMat, rng) });

        // Ground floor details
        comps.push({ type: "door", width: rng.range(0.08, 0.12), height: groundH * 0.7, color: mat("wood", rng) });
        if (rng.next() > 0.5) {
            comps.push({ type: "awning", color: mat(rng.pick(["awning_red", "yellow_paint"]), rng) });
        }

        return comps;
    },

    // ─── DOMUS ───
    domus(p, w, d, rng) {
        const mainMat = p.material || rng.pick(["stucco", "stucco", "brick"]);
        const roofMat = p.roofMaterial || "terracotta";

        const wallH = rng.range(0.3, 0.42);
        const wallThickness = rng.range(0.05, 0.08);
        // Atrium proportions from Vitruvius Book 6
        const atriumH = wallH * rng.range(0.6, 0.8);

        const comps = [
            { type: "walls", height: wallH, color: mat(mainMat, rng), thickness: wallThickness },
            { type: "atrium", height: atriumH, color: mat(mainMat, rng) },
            { type: "tiled_roof", height: w * rng.range(0.08, 0.12), color: mat(roofMat, rng) },
            { type: "door", width: rng.range(0.08, 0.12), height: wallH * rng.range(0.55, 0.7),
              color: mat("wood", rng) },
        ];

        // Interior features visible from above
        if (rng.next() > 0.4) {
            comps.push({ type: "fountain", radius: Math.min(w, d) * rng.range(0.08, 0.14),
                         height: 0.15, color: mat("marble", rng) });
        }
        // Peristyle columns around garden
        if (d > 0.6 && rng.next() > 0.3) {
            comps.push({ type: "colonnade", columns: rng.int(4, 8),
                         style: rng.pick(["ionic", "corinthian"]),
                         height: wallH * 0.6, color: mat("marble", rng), peripteral: false });
        }
        // Painted walls
        if (rng.next() > 0.5) {
            comps.push({ type: "pilasters", count: rng.int(2, 4), height: wallH * 0.8,
                         color: mat(rng.pick(["red_paint", "yellow_paint"]), rng) });
        }

        return comps;
    },

    // ─── THERMAE ───
    thermae(p, w, d, rng) {
        const mainMat = p.material || rng.pick(["brick", "brick", "concrete"]);
        const domeMat = p.domeMaterial || rng.pick(["concrete", "brick"]);

        const wallH = Math.max(w, d) * rng.range(0.35, 0.5);
        const domeR = Math.min(w, d) * rng.range(0.28, 0.4);
        const windowCount = Math.max(2, Math.floor(w / rng.range(0.12, 0.18)));

        const comps = [
            { type: "podium", steps: rng.int(1, 3), height: rng.range(0.04, 0.08),
              color: mat("travertine", rng) },
            { type: "block", stories: 1, storyHeight: wallH, color: mat(mainMat, rng),
              windows: windowCount, windowColor: mat("dark", rng) },
            { type: "dome", radius: domeR, color: mat(domeMat, rng) },
        ];

        // Some thermae have vaulted sections instead of just dome
        if (rng.next() > 0.5) {
            comps.push({ type: "vault", height: domeR * 0.6, color: mat("concrete", rng) });
        }
        // Decorative columns
        if (rng.next() > 0.4) {
            comps.push({ type: "colonnade", columns: rng.int(4, 6),
                         style: rng.pick(["corinthian", "composite"]),
                         height: wallH * 0.6, color: mat("marble", rng), peripteral: false });
        }
        // Fountain in the forecourt
        if (rng.next() > 0.5) {
            comps.push({ type: "fountain", radius: rng.range(0.08, 0.15),
                         height: rng.range(0.1, 0.2), color: mat("marble", rng) });
        }
        comps.push({ type: "door", width: 0.14, height: wallH * 0.5, color: mat("wood", rng) });

        return comps;
    },

    // ─── AMPHITHEATER ───
    amphitheater(p, w, d, rng) {
        const tiers = p.tiers || rng.int(2, 4);
        const mainMat = p.material || rng.pick(["travertine", "travertine", "tufa"]);

        // Colosseum proportions: height ≈ 0.25 x major axis
        const totalH = Math.max(w, d) * rng.range(0.22, 0.3);
        const arcadeH = totalH * rng.range(0.35, 0.5);
        const archCount = Math.max(4, Math.floor(Math.max(w, d) / rng.range(0.08, 0.13)));
        const tierH = (totalH - arcadeH) / tiers;

        const comps = [
            { type: "arcade", arches: archCount, height: arcadeH, color: mat(mainMat, rng) },
        ];
        for (let i = 0; i < tiers; i++) {
            comps.push({ type: "tier", height: tierH * rng.range(0.85, 1.0),
                         color: mat(i === 0 ? mainMat : rng.pick(["travertine", "concrete", "marble"]), rng) });
        }
        // Top level decorative elements
        if (rng.next() > 0.5) {
            comps.push({ type: "pilasters", count: rng.int(4, 8),
                         height: arcadeH * 0.8, color: mat(mainMat, rng) });
        }
        if (rng.next() > 0.6) {
            comps.push({ type: "battlements", height: 0.06, color: mat(mainMat, rng) });
        }

        return comps;
    },

    // ─── AQUEDUCT ───
    aqueduct(p, w, d, rng) {
        const arches = p.arches || rng.int(3, 6);
        const mainMat = p.material || rng.pick(["tufa", "travertine", "granite"]);

        // Pier width = 1/4 arch span (Vitruvius)
        const archH = Math.max(w, d) * rng.range(0.5, 0.75);

        const comps = [
            { type: "arcade", arches: arches, height: archH, color: mat(mainMat, rng) },
            { type: "flat_roof", color: mat(mainMat, rng) },
        ];

        // Second tier for tall aqueducts
        if (rng.next() > 0.6) {
            comps.push({ type: "arcade", arches: arches * 2, height: archH * 0.5,
                         color: mat(mainMat, rng) });
        }

        return comps;
    },

    // ─── MARKET ───
    market(p, w, d, rng) {
        const mainMat = p.material || rng.pick(["brick", "brick", "tufa"]);
        const shopH = rng.range(0.28, 0.38);
        const shopCount = Math.max(1, Math.floor(w / rng.range(0.1, 0.18)));

        const comps = [
            { type: "block", stories: 1, storyHeight: shopH, color: mat(mainMat, rng),
              windows: shopCount, windowColor: mat("dark", rng) },
            { type: "awning", color: mat(rng.pick(["awning_red", "awning_red", "yellow_paint"]), rng) },
            { type: "flat_roof", color: mat(rng.pick(["concrete", "travertine"]), rng) },
            { type: "door", width: rng.range(0.1, 0.15), height: shopH * 0.65,
              color: mat("wood", rng) },
        ];

        // Some markets have a second story
        if (rng.next() > 0.6) {
            comps.push({ type: "block", stories: 1, storyHeight: rng.range(0.2, 0.28),
                         color: mat("stucco", rng), windows: shopCount - 1,
                         windowColor: mat("dark", rng) });
        }
        // Decorative columns at entrance
        if (rng.next() > 0.5) {
            comps.push({ type: "colonnade", columns: rng.int(2, 4),
                         style: rng.pick(["tuscan", "doric"]),
                         height: shopH * 0.8, color: mat("travertine", rng), peripteral: false });
        }

        return comps;
    },

    // ─── TABERNA ───
    taberna(p, w, d, rng) {
        const mainMat = p.material || rng.pick(["brick", "tufa", "stucco"]);
        const shopH = rng.range(0.22, 0.32);

        return [
            { type: "block", stories: 1, storyHeight: shopH, color: mat(mainMat, rng),
              windows: rng.int(1, 2), windowColor: mat("dark", rng) },
            { type: "awning", color: mat(rng.pick(["awning_red", "yellow_paint"]), rng) },
            { type: "flat_roof", color: mat("concrete", rng) },
            { type: "door", width: rng.range(0.1, 0.14), height: shopH * 0.7,
              color: mat("wood", rng) },
        ];
    },

    // ─── WAREHOUSE ───
    warehouse(p, w, d, rng) {
        const mainMat = p.material || rng.pick(["brick", "concrete", "tufa"]);
        const wallH = rng.range(0.35, 0.5);

        const comps = [
            { type: "block", stories: 1, storyHeight: wallH, color: mat(mainMat, rng),
              windows: 0 },
            { type: "flat_roof", color: mat("concrete", rng) },
            { type: "door", width: rng.range(0.14, 0.2), height: wallH * 0.7,
              color: mat("wood", rng) },
        ];
        // Loading doors on sides
        if (rng.next() > 0.4) {
            comps.push({ type: "arcade", arches: rng.int(2, 4),
                         height: wallH * 0.6, color: mat(mainMat, rng) });
        }
        return comps;
    },

    // ─── GATE ───
    gate(p, w, d, rng) {
        const mainMat = p.material || rng.pick(["travertine", "marble", "tufa"]);
        const totalH = Math.max(w, d) * rng.range(0.5, 0.7);
        const archCount = rng.next() > 0.6 ? 3 : 1; // single or triple bay

        const comps = [
            { type: "arcade", arches: archCount, height: totalH, color: mat(mainMat, rng) },
            { type: "battlements", height: rng.range(0.08, 0.12), color: mat(mainMat, rng) },
        ];
        // Engaged columns on gate piers
        if (rng.next() > 0.3) {
            comps.push({ type: "colonnade", columns: archCount * 2 + 2,
                         style: rng.pick(["corinthian", "composite"]),
                         height: totalH * 0.7, color: mat("marble", rng), peripteral: false });
        }
        // Attic inscription block
        if (rng.next() > 0.4) {
            comps.push({ type: "flat_roof", color: mat(mainMat, rng), overhang: 0.06 });
        }
        return comps;
    },

    // ─── MONUMENT ───
    monument(p, w, d, rng) {
        const mainMat = p.material || rng.pick(["marble", "marble", "travertine"]);
        const podSteps = rng.int(3, 6);
        const podH = rng.range(0.15, 0.3);
        const statueH = rng.range(0.3, 0.5);

        const comps = [
            { type: "podium", steps: podSteps, height: podH, color: mat(mainMat, rng) },
            { type: "statue", height: statueH,
              color: mat(rng.pick(["bronze", "bronze", "marble"]), rng),
              pedestalColor: mat(mainMat, rng) },
        ];
        // Some monuments have columns
        if (rng.next() > 0.5) {
            comps.push({ type: "colonnade", columns: 4,
                         style: rng.pick(["corinthian", "ionic"]),
                         height: statueH * 0.8, color: mat("marble", rng) });
        }
        return comps;
    },

    // ─── WALL ───
    wall(p, w, d, rng) {
        const mainMat = p.material || rng.pick(["tufa", "brick", "concrete"]);
        const wallH = rng.range(0.4, 0.6);

        return [
            { type: "walls", height: wallH, color: mat(mainMat, rng),
              thickness: rng.range(0.08, 0.15) },
            { type: "battlements", height: rng.range(0.08, 0.12), color: mat(mainMat, rng) },
        ];
    },

    // ─── BRIDGE ───
    bridge(p, w, d, rng) {
        const arches = p.arches || rng.int(2, 5);
        const mainMat = p.material || rng.pick(["travertine", "tufa", "granite"]);
        const archH = rng.range(0.4, 0.6);

        return [
            { type: "arcade", arches: arches, height: archH, color: mat(mainMat, rng) },
            { type: "flat_roof", color: mat(mainMat, rng) },
            { type: "battlements", height: 0.06, color: mat(mainMat, rng) },
        ];
    },

    // ─── CIRCUS ───
    circus(p, w, d, rng) {
        const mainMat = p.material || rng.pick(["travertine", "tufa", "brick"]);
        const wallH = rng.range(0.2, 0.3);
        const tiers = rng.int(2, 3);

        const comps = [
            { type: "walls", height: wallH, color: mat(mainMat, rng) },
        ];
        for (let i = 0; i < tiers; i++) {
            comps.push({ type: "tier", height: rng.range(0.1, 0.16),
                         color: mat(i === 0 ? mainMat : "concrete", rng) });
        }
        // Spina (central divider) — represented as a small monument
        if (rng.next() > 0.4) {
            comps.push({ type: "statue", height: rng.range(0.15, 0.25),
                         color: mat("bronze", rng), pedestalColor: mat(mainMat, rng) });
        }
        return comps;
    },
};

// ─── Entry point ───
function generateParametric(buildingType, params, w, d, tileX, tileY) {
    const builder = ParametricBuilders[buildingType];
    if (!builder) return null;

    // Seed RNG from tile position for deterministic but unique results
    const seed = ((tileX || 0) * 7919 + (tileY || 0) * 6271 + 1) | 0;
    const rng = new SeededRandom(Math.abs(seed) + 1);

    return builder(params || {}, w, d, rng);
}
