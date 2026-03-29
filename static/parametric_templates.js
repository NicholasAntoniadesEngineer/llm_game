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
        const steps = clampInt(p.podium_steps != null ? p.podium_steps : 5, 1, 14);
        const columns = clampInt(p.columns != null ? p.columns : 8, 4, 32);
        const style = colonnadeStyle(p, "ionic");
        const base = [
            { type: "podium", steps, height: 0.12, color: hexColor(p.podium_color, "#F5E6C8") },
            {
                type: "colonnade",
                columns,
                style,
                height: 0.42,
                color: hexColor(p.colonnade_color, "#808080"),
                radius: 0.022,
            },
            {
                type: "cella",
                height: 0.34,
                width: 1.4,
                depth: 0.9,
                color: hexColor(p.cella_color, "#C8B070"),
            },
            { type: "pediment", height: 0.09, color: hexColor(p.pediment_color, "#C45A3C") },
            {
                type: "pilasters",
                count: clampInt(p.pilaster_count != null ? p.pilaster_count : 4, 0, 12),
                height: 0.35,
                color: hexColor(p.pilaster_color, "#808080"),
            },
            { type: "door", width: 0.12, height: 0.22, color: hexColor(p.door_color, "#6B4226") },
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
                stories: clampInt(p.stories != null ? p.stories : 1, 1, 4),
                storyHeight: 0.45,
                color: hexColor(p.block_color, "#F5E6C8"),
                windows: clampInt(p.windows != null ? p.windows : 6, 1, 20),
                windowColor: hexColor(p.window_color, "#1A1008"),
            },
            {
                type: "colonnade",
                columns: clampInt(p.colonnade_columns != null ? p.colonnade_columns : 10, 4, 24),
                style: colonnadeStyle(p, "corinthian"),
                height: 0.38,
                color: hexColor(p.colonnade_color, "#F0F0F0"),
                radius: 0.018,
                peripteral: false,
            },
            { type: "tiled_roof", height: 0.1, color: hexColor(p.roof_color, "#C45A3C") },
            { type: "door", width: 0.14, height: 0.28, color: hexColor(p.door_color, "#6B4226") },
            {
                type: "pilasters",
                count: clampInt(p.pilaster_count != null ? p.pilaster_count : 6, 0, 12),
                height: 0.38,
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
            {
                type: "colonnade",
                columns: clampInt(p.peristyle_columns != null ? p.peristyle_columns : 4, 2, 12),
                style: colonnadeStyle(p, "ionic"),
                height: 0.25,
                color: hexColor(p.colonnade_color, "#F0F0F0"),
                radius: 0.012,
                peripteral: false,
            },
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
                storyHeight: 0.4,
                color: hexColor(p.block_color, "#B85C3A"),
                windows: clampInt(p.windows != null ? p.windows : 5, 1, 16),
                windowColor: hexColor(p.window_color, "#1A1008"),
            },
            { type: "dome", radius: 0.28, color: hexColor(p.dome_color, "#A09880") },
            {
                type: "colonnade",
                columns: clampInt(p.colonnade_columns != null ? p.colonnade_columns : 6, 4, 16),
                style: colonnadeStyle(p, "corinthian"),
                height: 0.32,
                color: hexColor(p.colonnade_color, "#F0F0F0"),
                radius: 0.015,
                peripteral: false,
            },
            { type: "fountain", radius: 0.12, height: 0.15, color: hexColor(p.fountain_color, "#F0F0F0") },
            { type: "door", width: 0.14, height: 0.25, color: hexColor(p.door_color, "#6B4226") },
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
                height: 0.35,
                color: hexColor(p.statue_color, "#8B6914"),
                pedestalColor: hexColor(p.pedestal_color, "#F0F0F0"),
            },
            {
                type: "colonnade",
                columns: clampInt(p.columns != null ? p.columns : 4, 2, 12),
                style: colonnadeStyle(p, "corinthian"),
                height: 0.3,
                color: hexColor(p.colonnade_color, "#F0F0F0"),
                radius: 0.012,
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
                type: "colonnade",
                columns: clampInt(p.colonnade_columns != null ? p.colonnade_columns : 4, 2, 12),
                style: colonnadeStyle(p, "corinthian"),
                height: 0.4,
                color: hexColor(p.colonnade_color, "#F0F0F0"),
                radius: 0.015,
                peripteral: false,
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
