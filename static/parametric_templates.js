/**
 * Parametric templates — expand spec.template { id, params } into spec.components[].
 * Scaling matches agents/golden_specs.get_golden_example (footprint vs ref_w/ref_d).
 *
 * - id "open": culture-agnostic — params.components is a full component list (named + procedural).
 *   Optional ref_w + ref_d apply footprint scaling; omit both to use dimensions as-is.
 * - Other ids: optional shortcuts (mostly Greco-Roman typology labels); same renderer pipeline.
 *
 * Loaded before renderer3d.js; exposes expandParametricTemplate on window.
 */
(function (global) {
    "use strict";

    const SCALAR_KEYS = new Set(["height", "radius", "width", "depth", "thickness", "storyHeight"]);

    function footprintScale(tileW, tileD, refW, refD) {
        return (tileW / refW + tileD / refD) / 2;
    }

    function clampInt(n, lo, hi) {
        const x = Math.round(Number(n));
        if (!Number.isFinite(x)) return lo;
        return Math.max(lo, Math.min(hi, x));
    }

    function mergeParams(defaults, params) {
        const p = params && typeof params === "object" ? params : {};
        return { ...defaults, ...p };
    }

    function scaleComponents(comps, scale) {
        return comps.map((c) => {
            const o = { ...c };
            for (const k of SCALAR_KEYS) {
                if (o[k] != null && typeof o[k] === "number") {
                    o[k] = Math.round(o[k] * scale * 10000) / 10000;
                }
            }
            return o;
        });
    }

    function colonnadeStyle(params, fallback) {
        const s = String(params.style || fallback || "ionic").toLowerCase();
        if (s === "doric" || s === "ionic" || s === "corinthian") return s;
        return "ionic";
    }

    function hexColor(v, fallback) {
        if (typeof v === "string" && /^#[0-9A-Fa-f]{6}$/.test(v)) return v;
        return fallback;
    }

    // ─── Per-type builders (ref_w/ref_d match golden_specs.py) ───

    function temple(params, tileW, tileD) {
        const refW = 2.7;
        const refD = 1.8;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        // Italic/Roman temples: tall podium, FRONT colonnade only, solid flank walls.
        // Greek peripteral (columns on all sides) is explicit opt-in via params.peripteral.
        const steps = clampInt(p.podium_steps != null ? p.podium_steps : 5, 1, 14);
        const columns = clampInt(p.columns != null ? p.columns : 6, 4, 32);
        const style = colonnadeStyle(p, "ionic");
        const peripteral = p.peripteral === true; // default FALSE for Roman temples
        const base = [
            { type: "podium", steps, height: 0.18, color: hexColor(p.podium_color, "#F5E6C8") },
            {
                type: "colonnade",
                columns,
                style,
                height: 0.42,
                color: hexColor(p.colonnade_color, "#E8E0D0"),
                radius: 0.022,
                peripteral,
            },
            {
                type: "cella",
                height: 0.4,
                width: 1.5,
                depth: 1.05,
                color: hexColor(p.cella_color, "#C8B070"),
            },
            { type: "pediment", height: 0.09, color: hexColor(p.pediment_color, "#C45A3C") },
            {
                type: "pilasters",
                count: clampInt(p.pilaster_count != null ? p.pilaster_count : 4, 0, 12),
                height: 0.4,
                color: hexColor(p.pilaster_color, "#C8B070"),
                placement: "sides",
            },
            { type: "door", width: 0.16, height: 0.3, color: hexColor(p.door_color, "#6B4226") },
        ];
        const scaled = scaleComponents(base, scale);
        return scaled;
    }

    function basilica(params, tileW, tileD) {
        const refW = 3.6;
        const refD = 1.8;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            { type: "podium", steps: clampInt(p.podium_steps, 3, 10), height: 0.08, color: hexColor(p.podium_color, "#F5E6C8") },
            {
                type: "block",
                stories: clampInt(p.stories != null ? p.stories : 2, 1, 4),
                storyHeight: 0.32,
                color: hexColor(p.block_color, "#F5E6C8"),
                windows: clampInt(p.windows != null ? p.windows : 8, 1, 20),
                windowColor: hexColor(p.window_color, "#1A1008"),
            },
            { type: "tiled_roof", height: 0.1, color: hexColor(p.roof_color, "#C45A3C") },
            { type: "door", width: 0.2, height: 0.32, color: hexColor(p.door_color, "#6B4226") },
            {
                type: "pilasters",
                count: clampInt(p.pilaster_count != null ? p.pilaster_count : 6, 0, 12),
                height: 0.55,
                color: hexColor(p.pilaster_color, "#F0EAD6"),
            },
        ];
        return scaleComponents(base, scale);
    }

    function insula(params, tileW, tileD) {
        const refW = 1.8;
        const refD = 1.8;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            {
                type: "block",
                stories: clampInt(p.stories != null ? p.stories : 4, 1, 8),
                storyHeight: 0.18,
                color: hexColor(p.block_color, "#B85C3A"),
                windows: clampInt(p.windows != null ? p.windows : 4, 1, 12),
                windowColor: hexColor(p.window_color, "#1A1008"),
            },
            { type: "tiled_roof", height: 0.08, color: hexColor(p.roof_color, "#C45A3C") },
            { type: "door", width: 0.1, height: 0.2, color: hexColor(p.door_color, "#6B4226") },
        ];
        return scaleComponents(base, scale);
    }

    function domus(params, tileW, tileD) {
        const refW = 2.7;
        const refD = 1.8;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            {
                type: "walls",
                height: 0.35,
                color: hexColor(p.wall_color, "#F0EAD6"),
                thickness: 0.06,
            },
            { type: "atrium", height: 0.25, color: hexColor(p.atrium_color, "#F0EAD6") },
            { type: "tiled_roof", height: 0.08, color: hexColor(p.roof_color, "#C45A3C") },
            { type: "door", width: 0.1, height: 0.2, color: hexColor(p.door_color, "#6B4226") },
        ];
        return scaleComponents(base, scale);
    }

    function thermae(params, tileW, tileD) {
        const refW = 3.6;
        const refD = 2.7;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            { type: "podium", steps: clampInt(p.podium_steps, 2, 8), height: 0.06, color: hexColor(p.podium_color, "#F5E6C8") },
            {
                type: "block",
                stories: 1,
                storyHeight: 0.5,
                color: hexColor(p.block_color, "#B85C3A"),
                windows: clampInt(p.windows != null ? p.windows : 8, 1, 16),
                windowColor: hexColor(p.window_color, "#1A1008"),
            },
            { type: "dome", radius: 0.3, color: hexColor(p.dome_color, "#A09880") },
            { type: "fountain", radius: 0.12, height: 0.15, color: hexColor(p.fountain_color, "#F0F0F0") },
            { type: "door", width: 0.18, height: 0.3, color: hexColor(p.door_color, "#6B4226") },
            {
                type: "pilasters",
                count: clampInt(p.pilaster_count != null ? p.pilaster_count : 4, 0, 10),
                height: 0.55,
                color: hexColor(p.pilaster_color, "#F5E6C8"),
            },
        ];
        return scaleComponents(base, scale);
    }

    function amphitheater(params, tileW, tileD) {
        const refW = 3.6;
        const refD = 3.6;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            {
                type: "arcade",
                arches: clampInt(p.arches != null ? p.arches : 8, 4, 24),
                height: 0.35,
                color: hexColor(p.arcade_color, "#F5E6C8"),
            },
            { type: "tier", height: 0.15, color: hexColor(p.tier1_color, "#F5E6C8") },
            { type: "tier", height: 0.12, color: hexColor(p.tier2_color, "#A09880") },
            { type: "tier", height: 0.1, color: hexColor(p.tier3_color, "#A09880") },
            {
                type: "pilasters",
                count: clampInt(p.pilaster_count != null ? p.pilaster_count : 8, 0, 16),
                height: 0.3,
                color: hexColor(p.pilaster_color, "#F5E6C8"),
            },
        ];
        return scaleComponents(base, scale);
    }

    function market(params, tileW, tileD) {
        const refW = 1.8;
        const refD = 0.9;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            {
                type: "block",
                stories: 1,
                storyHeight: 0.3,
                color: hexColor(p.block_color, "#B85C3A"),
                windows: clampInt(p.windows != null ? p.windows : 2, 1, 8),
                windowColor: hexColor(p.window_color, "#1A1008"),
            },
            { type: "awning", color: hexColor(p.awning_color, "#CC3333") },
            { type: "flat_roof", color: hexColor(p.roof_color, "#A09880") },
            { type: "door", width: 0.12, height: 0.2, color: hexColor(p.door_color, "#6B4226") },
        ];
        return scaleComponents(base, scale);
    }

    function monument(params, tileW, tileD) {
        const refW = 1.8;
        const refD = 1.8;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            { type: "podium", steps: clampInt(p.podium_steps != null ? p.podium_steps : 5, 1, 12), height: 0.2, color: hexColor(p.podium_color, "#F0F0F0") },
            {
                type: "statue",
                height: 0.45,
                color: hexColor(p.statue_color, "#8B6914"),
                pedestalColor: hexColor(p.pedestal_color, "#F0F0F0"),
            },
            {
                type: "pilasters",
                count: clampInt(p.pilaster_count != null ? p.pilaster_count : 4, 0, 8),
                height: 0.15,
                color: hexColor(p.pilaster_color, "#F0F0F0"),
            },
        ];
        return scaleComponents(base, scale);
    }

    function gate(params, tileW, tileD) {
        const refW = 1.8;
        const refD = 0.9;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            {
                type: "arcade",
                arches: clampInt(p.arches != null ? p.arches : 1, 1, 4),
                height: 0.5,
                color: hexColor(p.arcade_color, "#F5E6C8"),
            },
            { type: "battlements", height: 0.08, color: hexColor(p.battlement_color, "#C8B070") },
            {
                type: "pilasters",
                count: clampInt(p.pilaster_count != null ? p.pilaster_count : 4, 0, 8),
                height: 0.4,
                color: hexColor(p.pilaster_color, "#C8B070"),
            },
            { type: "flat_roof", color: hexColor(p.roof_color, "#F5E6C8"), overhang: 0.04 },
        ];
        return scaleComponents(base, scale);
    }

    function wall(params, tileW, tileD) {
        const refW = 0.9;
        const refD = 0.9;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            {
                type: "walls",
                height: 0.45,
                color: hexColor(p.wall_color, "#C8B070"),
                thickness: 0.1,
            },
            { type: "battlements", height: 0.08, color: hexColor(p.battlement_color, "#C8B070") },
        ];
        return scaleComponents(base, scale);
    }

    function aqueduct(params, tileW, tileD) {
        const refW = 0.9;
        const refD = 2.7;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            {
                type: "arcade",
                arches: clampInt(p.arches != null ? p.arches : 4, 1, 12),
                height: 0.6,
                color: hexColor(p.arcade_color, "#C8B070"),
            },
            { type: "flat_roof", color: hexColor(p.deck_color, "#C8B070") },
        ];
        return scaleComponents(base, scale);
    }

    /** Taberna — single-story shop with wide opening, awning, no columns. */
    function taberna(params, tileW, tileD) {
        const refW = 0.9;
        const refD = 0.9;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            {
                type: "block",
                stories: 1,
                storyHeight: 0.25,
                color: hexColor(p.wall_color, "#B85C3A"),
                windows: clampInt(p.windows != null ? p.windows : 1, 0, 4),
                windowColor: hexColor(p.window_color, "#1A1008"),
            },
            { type: "awning", color: hexColor(p.awning_color, "#CC3333") },
            { type: "door", width: 0.14, height: 0.18, color: hexColor(p.door_color, "#6B4226") },
            { type: "flat_roof", color: hexColor(p.roof_color, "#A09880"), overhang: 0.02 },
        ];
        return scaleComponents(base, scale);
    }

    /** Warehouse (horreum) — tall plain block, minimal windows, wide loading door, flat roof. */
    function warehouse(params, tileW, tileD) {
        const refW = 2.7;
        const refD = 1.8;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            { type: "podium", steps: clampInt(p.podium_steps, 1, 4), height: 0.04, color: hexColor(p.podium_color, "#808080") },
            {
                type: "block",
                stories: clampInt(p.stories != null ? p.stories : 2, 1, 3),
                storyHeight: 0.35,
                color: hexColor(p.wall_color, "#C8B070"),
                windows: clampInt(p.windows != null ? p.windows : 2, 0, 6),
                windowColor: hexColor(p.window_color, "#1A1008"),
            },
            { type: "door", width: 0.2, height: 0.28, color: hexColor(p.door_color, "#6B4226") },
            { type: "flat_roof", color: hexColor(p.roof_color, "#A09880"), overhang: 0.03 },
        ];
        return scaleComponents(base, scale);
    }

    /** Circus — elongated track with low walls and seating tiers. */
    function circus(params, tileW, tileD) {
        const refW = 5.4;
        const refD = 1.8;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            { type: "walls", height: 0.2, thickness: 0.08, color: hexColor(p.wall_color, "#F5E6C8") },
            { type: "tier", height: 0.15, color: hexColor(p.tier1_color, "#F5E6C8") },
            { type: "tier", height: 0.12, color: hexColor(p.tier2_color, "#A09880") },
            {
                type: "pilasters",
                count: clampInt(p.pilaster_count != null ? p.pilaster_count : 6, 0, 12),
                height: 0.25,
                color: hexColor(p.pilaster_color, "#F5E6C8"),
            },
        ];
        return scaleComponents(base, scale);
    }

    /** Bridge — arched span, heavier piers, flat deck, no columns. */
    function bridge(params, tileW, tileD) {
        const refW = 0.9;
        const refD = 2.7;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            {
                type: "arcade",
                arches: clampInt(p.arches != null ? p.arches : 3, 1, 8),
                height: 0.4,
                color: hexColor(p.pier_color, "#C8B070"),
            },
            { type: "flat_roof", color: hexColor(p.deck_color, "#808080"), overhang: 0.02 },
            { type: "walls", height: 0.06, thickness: 0.03, color: hexColor(p.parapet_color, "#C8B070") },
        ];
        return scaleComponents(base, scale);
    }

    /**
     * Talud-tablero–style stepped massing (stacked slabs) for twin-temple / pyramid cores.
     * Params: tiers, stone_color, tablero_color, base_color, shrine_color, roof_color, podium_steps, windows.
     */
    function mesoamericanPyramidSlabs(p, scale, tileW, tileD) {
        const tiers = clampInt(p.tiers != null ? p.tiers : 5, 3, 12);
        const stone = hexColor(p.stone_color, "#A85A28");
        const tablero = hexColor(p.tablero_color, "#E8D4BE");
        const parts = [];
        let yCursor = 0;
        const w = Math.max(0.35, Number(tileW) || 0.9);
        const d = Math.max(0.35, Number(tileD) || 0.9);
        for (let t = 0; t < tiers; t++) {
            const frac = 1 - (t / tiers) * 0.68;
            const wx = w * 0.46 * frac;
            const dz = d * 0.46 * frac;
            const dy = (0.048 + (t % 2) * 0.014) * scale;
            const col = t % 2 === 0 ? stone : tablero;
            parts.push({
                shape: "box",
                size: [wx, dy, dz],
                position: [0, yCursor + dy / 2, 0],
                color: col,
                roughness: 0.82,
            });
            yCursor += dy;
        }
        return parts;
    }

    function mesoamerican_temple(params, tileW, tileD) {
        const refW = 2.7;
        const refD = 2.7;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            {
                type: "podium",
                steps: clampInt(p.podium_steps != null ? p.podium_steps : 8, 4, 16),
                height: 0.2,
                color: hexColor(p.base_color, "#8B4A1A"),
            },
            {
                type: "procedural",
                stack_role: "structural",
                stack_priority: 0,
                parts: mesoamericanPyramidSlabs(p, scale, tileW, tileD),
            },
            {
                type: "block",
                stack_priority: 1,
                stories: 1,
                storyHeight: 0.14,
                color: hexColor(p.shrine_color, "#D4C4A8"),
                windows: clampInt(p.windows != null ? p.windows : 2, 0, 6),
                windowColor: "#2a1810",
            },
            { type: "flat_roof", color: hexColor(p.roof_color, "#B84520"), overhang: 0.025 },
        ];
        return scaleComponents(base, scale);
    }

    function mesoamerican_shrine(params, tileW, tileD) {
        const refW = 1.8;
        const refD = 1.8;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({ tiers: 3 }, params);
        const base = [
            {
                type: "podium",
                steps: clampInt(p.podium_steps != null ? p.podium_steps : 5, 2, 10),
                height: 0.12,
                color: hexColor(p.base_color, "#9A4A18"),
            },
            {
                type: "procedural",
                stack_role: "structural",
                stack_priority: 0,
                parts: mesoamericanPyramidSlabs(p, scale, tileW * 0.85, tileD * 0.85),
            },
            {
                type: "statue",
                height: 0.22,
                color: hexColor(p.statue_color, "#5A4A3A"),
                pedestalColor: hexColor(p.pedestal_color, "#C8B8A0"),
            },
        ];
        return scaleComponents(base, scale);
    }

    /** Courtyard house / palace wing — adobe block massing, few openings, flat roof (no Greco-Roman colonnade). */
    function mesoamerican_civic(params, tileW, tileD) {
        const refW = 3.6;
        const refD = 2.7;
        const scale = footprintScale(tileW, tileD, refW, refD);
        const p = mergeParams({}, params);
        const base = [
            {
                type: "block",
                stories: clampInt(p.stories != null ? p.stories : 2, 1, 3),
                storyHeight: 0.32,
                color: hexColor(p.adobe_color, "#B8956A"),
                windows: clampInt(p.windows != null ? p.windows : 5, 1, 16),
                windowColor: "#1A1008",
            },
            { type: "awning", color: hexColor(p.awning_color, "#8B3A2A") },
            { type: "flat_roof", color: hexColor(p.roof_color, "#A07040"), overhang: 0.04 },
            { type: "door", width: 0.14, height: 0.24, color: hexColor(p.door_color, "#4A2810") },
        ];
        return scaleComponents(base, scale);
    }

    /**
     * Generic / culture-agnostic: full component list from the model (Egyptian, Amazonian, any era).
     * params.components — required, non-empty array (same schema as spec.components).
     * params.ref_w, params.ref_d — optional positive numbers; if both set, scale scalars like golden_specs.
     */
    function open(params, tileW, tileD) {
        const p = mergeParams({}, params);
        const comps = p.components;
        if (!Array.isArray(comps) || comps.length === 0) {
            throw new Error('template "open" requires params.components as a non-empty array');
        }
        let cloned;
        try {
            cloned = JSON.parse(JSON.stringify(comps));
        } catch (e) {
            throw new Error('template "open": params.components must be JSON-serializable');
        }
        const rw = p.ref_w;
        const rd = p.ref_d;
        if (rw != null && rd != null) {
            const nw = Number(rw);
            const nd = Number(rd);
            if (!(nw > 0) || !(nd > 0)) {
                throw new Error('template "open": ref_w and ref_d must be positive numbers when set');
            }
            const w = Math.max(0.35, Number(tileW) || 0.9);
            const d = Math.max(0.35, Number(tileD) || 0.9);
            return scaleComponents(cloned, footprintScale(w, d, nw, nd));
        }
        return cloned;
    }

    const TEMPLATES = {
        open,
        temple,
        basilica,
        insula,
        domus,
        thermae,
        amphitheater,
        market,
        monument,
        gate,
        wall,
        aqueduct,
        taberna,
        warehouse,
        circus,
        bridge,
        mesoamerican_temple,
        mesoamerican_shrine,
        mesoamerican_civic,
    };

    /**
     * @param {string} id
     * @param {Record<string, unknown>} params
     * @param {number} tileW
     * @param {number} tileD
     * @returns {object[]}
     */
    function expandParametricTemplate(id, params, tileW, tileD) {
        const fn = TEMPLATES[id];
        if (!fn) {
            throw new Error(`Unknown parametric template id: ${id}`);
        }
        const w = Math.max(0.35, Number(tileW) || 0.9);
        const d = Math.max(0.35, Number(tileD) || 0.9);
        const list = fn(params || {}, w, d);
        if (!Array.isArray(list) || list.length === 0) {
            throw new Error(`Parametric template ${id} produced no components`);
        }
        return list;
    }

    global.expandParametricTemplate = expandParametricTemplate;
    global.PARAMETRIC_TEMPLATE_IDS = Object.keys(TEMPLATES);
})(typeof window !== "undefined" ? window : globalThis);
