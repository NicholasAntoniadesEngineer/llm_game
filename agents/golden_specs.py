"""Golden reference specs — hand-tuned, architecturally correct component specs.
Injected as few-shot examples into the Urbanista prompt so the AI knows what
correct proportions look like at the actual building size."""

import json

# Each spec is tuned for a reference footprint size.
# Heights and dimensions will be scaled proportionally to the actual footprint.
GOLDEN_SPECS = {
    "temple": {
        "ref_w": 2.7, "ref_d": 1.8,
        "components": [
            {"type": "podium", "steps": 5, "height": 0.12, "color": "#F5E6C8"},
            {"type": "colonnade", "columns": 8, "style": "ionic", "height": 0.42, "color": "#808080", "radius": 0.022},
            {"type": "cella", "height": 0.34, "width": 1.4, "depth": 0.9, "color": "#C8B070"},
            {"type": "pediment", "height": 0.09, "color": "#C45A3C"},
            {"type": "pilasters", "count": 4, "height": 0.35, "color": "#808080"},
            {"type": "door", "width": 0.12, "height": 0.22, "color": "#6B4226"},
        ]
    },
    "basilica": {
        "ref_w": 3.6, "ref_d": 1.8,
        "components": [
            {"type": "podium", "steps": 3, "height": 0.08, "color": "#F5E6C8"},
            {"type": "block", "stories": 1, "storyHeight": 0.45, "color": "#F5E6C8", "windows": 6, "windowColor": "#1A1008"},
            {"type": "colonnade", "columns": 10, "style": "corinthian", "height": 0.38, "color": "#F0F0F0", "radius": 0.018, "peripteral": False},
            {"type": "tiled_roof", "height": 0.1, "color": "#C45A3C"},
            {"type": "door", "width": 0.14, "height": 0.28, "color": "#6B4226"},
            {"type": "pilasters", "count": 6, "height": 0.38, "color": "#F0EAD6"},
        ]
    },
    "insula": {
        "ref_w": 1.8, "ref_d": 1.8,
        "components": [
            {"type": "block", "stories": 4, "storyHeight": 0.18, "color": "#B85C3A", "windows": 4, "windowColor": "#1A1008"},
            {"type": "tiled_roof", "height": 0.08, "color": "#C45A3C"},
            {"type": "door", "width": 0.1, "height": 0.2, "color": "#6B4226"},
        ]
    },
    "domus": {
        "ref_w": 2.7, "ref_d": 1.8,
        "components": [
            {"type": "walls", "height": 0.35, "color": "#F0EAD6", "thickness": 0.06},
            {"type": "atrium", "height": 0.25, "color": "#F0EAD6"},
            {"type": "tiled_roof", "height": 0.08, "color": "#C45A3C"},
            {"type": "door", "width": 0.1, "height": 0.2, "color": "#6B4226"},
            {"type": "colonnade", "columns": 4, "style": "ionic", "height": 0.25, "color": "#F0F0F0", "radius": 0.012, "peripteral": False},
        ]
    },
    "thermae": {
        "ref_w": 3.6, "ref_d": 2.7,
        "components": [
            {"type": "podium", "steps": 2, "height": 0.06, "color": "#F5E6C8"},
            {"type": "block", "stories": 1, "storyHeight": 0.4, "color": "#B85C3A", "windows": 5, "windowColor": "#1A1008"},
            {"type": "dome", "radius": 0.28, "color": "#A09880"},
            {"type": "colonnade", "columns": 6, "style": "corinthian", "height": 0.32, "color": "#F0F0F0", "radius": 0.015, "peripteral": False},
            {"type": "fountain", "radius": 0.12, "height": 0.15, "color": "#F0F0F0"},
            {"type": "door", "width": 0.14, "height": 0.25, "color": "#6B4226"},
        ]
    },
    "amphitheater": {
        "ref_w": 3.6, "ref_d": 3.6,
        "components": [
            {"type": "arcade", "arches": 8, "height": 0.35, "color": "#F5E6C8"},
            {"type": "tier", "height": 0.15, "color": "#F5E6C8"},
            {"type": "tier", "height": 0.12, "color": "#A09880"},
            {"type": "tier", "height": 0.1, "color": "#A09880"},
            {"type": "pilasters", "count": 8, "height": 0.3, "color": "#F5E6C8"},
        ]
    },
    "market": {
        "ref_w": 1.8, "ref_d": 0.9,
        "components": [
            {"type": "block", "stories": 1, "storyHeight": 0.3, "color": "#B85C3A", "windows": 2, "windowColor": "#1A1008"},
            {"type": "awning", "color": "#CC3333"},
            {"type": "flat_roof", "color": "#A09880"},
            {"type": "door", "width": 0.12, "height": 0.2, "color": "#6B4226"},
        ]
    },
    "monument": {
        "ref_w": 1.8, "ref_d": 1.8,
        "components": [
            {"type": "podium", "steps": 5, "height": 0.2, "color": "#F0F0F0"},
            {"type": "statue", "height": 0.35, "color": "#8B6914", "pedestalColor": "#F0F0F0"},
            {"type": "colonnade", "columns": 4, "style": "corinthian", "height": 0.3, "color": "#F0F0F0", "radius": 0.012},
        ]
    },
    "gate": {
        "ref_w": 1.8, "ref_d": 0.9,
        "components": [
            {"type": "arcade", "arches": 1, "height": 0.5, "color": "#F5E6C8"},
            {"type": "battlements", "height": 0.08, "color": "#C8B070"},
            {"type": "colonnade", "columns": 4, "style": "composite", "height": 0.4, "color": "#F0F0F0", "radius": 0.015, "peripteral": False},
            {"type": "flat_roof", "color": "#F5E6C8", "overhang": 0.04},
        ]
    },
    "wall": {
        "ref_w": 0.9, "ref_d": 0.9,
        "components": [
            {"type": "walls", "height": 0.45, "color": "#C8B070", "thickness": 0.1},
            {"type": "battlements", "height": 0.08, "color": "#C8B070"},
        ]
    },
    "aqueduct": {
        "ref_w": 0.9, "ref_d": 2.7,
        "components": [
            {"type": "arcade", "arches": 4, "height": 0.6, "color": "#C8B070"},
            {"type": "flat_roof", "color": "#C8B070"},
        ]
    },
}

def get_golden_example(building_type, target_w, target_d):
    """Return a scaled golden spec as a JSON string for prompt injection."""
    ref = GOLDEN_SPECS.get(building_type)
    if not ref:
        # Use temple as generic fallback
        ref = GOLDEN_SPECS["temple"]

    ref_w, ref_d = ref["ref_w"], ref["ref_d"]
    scale = ((target_w / ref_w) + (target_d / ref_d)) / 2

    scaled = []
    for comp in ref["components"]:
        c = dict(comp)
        for key in ("height", "radius", "width", "depth", "thickness", "storyHeight"):
            if key in c:
                c[key] = round(c[key] * scale, 4)
        scaled.append(c)

    return json.dumps(scaled, indent=2)
