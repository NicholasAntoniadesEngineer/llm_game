/**
 * Grammar Engine for Eternal Cities
 *
 * Expands compact grammar specifications (e.g. {grammar: "roman_temple", params: {order: "corinthian", cols: 8}})
 * into full shape arrays compatible with the renderer's procedural component format.
 *
 * Output format: each grammar returns an array of procedural shape dicts:
 *   { shape: "box"|"cylinder"|"cone"|"sphere"|"torus", position: [x,y,z], size/radius/height/..., color: "#hex", roughness: N, metalness: N }
 *
 * These are wrapped into a { type: "procedural", parts: [...] } component for the renderer.
 *
 * Loaded via <script> tag before renderer3d.js. Attaches to window.EternalCities.GrammarEngine.
 */
(function (global) {
    "use strict";

    // ═══════════════════════════════════════════════════════════════════════
    // Namespace
    // ═══════════════════════════════════════════════════════════════════════

    global.EternalCities = global.EternalCities || {};

    // ═══════════════════════════════════════════════════════════════════════
    // Material Palette (loaded from data/material_palette.json, with fallbacks)
    // ═══════════════════════════════════════════════════════════════════════

    var MATERIALS = {
        travertine:    { color: "#f5ead6", roughness: 0.65, metalness: 0.01 },
        tufa:          { color: "#c4b896", roughness: 0.85, metalness: 0.01 },
        marble:        { color: "#f0ece4", roughness: 0.25, metalness: 0.02 },
        granite:       { color: "#8a8e8c", roughness: 0.35, metalness: 0.03 },
        porphyry:      { color: "#6b2d4e", roughness: 0.30, metalness: 0.04 },
        brick:         { color: "#b5603a", roughness: 0.90, metalness: 0.01 },
        concrete:      { color: "#a89a86", roughness: 0.95, metalness: 0.01 },
        terracotta:    { color: "#c45a2c", roughness: 0.80, metalness: 0.01 },
        bronze:        { color: "#8b6914", roughness: 0.40, metalness: 0.65 },
        gilded:        { color: "#daa520", roughness: 0.30, metalness: 0.55 },
        stucco:        { color: "#f0ece0", roughness: 0.85, metalness: 0.01 },
        wood:          { color: "#6b4226", roughness: 0.85, metalness: 0.01 },
        lead:          { color: "#6b6b6b", roughness: 0.70, metalness: 0.25 },
        basalt:        { color: "#404040", roughness: 0.80, metalness: 0.02 },
        sandstone:     { color: "#c8b070", roughness: 0.80, metalness: 0.01 },
        limestone:     { color: "#f5e6c8", roughness: 0.75, metalness: 0.01 },
    };

    var DEFAULT_MAT = { color: "#c2b280", roughness: 0.75, metalness: 0.01 };

    // ═══════════════════════════════════════════════════════════════════════
    // Utility helpers
    // ═══════════════════════════════════════════════════════════════════════

    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
    function clampInt(v, lo, hi) { var n = Math.round(Number(v)); return Number.isFinite(n) ? clamp(n, lo, hi) : lo; }
    function dflt(v, d) { return (v != null && Number.isFinite(Number(v))) ? Number(v) : d; }

    /** Resolve a material name or hex color string into {color, roughness, metalness}. */
    function mat(nameOrHex) {
        if (!nameOrHex) return DEFAULT_MAT;
        if (typeof nameOrHex === "string" && nameOrHex.charAt(0) === "#") {
            return { color: nameOrHex, roughness: 0.75, metalness: 0.01 };
        }
        var key = String(nameOrHex).toLowerCase().replace(/[\s-]/g, "_");
        var m = MATERIALS[key];
        if (m) return m;
        // Check loaded palette
        var loaded = global.EternalCities.GrammarEngine._materials[key];
        if (loaded) return loaded;
        return DEFAULT_MAT;
    }

    /** Create a shape dict with material applied. */
    function shapeBox(pos, size, material) {
        var m = mat(material);
        return { shape: "box", position: pos, size: size, color: m.color, roughness: m.roughness, metalness: m.metalness };
    }

    function shapeCyl(pos, radius, height, material, rTop, segs) {
        var m = mat(material);
        var s = { shape: "cylinder", position: pos, radius: radius, height: height, color: m.color, roughness: m.roughness, metalness: m.metalness };
        if (rTop != null) s.radiusTop = rTop;
        if (segs != null) s.radialSegments = segs;
        return s;
    }

    function shapeCylTapered(pos, rBot, rTop, height, material, segs) {
        var m = mat(material);
        return { shape: "cylinder", position: pos, radiusBottom: rBot, radiusTop: rTop, height: height, color: m.color, roughness: m.roughness, metalness: m.metalness, radialSegments: segs || 12 };
    }

    function shapeCone(pos, radius, height, material) {
        var m = mat(material);
        return { shape: "cone", position: pos, radius: radius, height: height, color: m.color, roughness: m.roughness, metalness: m.metalness };
    }

    function shapeSphere(pos, radius, material) {
        var m = mat(material);
        return { shape: "sphere", position: pos, radius: radius, color: m.color, roughness: m.roughness, metalness: m.metalness };
    }

    function shapeTorus(pos, radius, tube, material) {
        var m = mat(material);
        return { shape: "torus", position: pos, radius: radius, tube: tube, color: m.color, roughness: m.roughness, metalness: m.metalness };
    }

    /** Cap total shapes to avoid performance problems. */
    var MAX_SHAPES = 100;
    function capShapes(arr) {
        if (arr.length > MAX_SHAPES) arr.length = MAX_SHAPES;
        return arr;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // Sub-grammars (reusable components)
    // ═══════════════════════════════════════════════════════════════════════

    /**
     * _colonnade: Generate a row/ring of columns with bases, shafts, and capitals.
     * @param {number} count    - number of columns
     * @param {number} spacing  - center-to-center distance between columns
     * @param {number} h        - total column height (base + shaft + capital)
     * @param {number} r        - shaft radius at base
     * @param {string} order    - "doric"|"ionic"|"corinthian"
     * @param {number} startX   - x offset of first column center
     * @param {number} startZ   - z position
     * @param {number} baseY    - y position of column base bottom
     * @param {string} material - material name
     * @param {string} axis     - "x" (columns march along x) or "z" (along z)
     * @returns {object[]} shape array
     */
    function _colonnade(count, spacing, h, r, order, startX, startZ, baseY, material, axis) {
        var shapes = [];
        if (count <= 0) return shapes;

        // Order proportions (height:diameter ratios affect shaft vs capital)
        var ord = String(order || "ionic").toLowerCase();
        var baseH, shaftH, capH, capR, entasisBulge;
        if (ord === "doric") {
            // Doric: h/d = 6:1, flat echinus capital, no base
            baseH = 0;
            capH = h * 0.08;
            shaftH = h - capH;
            capR = r * 1.4;
            entasisBulge = 1.08;
        } else if (ord === "corinthian") {
            // Corinthian: h/d = 10:1, elaborate capital
            baseH = h * 0.04;
            capH = h * 0.12;
            shaftH = h - baseH - capH;
            capR = r * 1.6;
            entasisBulge = 1.02;
        } else {
            // Ionic: h/d = 8:1, volute capital
            baseH = h * 0.04;
            capH = h * 0.08;
            shaftH = h - baseH - capH;
            capR = r * 1.35;
            entasisBulge = 1.04;
        }

        var ax = axis || "x";

        for (var i = 0; i < count; i++) {
            var cx, cz;
            if (ax === "x") {
                cx = startX + i * spacing;
                cz = startZ;
            } else {
                cx = startX;
                cz = startZ + i * spacing;
            }

            var y = baseY;

            // Base (torus-like disc, except Doric which has none)
            if (baseH > 0) {
                shapes.push(shapeCyl([cx, y + baseH / 2, cz], r * 1.3, baseH, material));
                y += baseH;
            }

            // Shaft (slight entasis: wider at 1/3 height)
            var rBot = r * entasisBulge;
            var rTop = r * 0.92;
            shapes.push(shapeCylTapered([cx, y + shaftH / 2, cz], rBot, rTop, shaftH, material, 10));
            y += shaftH;

            // Capital
            if (ord === "doric") {
                // Echinus (flared disc) + abacus (flat square)
                shapes.push(shapeCylTapered([cx, y + capH * 0.6 / 2, cz], r, capR, capH * 0.6, material, 10));
                shapes.push(shapeBox([cx, y + capH * 0.6 + capH * 0.4 / 2, cz], [capR * 2, capH * 0.4, capR * 2], material));
            } else if (ord === "corinthian") {
                // Bell + volutes (simplified as stacked tapered cylinders + abacus)
                shapes.push(shapeCylTapered([cx, y + capH * 0.7 / 2, cz], r, capR * 0.9, capH * 0.7, material, 10));
                shapes.push(shapeBox([cx, y + capH * 0.7 + capH * 0.3 / 2, cz], [capR * 2.1, capH * 0.3, capR * 2.1], material));
            } else {
                // Ionic: flat volute disc + abacus
                shapes.push(shapeCyl([cx, y + capH * 0.5 / 2, cz], capR, capH * 0.5, material));
                shapes.push(shapeBox([cx, y + capH * 0.5 + capH * 0.5 / 2, cz], [capR * 2.2, capH * 0.5, capR * 1.5], material));
            }
        }
        return shapes;
    }

    /**
     * _podium: Stepped platform.
     * @returns {object[]} shapes, with y starting at baseY
     */
    function _podium(w, d, h, steps, baseY, material) {
        var shapes = [];
        var nSteps = clampInt(steps, 1, 12);
        var stepH = h / nSteps;
        for (var i = 0; i < nSteps; i++) {
            var frac = 1 - (i / nSteps) * 0.3; // Each step inset slightly
            var sw = w * frac;
            var sd = d * frac;
            shapes.push(shapeBox([0, baseY + i * stepH + stepH / 2, 0], [sw, stepH, sd], material));
        }
        return shapes;
    }

    /**
     * _pediment: Triangular gable sitting on top of a structure.
     * @param {number} w - width
     * @param {number} h - peak height
     * @param {number} d - depth (thickness)
     * @param {number} baseY - y position of pediment base
     * @param {string} material
     * @returns {object[]}
     */
    function _pediment(w, h, d, baseY, material) {
        var shapes = [];
        // Tympanum (triangle approximated as a tapered box — narrow at top)
        shapes.push(shapeCylTapered([0, baseY + h / 2, 0], w / 2, 0.001, h, material, 3));
        // Horizontal cornice (base beam)
        shapes.push(shapeBox([0, baseY + 0.005, 0], [w * 1.05, d * 0.3, d], material));
        // Raking cornices (angled beams) — simplified as thin boxes
        var halfW = w / 2;
        shapes.push(shapeBox([-halfW * 0.5, baseY + h * 0.5 * 0.5 + 0.005, 0], [halfW * 1.05, d * 0.2, d * 1.05], material));
        shapes.push(shapeBox([halfW * 0.5, baseY + h * 0.5 * 0.5 + 0.005, 0], [halfW * 1.05, d * 0.2, d * 1.05], material));
        return shapes;
    }

    /**
     * _arcade: Series of arches on piers.
     * @param {number} count   - number of arches
     * @param {number} spanW   - width of each arch opening
     * @param {number} h       - total height (pier + arch)
     * @param {number} pierW   - pier width
     * @param {number} baseY   - y position
     * @param {number} depth   - depth (z extent) of the arcade
     * @param {string} material
     * @returns {object[]}
     */
    function _arcade(count, spanW, h, pierW, baseY, depth, material) {
        var shapes = [];
        var n = clampInt(count, 1, 16);
        var totalW = n * spanW + (n + 1) * pierW;
        var pierH = h * 0.7;
        var archH = h * 0.3;
        var startX = -totalW / 2 + pierW / 2;

        for (var i = 0; i <= n; i++) {
            // Pier
            var px = startX + i * (spanW + pierW);
            shapes.push(shapeBox([px, baseY + pierH / 2, 0], [pierW, pierH, depth], material));
        }

        // Arch tops (semicircle approximated as short cylinders bridging piers)
        for (var j = 0; j < n; j++) {
            var cx = startX + pierW / 2 + j * (spanW + pierW) + spanW / 2;
            // Lintel/arch beam at top
            shapes.push(shapeBox([cx, baseY + pierH + archH / 2, 0], [spanW, archH * 0.4, depth], material));
            // Arch curve (torus segment approximated as a thin box for simplicity and shape budget)
            shapes.push(shapeBox([cx, baseY + pierH, 0], [spanW * 0.9, archH * 0.25, depth * 0.9], material));
        }

        // Top entablature beam
        shapes.push(shapeBox([0, baseY + h - h * 0.05, 0], [totalW, h * 0.1, depth * 1.02], material));

        return shapes;
    }

    /**
     * _roof: Generate roof shapes.
     * @param {string} type - "gable"|"flat"|"barrel"|"hip"
     */
    function _roof(w, d, pitch, type, baseY, material) {
        var shapes = [];
        var t = String(type || "gable").toLowerCase();
        var roofH = dflt(pitch, 0.15) * w;

        if (t === "flat") {
            shapes.push(shapeBox([0, baseY + 0.01, 0], [w * 1.02, 0.02, d * 1.02], material));
        } else if (t === "barrel") {
            // Barrel vault approximated as stacked curved slabs
            var segs = 6;
            for (var i = 0; i < segs; i++) {
                var a0 = (i / segs) * Math.PI;
                var a1 = ((i + 1) / segs) * Math.PI;
                var x0 = Math.cos(a0) * w / 2;
                var y0 = Math.sin(a0) * roofH;
                var x1 = Math.cos(a1) * w / 2;
                var y1 = Math.sin(a1) * roofH;
                var cx = (x0 + x1) / 2;
                var cy = (y0 + y1) / 2;
                var segW = Math.sqrt((x1 - x0) * (x1 - x0) + (y1 - y0) * (y1 - y0));
                shapes.push(shapeBox([cx, baseY + cy, 0], [segW, 0.015, d], material));
            }
        } else {
            // Gable roof: two angled planes (simplified as wedge boxes)
            var halfW = w / 2;
            // Ridge beam
            shapes.push(shapeBox([0, baseY + roofH, 0], [w * 0.04, 0.02, d * 1.02], material));
            // Left slope
            shapes.push(shapeBox([-halfW * 0.5, baseY + roofH * 0.5, 0], [halfW * 1.05, 0.02, d * 1.02], material));
            // Right slope
            shapes.push(shapeBox([halfW * 0.5, baseY + roofH * 0.5, 0], [halfW * 1.05, 0.02, d * 1.02], material));
            // Eaves
            shapes.push(shapeBox([0, baseY + 0.005, 0], [w * 1.08, 0.01, d * 1.06], material));
        }
        return shapes;
    }

    /**
     * _wall: Rectangular wall segment.
     * @param {string} construction - "opus_quadratum"|"brick"|"plain"
     */
    function _wall(w, h, d, baseY, material, construction) {
        var shapes = [];
        shapes.push(shapeBox([0, baseY + h / 2, 0], [w, h, d], material));
        return shapes;
    }

    /**
     * _window: Arched or rectangular opening (represented as dark inset box).
     */
    function _window(x, y, z, w, h, style) {
        var shapes = [];
        var dark = { color: "#1A1008", roughness: 0.90, metalness: 0.01 };
        shapes.push({ shape: "box", position: [x, y, z], size: [w, h, 0.02], color: dark.color, roughness: dark.roughness, metalness: dark.metalness });
        if (style === "arched") {
            // Small semicircle on top
            shapes.push({ shape: "cylinder", position: [x, y + h / 2 + w / 4, z], radius: w / 2, height: 0.02, color: dark.color, roughness: dark.roughness, metalness: dark.metalness, radialSegments: 8 });
        }
        return shapes;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // Dense shape format expansion
    // ═══════════════════════════════════════════════════════════════════════

    var DENSE_TYPE_MAP = {
        b:  "box",
        c:  "cylinder",
        n:  "cone",
        s:  "sphere",
        t:  "torus",
        h:  "sphere",      // hemisphere treated as sphere (renderer doesn't have hemisphere)
        br: "barrel_roof",
        a:  "arch",
        w:  "box",          // wedge approximated as box
        st: "box",          // stairs approximated as box
        co: "colonnade_ring",
        r:  "torus",        // ring mapped to torus
    };

    /**
     * Convert a dense array shape to the dict format the renderer expects.
     * Formats:
     *   ["b", [x,y,z], [sx,sy,sz], "material"]              -> box
     *   ["c", [x,y,z], radius, height, "material"]           -> cylinder
     *   ["n", [x,y,z], radius, height, "material"]           -> cone
     *   ["s", [x,y,z], radius, "material"]                   -> sphere
     *   ["t", [x,y,z], radius, tube, "material"]             -> torus
     *   ["h", [x,y,z], radius, "material"]                   -> hemisphere (sphere)
     *   ["br", [x,y,z], [w,d,h], "material"]                 -> barrel_roof
     *   ["a", [x,y,z], [w,h,t], "material"]                  -> arch
     *   ["w", [x,y,z], [sx,sy,sz], "material"]               -> wedge (box approx)
     *   ["st", [x,y,z], [sx,sy,sz], "material"]              -> stairs (box approx)
     *   ["co", [x,y,z], radius, count, height, "material"]   -> colonnade_ring
     *   ["r", [x,y,z], radius, tube, "material"]             -> ring (torus)
     */
    function expandOneDense(arr) {
        if (!Array.isArray(arr) || arr.length < 3) return null;
        var code = arr[0];
        var pos = arr[1];
        if (!Array.isArray(pos) || pos.length < 3) return null;

        var shape = DENSE_TYPE_MAP[code];
        if (!shape) return null;

        var m, result;

        switch (code) {
            case "b":
            case "w":
            case "st":
                m = mat(arr[3]);
                result = { shape: "box", position: pos, size: arr[2], color: m.color, roughness: m.roughness, metalness: m.metalness };
                break;
            case "c":
                m = mat(arr[4]);
                result = { shape: "cylinder", position: pos, radius: arr[2], height: arr[3], color: m.color, roughness: m.roughness, metalness: m.metalness };
                break;
            case "n":
                m = mat(arr[4]);
                result = { shape: "cone", position: pos, radius: arr[2], height: arr[3], color: m.color, roughness: m.roughness, metalness: m.metalness };
                break;
            case "s":
            case "h":
                m = mat(arr[3]);
                result = { shape: "sphere", position: pos, radius: arr[2], color: m.color, roughness: m.roughness, metalness: m.metalness };
                if (code === "h") result.heightSegments = 4; // half-sphere visual hint
                break;
            case "t":
            case "r":
                m = mat(arr[4]);
                result = { shape: "torus", position: pos, radius: arr[2], tube: arr[3], color: m.color, roughness: m.roughness, metalness: m.metalness };
                break;
            case "br":
                m = mat(arr[3]);
                var dims = arr[2] || [0.5, 0.5, 0.15];
                result = { shape: "barrel_roof", position: pos, width: dims[0], depth: dims[1], height: dims[2], color: m.color, roughness: m.roughness, metalness: m.metalness };
                break;
            case "a":
                m = mat(arr[3]);
                var adims = arr[2] || [0.3, 0.4, 0.04];
                result = { shape: "arch", position: pos, width: adims[0], height: adims[1], thickness: adims[2], color: m.color, roughness: m.roughness, metalness: m.metalness };
                break;
            case "co":
                m = mat(arr[5]);
                result = { shape: "colonnade_ring", position: pos, radius: arr[2], column_count: arr[3], height: arr[4], color: m.color, roughness: m.roughness, metalness: m.metalness };
                break;
            default:
                return null;
        }
        return result;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar registry
    // ═══════════════════════════════════════════════════════════════════════

    var _registry = {};

    function register(name, fn) { _registry[name] = fn; }

    function expand(grammar, params) {
        var fn = _registry[grammar];
        if (!fn) {
            console.warn("GrammarEngine: unknown grammar '" + grammar + "'");
            return [];
        }
        var p = (params && typeof params === "object") ? params : {};
        var shapes = fn(p);
        return capShapes(shapes);
    }

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #1: roman_temple
    // ═══════════════════════════════════════════════════════════════════════

    register("roman_temple", function (p) {
        var shapes = [];

        // Defaults
        var order = String(p.order || "ionic").toLowerCase();
        var pod = p.podium || {};
        var podSteps = clampInt(pod.steps || pod.s, 3, 10);
        var podH = dflt(pod.h, 0.18);
        var cel = p.cella || {};
        var celW = dflt(cel.w, 0.6);
        var celD = dflt(cel.d, 0.9);
        var celH = dflt(cel.h, 0.5);
        var cols = p.cols || {};
        var colArr = String(cols.arr || "prostyle").toLowerCase();
        var colFront = clampInt(cols.front || cols.count, 4, 12);
        var colSide = clampInt(cols.side, 0, 16);
        if (colSide === 0) colSide = colFront + 2;
        var ped = p.pediment || {};
        var pedPitch = dflt(ped.pitch, 0.22);
        var wallMat = p.wall || "travertine";
        var colMat = p.col_material || "marble";
        var roofMat = p.roof_material || "terracotta";
        var podMat = p.podium_material || "travertine";

        var y = 0;

        // Column proportions from order
        var colRatio = order === "doric" ? 6 : (order === "corinthian" ? 10 : 8);
        var colH = celH * 0.85;
        var colR = colH / (colRatio * 2);
        var colSpacing = celW / (colFront - 1);

        // 1. Crepidoma / Podium
        var podW = celW + colSpacing * 2;
        var podD = celD + colSpacing * 2;
        shapes = shapes.concat(_podium(podW, podD, podH, podSteps, y, podMat));
        y += podH;

        var platformY = y;

        // 2. Cella walls
        shapes.push(shapeBox([0, y + celH / 2, 0], [celW, celH, celD], wallMat));

        // 3. Colonnade
        var halfW = (colFront - 1) * colSpacing / 2;
        var halfD = (colSide - 1) * colSpacing / 2;
        var frontZ = -celD / 2 - colSpacing * 0.6;
        var backZ = celD / 2 + colSpacing * 0.6;

        // Front colonnade (always present)
        shapes = shapes.concat(_colonnade(colFront, colSpacing, colH, colR, order, -halfW, frontZ, y, colMat, "x"));

        if (colArr === "amphiprostyle" || colArr === "peripteral") {
            // Back colonnade
            shapes = shapes.concat(_colonnade(colFront, colSpacing, colH, colR, order, -halfW, backZ, y, colMat, "x"));
        }

        if (colArr === "peripteral") {
            // Side colonnades (excluding corners already placed)
            var sideCount = colSide - 2;
            if (sideCount > 0) {
                var sideSpacing = (celD + colSpacing * 1.2) / (colSide - 1);
                var sideStartZ = frontZ + sideSpacing;
                // Left side
                shapes = shapes.concat(_colonnade(sideCount, sideSpacing, colH, colR, order, -halfW, sideStartZ, y, colMat, "z"));
                // Right side
                shapes = shapes.concat(_colonnade(sideCount, sideSpacing, colH, colR, order, halfW, sideStartZ, y, colMat, "z"));
            }
        }

        // 4. Entablature (beam on top of columns)
        var entH = colH * 0.08;
        var entW = podW;
        var entD = (colArr === "peripteral") ? podD : celD * 0.3;
        shapes.push(shapeBox([0, y + colH + entH / 2, frontZ], [entW, entH, 0.04], colMat));
        if (colArr === "amphiprostyle" || colArr === "peripteral") {
            shapes.push(shapeBox([0, y + colH + entH / 2, backZ], [entW, entH, 0.04], colMat));
        }

        // 5. Pediment (front)
        var pedBaseY = y + colH + entH;
        var pedH = celW * pedPitch;
        shapes = shapes.concat(_pediment(podW, pedH, 0.03, pedBaseY, colMat));

        // 6. Roof
        shapes = shapes.concat(_roof(celW * 1.1, celD * 1.05, pedPitch * 0.5, "gable", y + celH, roofMat));

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #2: basilica
    // ═══════════════════════════════════════════════════════════════════════

    register("basilica", function (p) {
        var shapes = [];

        var nave = p.nave || {};
        var naveW = dflt(nave.w, 0.5);
        var naveH = dflt(nave.h, 0.6);
        var aisles = p.aisles || {};
        var aisleW = dflt(aisles.w, 0.25);
        var aisleH = dflt(aisles.h, 0.4);
        var apse = p.apse || {};
        var apseR = dflt(apse.r, 0.25);
        var colProps = p.cols || {};
        var colCount = clampInt(colProps.count, 4, 14);
        var clerestory = p.clerestory !== false;
        var material = p.material || "travertine";
        var roofMat = p.roof_material || "terracotta";
        var colMat = p.col_material || "marble";

        var totalW = naveW + aisleW * 2;
        var totalD = naveW * 2.5;  // proportional length
        var y = 0;

        // Foundation platform
        shapes.push(shapeBox([0, y + 0.02, 0], [totalW * 1.05, 0.04, totalD * 1.05], material));
        y += 0.04;

        // Nave walls
        var naveWallT = 0.03;
        // Left nave wall
        shapes.push(shapeBox([-naveW / 2 - naveWallT / 2, y + naveH / 2, 0], [naveWallT, naveH, totalD], material));
        // Right nave wall
        shapes.push(shapeBox([naveW / 2 + naveWallT / 2, y + naveH / 2, 0], [naveWallT, naveH, totalD], material));

        // Aisle outer walls
        var aisleOX = naveW / 2 + aisleW + naveWallT;
        shapes.push(shapeBox([-aisleOX, y + aisleH / 2, 0], [naveWallT, aisleH, totalD], material));
        shapes.push(shapeBox([aisleOX, y + aisleH / 2, 0], [naveWallT, aisleH, totalD], material));

        // Aisle roofs (flat)
        shapes.push(shapeBox([-(naveW / 2 + aisleW / 2 + naveWallT / 2), y + aisleH + 0.01, 0], [aisleW, 0.02, totalD * 1.01], roofMat));
        shapes.push(shapeBox([(naveW / 2 + aisleW / 2 + naveWallT / 2), y + aisleH + 0.01, 0], [aisleW, 0.02, totalD * 1.01], roofMat));

        // Internal colonnades separating nave from aisles
        var colH = aisleH * 0.85;
        var colR = colH / 16;
        var colSpacing = totalD / (colCount + 1);
        var colStartZ = -totalD / 2 + colSpacing;
        // Left colonnade
        shapes = shapes.concat(_colonnade(colCount, colSpacing, colH, colR, "ionic", -naveW / 2, colStartZ, y, colMat, "z"));
        // Right colonnade
        shapes = shapes.concat(_colonnade(colCount, colSpacing, colH, colR, "ionic", naveW / 2, colStartZ, y, colMat, "z"));

        // Clerestory windows (dark insets on upper nave wall)
        if (clerestory) {
            var winH = (naveH - aisleH) * 0.5;
            var winW = colSpacing * 0.4;
            var winY = y + aisleH + (naveH - aisleH) * 0.5;
            for (var i = 0; i < Math.min(colCount, 8); i++) {
                var wz = colStartZ + i * colSpacing;
                shapes = shapes.concat(_window(-naveW / 2 - 0.01, winY, wz, winW, winH, "arched"));
                shapes = shapes.concat(_window(naveW / 2 + 0.01, winY, wz, winW, winH, "arched"));
            }
        }

        // Apse (half-cylinder at the far end)
        shapes.push(shapeCyl([0, y + naveH * 0.4, totalD / 2 + apseR * 0.3], apseR, naveH * 0.8, material, null, 12));

        // Nave roof (gable)
        shapes = shapes.concat(_roof(naveW * 1.05, totalD * 1.02, 0.25, "gable", y + naveH, roofMat));

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #3: insula
    // ═══════════════════════════════════════════════════════════════════════

    register("insula", function (p) {
        var shapes = [];

        var stories = clampInt(p.stories, 2, 7);
        var ground = String(p.ground || "tabernae").toLowerCase();
        var w = dflt(p.w, 0.7);
        var d = dflt(p.d, 0.6);
        var balconies = p.balconies !== false;
        var courtyard = p.courtyard === true;
        var material = p.material || "brick";
        var roofMat = p.roof_material || "terracotta";
        var storyH = dflt(p.story_h, 0.14);

        var y = 0;
        var totalH = stories * storyH;
        var wallT = 0.03;

        if (courtyard) {
            // U-shaped with open courtyard in center
            var cW = w * 0.4;
            var cD = d * 0.4;

            // Back wall
            shapes.push(shapeBox([0, y + totalH / 2, -d / 2 + wallT / 2], [w, totalH, wallT], material));
            // Left wall
            shapes.push(shapeBox([-w / 2 + wallT / 2, y + totalH / 2, 0], [wallT, totalH, d], material));
            // Right wall
            shapes.push(shapeBox([w / 2 - wallT / 2, y + totalH / 2, 0], [wallT, totalH, d], material));
            // Front wall (two halves with gap)
            var fwW = (w - cW) / 2;
            shapes.push(shapeBox([-w / 2 + fwW / 2, y + totalH / 2, d / 2 - wallT / 2], [fwW, totalH, wallT], material));
            shapes.push(shapeBox([w / 2 - fwW / 2, y + totalH / 2, d / 2 - wallT / 2], [fwW, totalH, wallT], material));
        } else {
            // Solid block
            shapes.push(shapeBox([0, y + totalH / 2, 0], [w, totalH, d], material));
        }

        // Ground floor shops (dark openings)
        if (ground === "tabernae") {
            var shopW = 0.08;
            var shopH = storyH * 0.75;
            var nShops = clampInt(Math.floor(w / (shopW * 2)), 1, 8);
            var shopSpacing = w / (nShops + 1);
            for (var i = 0; i < nShops; i++) {
                var sx = -w / 2 + shopSpacing * (i + 1);
                shapes = shapes.concat(_window(sx, y + shopH / 2 + 0.01, d / 2 + 0.005, shopW, shopH, "arched"));
            }
        }

        // Windows on upper floors
        var nWin = clampInt(Math.floor(w / 0.12), 2, 8);
        var winSpacing = w / (nWin + 1);
        var winW = 0.04;
        var winH = storyH * 0.45;
        for (var floor = 1; floor < stories && floor < 6; floor++) {
            var floorY = y + floor * storyH + storyH * 0.5;
            for (var j = 0; j < nWin; j++) {
                var wx = -w / 2 + winSpacing * (j + 1);
                shapes = shapes.concat(_window(wx, floorY, d / 2 + 0.005, winW, winH, "arched"));
            }
        }

        // Balconies
        if (balconies) {
            var balD = 0.04;
            var balH = 0.008;
            for (var fl = 1; fl < stories && fl < 5; fl++) {
                var balY = y + fl * storyH;
                shapes.push(shapeBox([0, balY, d / 2 + balD / 2], [w * 0.9, balH, balD], material));
                // Railing
                shapes.push(shapeBox([0, balY + 0.02, d / 2 + balD], [w * 0.9, 0.025, 0.005], material));
            }
        }

        // Roof
        shapes = shapes.concat(_roof(w * 1.02, d * 1.02, 0.15, "gable", y + totalH, roofMat));

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #4: domus
    // ═══════════════════════════════════════════════════════════════════════

    register("domus", function (p) {
        var shapes = [];

        var fauces = p.fauces || {};
        var faucW = dflt(fauces.w, 0.08);
        var atr = p.atrium || {};
        var atrW = dflt(atr.w, 0.35);
        var atrD = dflt(atr.d, 0.3);
        var atrStyle = String(atr.style || "tuscan").toLowerCase();
        var tab = p.tablinum || {};
        var tabW = dflt(tab.w, 0.2);
        var tabD = dflt(tab.d, 0.15);
        var per = p.peristyle || {};
        var perW = dflt(per.w, 0.4);
        var perD = dflt(per.d, 0.35);
        var perCols = clampInt(per.cols, 4, 16);
        var wallH = dflt(p.wall_h, 0.25);
        var material = p.material || "stucco";
        var roofMat = p.roof_material || "terracotta";
        var colMat = p.col_material || "marble";

        var wallT = 0.025;
        var y = 0;

        // Total domus length: fauces + atrium + tablinum + peristyle
        var totalD = faucW + atrD + tabD + perD;
        var totalW = Math.max(atrW, perW) + wallT * 2;
        var zCursor = -totalD / 2;

        // Outer walls
        shapes.push(shapeBox([-totalW / 2, y + wallH / 2, 0], [wallT, wallH, totalD], material));
        shapes.push(shapeBox([totalW / 2, y + wallH / 2, 0], [wallT, wallH, totalD], material));
        shapes.push(shapeBox([0, y + wallH / 2, -totalD / 2], [totalW, wallH, wallT], material));
        shapes.push(shapeBox([0, y + wallH / 2, totalD / 2], [totalW, wallH, wallT], material));

        // Fauces (entrance corridor)
        var faucZ = zCursor + faucW / 2;
        // Door opening
        shapes = shapes.concat(_window(0, y + wallH * 0.35, -totalD / 2 - 0.005, faucW * 0.8, wallH * 0.6, "arched"));
        zCursor += faucW;

        // Atrium
        var atrZ = zCursor + atrD / 2;
        // Impluvium (water basin in center of atrium)
        var implW = atrW * 0.35;
        var implD = atrD * 0.35;
        shapes.push(shapeBox([0, y + 0.01, atrZ], [implW, 0.02, implD], "marble"));
        // Water surface
        shapes.push({ shape: "box", position: [0, y + 0.025, atrZ], size: [implW * 0.9, 0.005, implD * 0.9], color: "#2980b9", roughness: 0.08, metalness: 0.02 });

        // Atrium columns (if tetrastyle or corinthian)
        if (atrStyle === "tetrastyle" || atrStyle === "corinthian") {
            var aColR = wallH / 20;
            var aColH = wallH * 0.7;
            var acx = implW / 2 + 0.03;
            var acz = atrZ - implD / 2 - 0.03;
            shapes = shapes.concat(_colonnade(2, implD + 0.06, aColH, aColR, "ionic", -acx, acz, y, colMat, "z"));
            shapes = shapes.concat(_colonnade(2, implD + 0.06, aColH, aColR, "ionic", acx, acz, y, colMat, "z"));
        }

        // Compluvium (roof opening over atrium — just mark with edge beams)
        shapes.push(shapeBox([0, y + wallH, atrZ], [atrW * 0.5, 0.01, 0.015], roofMat));
        shapes.push(shapeBox([0, y + wallH, atrZ], [0.015, 0.01, atrD * 0.5], roofMat));
        zCursor += atrD;

        // Tablinum (reception room between atrium and peristyle)
        var tabZ = zCursor + tabD / 2;
        shapes.push(shapeBox([0, y + wallH / 2, tabZ - tabD / 2], [tabW, wallH, wallT], material));
        shapes.push(shapeBox([0, y + wallH / 2, tabZ + tabD / 2], [tabW, wallH, wallT], material));
        // Tablinum floor (slightly raised)
        shapes.push(shapeBox([0, y + 0.01, tabZ], [tabW, 0.02, tabD], "marble"));
        zCursor += tabD;

        // Peristyle garden with colonnade
        var perZ = zCursor + perD / 2;
        // Garden floor
        shapes.push(shapeBox([0, y + 0.005, perZ], [perW * 0.7, 0.01, perD * 0.7], "#3d5c32"));

        // Peristyle colonnade (rectangular arrangement)
        var pcSpacing;
        var pcFront = clampInt(Math.floor(perCols / 3), 2, 8);
        var pcSide = clampInt(Math.ceil(perCols / 3), 2, 8);
        var pcH = wallH * 0.75;
        var pcR = pcH / 18;
        var pcHalfW = perW * 0.35;
        var pcHalfD = perD * 0.35;

        // Front and back rows
        pcSpacing = (pcHalfW * 2) / Math.max(pcFront - 1, 1);
        shapes = shapes.concat(_colonnade(pcFront, pcSpacing, pcH, pcR, "corinthian", -pcHalfW, perZ - pcHalfD, y, colMat, "x"));
        shapes = shapes.concat(_colonnade(pcFront, pcSpacing, pcH, pcR, "corinthian", -pcHalfW, perZ + pcHalfD, y, colMat, "x"));
        // Side rows (excluding corners)
        if (pcSide > 2) {
            pcSpacing = (pcHalfD * 2) / Math.max(pcSide - 1, 1);
            var sStart = perZ - pcHalfD + pcSpacing;
            shapes = shapes.concat(_colonnade(pcSide - 2, pcSpacing, pcH, pcR, "corinthian", -pcHalfW, sStart, y, colMat, "z"));
            shapes = shapes.concat(_colonnade(pcSide - 2, pcSpacing, pcH, pcR, "corinthian", pcHalfW, sStart, y, colMat, "z"));
        }

        // Roof (covers the whole domus minus openings)
        shapes = shapes.concat(_roof(totalW, totalD, 0.12, "flat", y + wallH, roofMat));

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #5: amphitheater
    // ═══════════════════════════════════════════════════════════════════════

    register("amphitheater", function (p) {
        var shapes = [];

        var tiers = clampInt(p.tiers, 2, 5);
        var arena = p.arena || {};
        var arenaRX = dflt(arena.rx, 0.3);
        var arenaRY = dflt(arena.ry, 0.22);
        var arcades = clampInt(p.arcades, 4, 16);
        var h = dflt(p.h, 0.6);
        var material = p.material || "travertine";
        var seatMat = p.seat_material || "sandstone";

        var y = 0;
        var tierH = h / tiers;

        // Foundation/arena floor
        shapes.push(shapeBox([0, y + 0.01, 0], [arenaRX * 2 * 0.9, 0.02, arenaRY * 2 * 0.9], "sandstone"));

        // Seating tiers (concentric oval rings of increasing size and height)
        for (var t = 0; t < tiers; t++) {
            var rx = arenaRX + (t + 1) * 0.06;
            var ry = arenaRY + (t + 1) * 0.06;
            var innerRX = arenaRX + t * 0.06;
            var innerRY = arenaRY + t * 0.06;
            var tierY = y + t * tierH;

            // Approximate oval ring as 8 box segments
            var nSegs = 8;
            for (var s = 0; s < nSegs; s++) {
                var a = (s / nSegs) * Math.PI * 2;
                var aN = ((s + 1) / nSegs) * Math.PI * 2;
                var midA = (a + aN) / 2;
                var cx = Math.cos(midA) * (rx + innerRX) / 2;
                var cz = Math.sin(midA) * (ry + innerRY) / 2;
                var segW = (rx - innerRX) * 1.3;
                var segD = ((rx + ry) / 2) * Math.PI / nSegs * 1.1;
                shapes.push(shapeBox([cx, tierY + tierH / 2, cz], [segW, tierH, segD], seatMat));
            }
        }

        // External facade (outer wall with arched openings)
        var outerRX = arenaRX + (tiers + 0.5) * 0.06;
        var outerRY = arenaRY + (tiers + 0.5) * 0.06;
        var wallH = h;
        var wallT = 0.025;

        // External wall segments with pilasters
        var nFacade = clampInt(arcades, 4, 16);
        for (var f = 0; f < nFacade; f++) {
            var fa = (f / nFacade) * Math.PI * 2;
            var fx = Math.cos(fa) * outerRX;
            var fz = Math.sin(fa) * outerRY;
            // Pilaster
            shapes.push(shapeBox([fx, y + wallH / 2, fz], [wallT * 1.5, wallH, wallT * 2], material));
        }

        // Continuous outer wall (simplified as 4 curved segments using boxes)
        var nWallSegs = Math.min(16, nFacade * 2);
        for (var ws = 0; ws < nWallSegs; ws++) {
            var wa = (ws / nWallSegs) * Math.PI * 2;
            var waN = ((ws + 1) / nWallSegs) * Math.PI * 2;
            var wmA = (wa + waN) / 2;
            var wwx = Math.cos(wmA) * (outerRX + wallT / 2);
            var wwz = Math.sin(wmA) * (outerRY + wallT / 2);
            var wSegLen = ((outerRX + outerRY) / 2) * Math.PI / nWallSegs * 1.05;
            shapes.push(shapeBox([wwx, y + wallH / 2, wwz], [wallT, wallH, wSegLen], material));
        }

        // Top cornice
        shapes.push(shapeCyl([0, y + h + 0.01, 0], (outerRX + outerRY) / 2 + wallT, 0.02, material, null, 24));

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #6: thermae (bath complex)
    // ═══════════════════════════════════════════════════════════════════════

    register("thermae", function (p) {
        var shapes = [];

        var frig = p.frigidarium || {};
        var frigW = dflt(frig.w, 0.35);
        var frigD = dflt(frig.d, 0.3);
        var tep = p.tepidarium || {};
        var tepW = dflt(tep.w, 0.25);
        var tepD = dflt(tep.d, 0.25);
        var cald = p.caldarium || {};
        var caldW = dflt(cald.w, 0.3);
        var caldD = dflt(cald.d, 0.3);
        var caldApse = cald.apse !== false;
        var material = p.material || "brick";
        var marbleMat = p.marble_material || "marble";
        var roofMat = p.roof_material || "lead";

        var wallH = dflt(p.wall_h, 0.35);
        var wallT = 0.025;
        var y = 0;

        // Total building dimensions
        var totalD = frigD + tepD + caldD + wallT * 4;
        var totalW = Math.max(frigW, tepW, caldW) + wallT * 2;
        var zCursor = -totalD / 2;

        // Foundation
        shapes.push(shapeBox([0, y + 0.015, 0], [totalW * 1.05, 0.03, totalD * 1.05], material));
        y += 0.03;

        // Outer walls
        shapes.push(shapeBox([-totalW / 2, y + wallH / 2, 0], [wallT, wallH, totalD], material));
        shapes.push(shapeBox([totalW / 2, y + wallH / 2, 0], [wallT, wallH, totalD], material));
        shapes.push(shapeBox([0, y + wallH / 2, -totalD / 2], [totalW, wallH, wallT], material));

        // Frigidarium (cold room — largest, often vaulted)
        var frigZ = zCursor + frigD / 2 + wallT;
        // Pool
        shapes.push(shapeBox([0, y + 0.01, frigZ], [frigW * 0.6, 0.02, frigD * 0.6], marbleMat));
        shapes.push({ shape: "box", position: [0, y + 0.025, frigZ], size: [frigW * 0.55, 0.005, frigD * 0.55], color: "#2980b9", roughness: 0.08, metalness: 0.02 });
        // Columns flanking pool
        var frigColH = wallH * 0.7;
        var frigColR = frigColH / 18;
        shapes = shapes.concat(_colonnade(3, frigW * 0.25, frigColH, frigColR, "corinthian", -frigW * 0.35, frigZ - frigD * 0.2, y, marbleMat, "z"));
        shapes = shapes.concat(_colonnade(3, frigW * 0.25, frigColH, frigColR, "corinthian", frigW * 0.35, frigZ - frigD * 0.2, y, marbleMat, "z"));

        // Dividing wall
        zCursor += frigD + wallT;
        shapes.push(shapeBox([0, y + wallH / 2, zCursor], [totalW * 0.9, wallH, wallT], material));

        // Tepidarium (warm room — medium)
        var tepZ = zCursor + tepD / 2 + wallT;
        shapes.push(shapeBox([0, y + 0.01, tepZ], [tepW * 0.5, 0.02, tepD * 0.5], marbleMat));

        // Dividing wall
        zCursor += tepD + wallT;
        shapes.push(shapeBox([0, y + wallH / 2, zCursor], [totalW * 0.9, wallH, wallT], material));

        // Caldarium (hot room — with optional apse)
        var caldZ = zCursor + caldD / 2 + wallT;
        shapes.push(shapeBox([0, y + 0.01, caldZ], [caldW * 0.4, 0.02, caldD * 0.4], marbleMat));
        shapes.push({ shape: "box", position: [0, y + 0.025, caldZ], size: [caldW * 0.35, 0.005, caldD * 0.35], color: "#b0422a", roughness: 0.3, metalness: 0.01 });

        if (caldApse) {
            // Semicircular apse at the far end
            var apseR = caldW * 0.4;
            shapes.push(shapeCyl([0, y + wallH * 0.4, totalD / 2 - wallT], apseR, wallH * 0.8, material, null, 12));
        }

        // Back wall
        shapes.push(shapeBox([0, y + wallH / 2, totalD / 2], [totalW, wallH, wallT], material));

        // Roof: barrel vault on frigidarium, flat on others
        shapes = shapes.concat(_roof(frigW, frigD, 0.25, "barrel", y + wallH, roofMat));
        shapes = shapes.concat(_roof(tepW, tepD, 0, "flat", y + wallH, roofMat));
        shapes = shapes.concat(_roof(caldW, caldD, 0, "flat", y + wallH, roofMat));

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #7: triumphal_arch
    // ═══════════════════════════════════════════════════════════════════════

    register("triumphal_arch", function (p) {
        var shapes = [];

        var spans = clampInt(p.spans, 1, 3);
        var h = dflt(p.h, 0.5);
        var w = dflt(p.w, 0.4);
        var attic = p.attic || {};
        var atticH = dflt(attic.h, h * 0.2);
        var colOpts = p.cols || {};
        var order = String(colOpts.order || "corinthian").toLowerCase();
        var material = p.material || "marble";

        var y = 0;
        var pierW = 0.04;
        var archH = h - atticH;
        var pierH = archH * 0.65;
        var archCurveH = archH - pierH;
        var depth = dflt(p.depth, 0.12);

        if (spans === 1) {
            var spanW = w - pierW * 2;

            // Two piers
            shapes.push(shapeBox([-w / 2 + pierW / 2, y + pierH / 2, 0], [pierW, pierH, depth], material));
            shapes.push(shapeBox([w / 2 - pierW / 2, y + pierH / 2, 0], [pierW, pierH, depth], material));

            // Arch barrel
            shapes.push(shapeBox([0, y + pierH + archCurveH / 2, 0], [spanW, archCurveH * 0.4, depth], material));
            shapes.push(shapeBox([0, y + pierH, 0], [spanW * 0.85, archCurveH * 0.25, depth * 0.9], material));

            // Columns flanking (decorative)
            var colH = pierH * 0.9;
            var colR = colH / 20;
            shapes = shapes.concat(_colonnade(1, 0, colH, colR, order, -w / 2 - colR * 2, 0, y, material, "x"));
            shapes = shapes.concat(_colonnade(1, 0, colH, colR, order, w / 2 + colR * 2, 0, y, material, "x"));
        } else {
            // Triple-span
            var mainSpanW = w * 0.4;
            var sideSpanW = w * 0.2;
            var allPierW = pierW * 4;
            var actualW = mainSpanW + sideSpanW * 2 + allPierW;

            var cx = -actualW / 2;
            var pierPositions = [];

            // Build from left: pier, side span, pier, main span, pier, side span, pier
            var segments = [pierW, sideSpanW, pierW, mainSpanW, pierW, sideSpanW, pierW];
            var isPier = true;
            for (var i = 0; i < segments.length; i++) {
                var segW = segments[i];
                if (isPier) {
                    shapes.push(shapeBox([cx + segW / 2, y + pierH / 2, 0], [segW, pierH, depth], material));
                    pierPositions.push(cx + segW / 2);
                } else {
                    // Arch opening
                    var openingH = (segW === mainSpanW) ? archCurveH : archCurveH * 0.7;
                    shapes.push(shapeBox([cx + segW / 2, y + pierH + openingH / 2, 0], [segW, openingH * 0.4, depth], material));
                }
                cx += segW;
                isPier = !isPier;
            }

            // Decorative columns at piers
            var colH2 = pierH * 0.9;
            var colR2 = colH2 / 20;
            for (var pi = 0; pi < pierPositions.length; pi++) {
                shapes = shapes.concat(_colonnade(1, 0, colH2, colR2, order, pierPositions[pi], depth / 2 + colR2 * 2, y, material, "x"));
                shapes = shapes.concat(_colonnade(1, 0, colH2, colR2, order, pierPositions[pi], -depth / 2 - colR2 * 2, y, material, "x"));
            }
        }

        // Entablature
        shapes.push(shapeBox([0, y + archH - 0.01, 0], [w * 1.05, 0.02, depth * 1.1], material));

        // Attic (inscription panel)
        shapes.push(shapeBox([0, y + archH + atticH / 2, 0], [w * 1.02, atticH, depth * 0.95], material));

        // Top cornice
        shapes.push(shapeBox([0, y + h + 0.005, 0], [w * 1.08, 0.01, depth * 1.15], material));

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #8: aqueduct
    // ═══════════════════════════════════════════════════════════════════════

    register("aqueduct", function (p) {
        var shapes = [];

        var spans = clampInt(p.spans, 2, 12);
        var spanW = dflt(p.span_w, 0.1);
        var pierW = dflt(p.pier_w, 0.04);
        var tierCount = clampInt(p.tiers, 1, 3);
        var h = dflt(p.h, 0.5);
        var material = p.material || "tufa";
        var depth = dflt(p.depth, 0.05);

        var tierH = h / tierCount;
        var totalW = spans * spanW + (spans + 1) * pierW;
        var y = 0;

        for (var tier = 0; tier < tierCount; tier++) {
            var tierY = y + tier * tierH;
            var tierSpanW = spanW;
            var tierPierW = pierW;

            // Slight taper on upper tiers
            if (tier > 0) {
                tierSpanW *= 0.85;
                tierPierW *= 0.9;
            }

            var tierTotalW = spans * tierSpanW + (spans + 1) * tierPierW;

            // Piers
            var pierH = tierH * 0.75;
            var startX = -tierTotalW / 2 + tierPierW / 2;
            for (var i = 0; i <= spans; i++) {
                var px = startX + i * (tierSpanW + tierPierW);
                shapes.push(shapeBox([px, tierY + pierH / 2, 0], [tierPierW, pierH, depth], material));
            }

            // Arches between piers
            var archH = tierH - pierH;
            for (var j = 0; j < spans; j++) {
                var ax = startX + tierPierW / 2 + j * (tierSpanW + tierPierW) + tierSpanW / 2;
                shapes.push(shapeBox([ax, tierY + pierH + archH / 2, 0], [tierSpanW * 0.9, archH * 0.5, depth * 0.9], material));
            }

            // Horizontal entablature on each tier
            shapes.push(shapeBox([0, tierY + tierH - 0.005, 0], [tierTotalW, 0.01, depth * 1.05], material));
        }

        // Water channel on top
        var channelW = totalW * 0.3;
        shapes.push(shapeBox([0, y + h + 0.005, 0], [totalW * 1.02, 0.01, depth * 1.1], material));
        // Channel walls
        shapes.push(shapeBox([0, y + h + 0.02, -depth * 0.4], [totalW * 1.02, 0.02, 0.005], material));
        shapes.push(shapeBox([0, y + h + 0.02, depth * 0.4], [totalW * 1.02, 0.02, 0.005], material));

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #9: taberna (shop)
    // ═══════════════════════════════════════════════════════════════════════

    register("taberna", function (p) {
        var shapes = [];

        var w = dflt(p.w, 0.25);
        var d = dflt(p.d, 0.2);
        var h = dflt(p.h, 0.2);
        var counter = p.counter !== false;
        var mezzanine = p.mezzanine === true;
        var material = p.material || "brick";
        var roofMat = p.roof_material || "terracotta";
        var woodMat = "wood";

        var y = 0;
        var wallT = 0.02;

        // Back wall
        shapes.push(shapeBox([0, y + h / 2, -d / 2 + wallT / 2], [w, h, wallT], material));
        // Side walls
        shapes.push(shapeBox([-w / 2 + wallT / 2, y + h / 2, 0], [wallT, h, d], material));
        shapes.push(shapeBox([w / 2 - wallT / 2, y + h / 2, 0], [wallT, h, d], material));

        // Front: mostly open (wide shop opening), with thin upper wall
        var openH = h * 0.65;
        var upperH = h - openH;
        shapes.push(shapeBox([0, y + openH + upperH / 2, d / 2 - wallT / 2], [w, upperH, wallT], material));

        // Counter (L-shaped masonry counter at front)
        if (counter) {
            var counterH = h * 0.3;
            var counterD = d * 0.25;
            shapes.push(shapeBox([0, y + counterH / 2, d / 2 - counterD / 2 - wallT], [w * 0.8, counterH, counterD], material));
            // Dolia (storage jars embedded in counter — small cylinders)
            var nJars = clampInt(Math.floor(w / 0.06), 1, 4);
            var jarSpacing = (w * 0.6) / (nJars + 1);
            for (var i = 0; i < nJars; i++) {
                var jx = -w * 0.3 + jarSpacing * (i + 1);
                shapes.push(shapeCyl([jx, y + counterH + 0.015, d / 2 - counterD / 2 - wallT], 0.015, 0.03, "terracotta"));
            }
        }

        // Mezzanine (half-floor above shop for sleeping)
        if (mezzanine) {
            var mezH = h * 0.45;
            // Floor
            shapes.push(shapeBox([0, y + mezH, -d * 0.15], [w * 0.9, 0.01, d * 0.55], woodMat));
            // Ladder (thin vertical + horizontal rungs)
            shapes.push(shapeBox([w * 0.35, y + mezH / 2, d * 0.2], [0.008, mezH, 0.008], woodMat));
        }

        // Awning over entrance
        shapes.push(shapeBox([0, y + openH + 0.005, d / 2 + 0.02], [w * 1.1, 0.005, 0.04], "red"));

        // Roof
        shapes = shapes.concat(_roof(w * 1.02, d * 1.02, 0, "flat", y + h, roofMat));

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #10: warehouse (horreum)
    // ═══════════════════════════════════════════════════════════════════════

    register("warehouse", function (p) {
        var shapes = [];

        var w = dflt(p.w, 0.7);
        var d = dflt(p.d, 0.5);
        var h = dflt(p.h, 0.35);
        var bays = clampInt(p.bays, 2, 8);
        var buttresses = p.buttresses !== false;
        var material = p.material || "concrete";
        var roofMat = p.roof_material || "lead";

        var y = 0;
        var wallT = 0.03;

        // Foundation
        shapes.push(shapeBox([0, y + 0.015, 0], [w * 1.05, 0.03, d * 1.05], "basalt"));
        y += 0.03;

        // Main block (walls)
        shapes.push(shapeBox([0, y + h / 2, -d / 2 + wallT / 2], [w, h, wallT], material)); // back
        shapes.push(shapeBox([0, y + h / 2, d / 2 - wallT / 2], [w, h, wallT], material)); // front
        shapes.push(shapeBox([-w / 2 + wallT / 2, y + h / 2, 0], [wallT, h, d], material)); // left
        shapes.push(shapeBox([w / 2 - wallT / 2, y + h / 2, 0], [wallT, h, d], material)); // right

        // Internal dividing walls (bays)
        var bayW = w / bays;
        for (var i = 1; i < bays; i++) {
            var bx = -w / 2 + i * bayW;
            shapes.push(shapeBox([bx, y + h / 2, 0], [wallT * 0.6, h * 0.9, d * 0.95], material));
        }

        // Loading doors on front
        var doorW = bayW * 0.5;
        var doorH = h * 0.6;
        for (var j = 0; j < Math.min(bays, 6); j++) {
            var dx = -w / 2 + (j + 0.5) * bayW;
            shapes = shapes.concat(_window(dx, y + doorH / 2, d / 2 + 0.005, doorW, doorH, "arched"));
        }

        // Small windows on upper walls
        var winH = h * 0.2;
        var winW = bayW * 0.25;
        for (var k = 0; k < Math.min(bays, 6); k++) {
            var wx = -w / 2 + (k + 0.5) * bayW;
            shapes = shapes.concat(_window(wx, y + h * 0.75, d / 2 + 0.005, winW, winH, "arched"));
        }

        // External buttresses
        if (buttresses) {
            var nButt = clampInt(Math.floor(d / 0.15), 2, 6);
            var buttSpacing = d / (nButt + 1);
            var buttW = 0.025;
            var buttD = 0.04;
            for (var b = 0; b < nButt; b++) {
                var bz = -d / 2 + buttSpacing * (b + 1);
                // Left side
                shapes.push(shapeBox([-w / 2 - buttD / 2, y + h * 0.4, bz], [buttD, h * 0.8, buttW], material));
                // Right side
                shapes.push(shapeBox([w / 2 + buttD / 2, y + h * 0.4, bz], [buttD, h * 0.8, buttW], material));
            }
        }

        // Roof
        shapes = shapes.concat(_roof(w * 1.02, d * 1.02, 0.1, "gable", y + h, roofMat));

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #11: monument
    // ═══════════════════════════════════════════════════════════════════════

    register("monument", function (p) {
        var shapes = [];

        var mType = String(p.type || "column").toLowerCase();
        var ped = p.pedestal || {};
        var pedW = dflt(ped.w, 0.15);
        var pedH = dflt(ped.h, 0.1);
        var h = dflt(p.h, 0.4);
        var material = p.material || "marble";
        var pedMat = p.pedestal_material || material;

        var y = 0;

        // Stepped base
        shapes = shapes.concat(_podium(pedW * 1.5, pedW * 1.5, pedH * 0.3, 3, y, pedMat));
        y += pedH * 0.3;

        // Pedestal
        shapes.push(shapeBox([0, y + pedH / 2, 0], [pedW, pedH, pedW], pedMat));
        y += pedH;

        // Pedestal cornice
        shapes.push(shapeBox([0, y + 0.005, 0], [pedW * 1.1, 0.01, pedW * 1.1], pedMat));
        y += 0.01;

        if (mType === "column") {
            // Commemorative column (like Trajan's Column)
            var colR = pedW * 0.25;
            // Base torus
            shapes.push(shapeCyl([0, y + 0.01, 0], colR * 1.4, 0.02, material));
            y += 0.02;
            // Shaft
            shapes.push(shapeCyl([0, y + h / 2, 0], colR, h, material));
            y += h;
            // Capital
            shapes.push(shapeCylTapered([0, y + 0.02, 0], colR, colR * 1.5, 0.04, material, 10));
            y += 0.04;
            // Platform on top
            shapes.push(shapeCyl([0, y + 0.005, 0], colR * 1.8, 0.01, material));
            y += 0.01;
            // Figure on top (sphere for head + cylinder for body)
            shapes.push(shapeCyl([0, y + 0.03, 0], colR * 0.5, 0.06, "bronze"));
            shapes.push(shapeSphere([0, y + 0.07, 0], colR * 0.35, "bronze"));

        } else if (mType === "obelisk") {
            // Tall tapered rectangular shaft
            var obW = pedW * 0.35;
            shapes.push(shapeCylTapered([0, y + h / 2, 0], obW, obW * 0.3, h, "granite", 4));
            y += h;
            // Pyramidion (tip)
            shapes.push(shapeCone([0, y + h * 0.06, 0], obW * 0.35, h * 0.12, "gilded"));

        } else {
            // Statue (default)
            // Body (cylinder)
            var bodyH = h * 0.65;
            var bodyR = pedW * 0.2;
            shapes.push(shapeCyl([0, y + bodyH / 2, 0], bodyR, bodyH, "bronze"));
            y += bodyH;
            // Head
            shapes.push(shapeSphere([0, y + bodyR * 0.8, 0], bodyR * 0.7, "bronze"));
            // Arm (extended)
            shapes.push(shapeCyl([bodyR * 1.2, y - bodyH * 0.15, 0], bodyR * 0.2, bodyH * 0.35, "bronze"));
        }

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #12: wall_gate
    // ═══════════════════════════════════════════════════════════════════════

    register("wall_gate", function (p) {
        var shapes = [];

        var gateW = dflt(p.gate_w, 0.15);
        var gateH = dflt(p.gate_h, 0.25);
        var wallH = dflt(p.wall_h, 0.3);
        var towers = p.towers !== false;
        var towerH = dflt(p.tower_h, wallH * 1.4);
        var material = p.material || "tufa";
        var wallLength = dflt(p.wall_length, 0.6);
        var wallT = 0.04;

        var y = 0;

        // Main wall segments (left and right of gate)
        var segW = (wallLength - gateW) / 2;
        // Left wall segment
        shapes.push(shapeBox([-wallLength / 2 + segW / 2, y + wallH / 2, 0], [segW, wallH, wallT], material));
        // Right wall segment
        shapes.push(shapeBox([wallLength / 2 - segW / 2, y + wallH / 2, 0], [segW, wallH, wallT], material));

        // Gate arch
        var archPierW = 0.03;
        var archOpenH = gateH * 0.7;
        // Gate piers
        shapes.push(shapeBox([-gateW / 2 - archPierW / 2, y + archOpenH / 2, 0], [archPierW, archOpenH, wallT * 1.2], material));
        shapes.push(shapeBox([gateW / 2 + archPierW / 2, y + archOpenH / 2, 0], [archPierW, archOpenH, wallT * 1.2], material));
        // Arch top
        shapes.push(shapeBox([0, y + archOpenH + (gateH - archOpenH) / 2, 0], [gateW + archPierW * 2, gateH - archOpenH, wallT * 1.2], material));
        // Gate opening (dark)
        shapes = shapes.concat(_window(0, y + archOpenH / 2, 0, gateW, archOpenH, "arched"));

        // Walkway on top
        shapes.push(shapeBox([0, y + wallH + 0.005, 0], [wallLength, 0.01, wallT * 1.5], material));

        // Battlements (merlons)
        var merlonW = 0.025;
        var merlonH = wallH * 0.12;
        var nMerlons = clampInt(Math.floor(wallLength / (merlonW * 2.5)), 3, 16);
        var merlonSpacing = wallLength / nMerlons;
        for (var i = 0; i < nMerlons; i++) {
            var mx = -wallLength / 2 + merlonSpacing * (i + 0.5);
            shapes.push(shapeBox([mx, y + wallH + 0.01 + merlonH / 2, 0], [merlonW, merlonH, wallT * 1.2], material));
        }

        // Flanking towers
        if (towers) {
            var towerW = gateW * 0.5;
            var towerD = wallT * 2.5;

            for (var side = -1; side <= 1; side += 2) {
                var tx = side * (wallLength / 2 + towerW / 2 - 0.01);
                // Tower body
                shapes.push(shapeBox([tx, y + towerH / 2, 0], [towerW, towerH, towerD], material));
                // Tower top platform
                shapes.push(shapeBox([tx, y + towerH + 0.005, 0], [towerW * 1.1, 0.01, towerD * 1.1], material));
                // Tower battlements
                var tMerlons = 4;
                var tMSpacing = towerW / tMerlons;
                for (var m = 0; m < tMerlons; m++) {
                    var tmx = tx - towerW / 2 + tMSpacing * (m + 0.5);
                    shapes.push(shapeBox([tmx, y + towerH + 0.01 + merlonH / 2, 0], [merlonW, merlonH, towerD * 1.05], material));
                }
                // Arrow slits
                var nSlits = 3;
                var slitSpacing = towerH / (nSlits + 1);
                for (var sl = 0; sl < nSlits; sl++) {
                    var slY = y + slitSpacing * (sl + 1);
                    shapes = shapes.concat(_window(tx, slY, towerD / 2 + 0.005, 0.01, 0.04, "arched"));
                }
            }
        }

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Grammar #13: circus
    // ═══════════════════════════════════════════════════════════════════════

    register("circus", function (p) {
        var shapes = [];

        var length = dflt(p.length, 1.5);
        var w = dflt(p.w, 0.4);
        var spina = p.spina !== false;
        var tiers = clampInt(p.tiers, 1, 4);
        var material = p.material || "travertine";
        var seatMat = p.seat_material || "sandstone";

        var y = 0;
        var trackW = w * 0.6;
        var standW = (w - trackW) / 2;
        var tierH = 0.06;

        // Track surface
        shapes.push(shapeBox([0, y + 0.005, 0], [length, 0.01, trackW], "sandstone"));
        y += 0.01;

        // Spina (central barrier)
        if (spina) {
            var spinaH = 0.04;
            var spinaW = length * 0.65;
            shapes.push(shapeBox([0, y + spinaH / 2, 0], [spinaW, spinaH, 0.02], material));
            // Meta (turning posts) at each end of spina
            shapes.push(shapeCone([-spinaW / 2, y + spinaH + 0.02, 0], 0.015, 0.04, "bronze"));
            shapes.push(shapeCone([spinaW / 2, y + spinaH + 0.02, 0], 0.015, 0.04, "bronze"));
            // Obelisk in center of spina
            shapes.push(shapeCylTapered([0, y + spinaH + 0.04, 0], 0.01, 0.005, 0.08, "granite", 4));
        }

        // Seating on both sides
        for (var side = -1; side <= 1; side += 2) {
            var baseZ = side * (trackW / 2 + standW / 2);
            for (var t = 0; t < tiers; t++) {
                var tierZ = baseZ + side * t * standW / tiers * 0.3;
                var tierY = y + t * tierH;
                shapes.push(shapeBox([0, tierY + tierH / 2, tierZ], [length * 0.98, tierH, standW / tiers], seatMat));
            }
            // Back wall behind top tier
            var backZ = baseZ + side * standW * 0.4;
            var backH = tiers * tierH + 0.04;
            shapes.push(shapeBox([0, y + backH / 2, backZ], [length, backH, 0.02], material));
        }

        // Curved ends (carceres at one end, semicircle at other)
        // Starting gates (carceres) — simplified as a wall with openings
        var carcX = -length / 2;
        shapes.push(shapeBox([carcX - 0.02, y + 0.06, 0], [0.03, 0.12, w * 0.8], material));
        // Gate openings
        var nGates = clampInt(Math.floor(trackW / 0.08), 2, 8);
        var gateSpacing = trackW / (nGates + 1);
        for (var g = 0; g < nGates; g++) {
            var gz = -trackW / 2 + gateSpacing * (g + 1);
            shapes = shapes.concat(_window(carcX - 0.02, y + 0.04, gz, 0.03, 0.06, "arched"));
        }

        // Semicircular end (sphendone)
        var endX = length / 2;
        var endR = w * 0.35;
        var nEndSegs = 6;
        for (var e = 0; e < nEndSegs; e++) {
            var ea = (e / nEndSegs) * Math.PI - Math.PI / 2;
            var ean = ((e + 1) / nEndSegs) * Math.PI - Math.PI / 2;
            var ema = (ea + ean) / 2;
            var ex = endX + Math.cos(ema) * endR * 0.3;
            var ez = Math.sin(ema) * endR;
            shapes.push(shapeBox([ex, y + tiers * tierH / 2, ez], [0.025, tiers * tierH, endR * Math.PI / nEndSegs], seatMat));
        }

        return shapes;
    });

    // ═══════════════════════════════════════════════════════════════════════
    // Public API
    // ═══════════════════════════════════════════════════════════════════════

    global.EternalCities.GrammarEngine = {
        _registry: _registry,
        _materials: {},  // populated by loadMaterials or inline

        register: register,
        expand: expand,

        /** Resolve a material name to {color, roughness, metalness}. */
        getMaterial: function (name) {
            return mat(name);
        },

        /**
         * Load materials from a fetched palette object (e.g. data/material_palette.json).
         * Accepts either the old flat format {"marble": "#F0F0F0"} or the new
         * PBR format {"marble": {"color": "#F0F0F0", "roughness": 0.25, "metalness": 0.02}}.
         */
        loadMaterials: function (paletteObj) {
            if (!paletteObj || typeof paletteObj !== "object") return;
            var self = this;
            Object.keys(paletteObj).forEach(function (key) {
                var v = paletteObj[key];
                if (typeof v === "string") {
                    // Old flat format: just a hex color
                    self._materials[key] = { color: v, roughness: 0.75, metalness: 0.01 };
                } else if (v && typeof v === "object" && v.color) {
                    self._materials[key] = {
                        color: v.color || v.hex || "#c2b280",
                        roughness: v.roughness != null ? v.roughness : 0.75,
                        metalness: v.metalness != null ? v.metalness : 0.01,
                    };
                }
            });
            // Merge into built-in MATERIALS so mat() picks them up
            Object.keys(self._materials).forEach(function (key) {
                MATERIALS[key] = self._materials[key];
            });
        },

        /**
         * Convert an array of dense-format shapes to the dict format the renderer expects.
         * Input:  [["b",[0,.5,0],[.8,1,.8],"travertine"], ["c",[0,0,0],0.05,0.8,"marble"], ...]
         * Output: [{shape:"box",position:[0,.5,0],size:[.8,1,.8],color:"#f5ead6",roughness:0.65,...}, ...]
         */
        expandDenseShapes: function (denseArray) {
            if (!Array.isArray(denseArray)) return [];
            var result = [];
            for (var i = 0; i < denseArray.length; i++) {
                var expanded = expandOneDense(denseArray[i]);
                if (expanded) result.push(expanded);
            }
            return result;
        },

        /**
         * Expand a grammar spec into a renderer-compatible procedural component.
         * Returns: { type: "procedural", stack_role: "structural", parts: [...] }
         * or null if the grammar is unknown.
         */
        expandToComponent: function (grammar, params) {
            var parts = expand(grammar, params);
            if (!parts || parts.length === 0) return null;
            return {
                type: "procedural",
                stack_role: "structural",
                stack_priority: 0,
                parts: parts,
            };
        },

        /**
         * List all registered grammar names.
         */
        listGrammars: function () {
            return Object.keys(_registry);
        },

        /** Sub-grammar access for external use. */
        sub: {
            colonnade: _colonnade,
            podium: _podium,
            pediment: _pediment,
            arcade: _arcade,
            roof: _roof,
            wall: _wall,
            window: _window,
        },
    };

    // ═══════════════════════════════════════════════════════════════════════
    // Auto-load material palette if fetch is available
    // ═══════════════════════════════════════════════════════════════════════

    if (typeof fetch === "function") {
        fetch("data/material_palette.json")
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (data) global.EternalCities.GrammarEngine.loadMaterials(data);
            })
            .catch(function () { /* palette load is best-effort */ });
    }

})(typeof window !== "undefined" ? window : globalThis);
