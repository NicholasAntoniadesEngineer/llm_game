"""World query tools — structured world-inspection functions for agent context.

Agents need spatial awareness to make good design decisions. These tools query
the WorldState and return compact, token-efficient summaries suitable for
direct injection into LLM prompts.

All output strings are designed to be <100 tokens for the typical case.

Usage:
    tools = WorldQueryTools(world_state)
    context = tools.format_context_block(x=10, y=15, building_type="temple")
    # Inject `context` into the agent's instruction prompt.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from world.state import WorldState

logger = logging.getLogger("eternal.tools")

# Cardinal direction vectors: (dx, dy, label)
_DIRECTIONS = [
    (0, -1, "N"),
    (1, -1, "NE"),
    (1, 0, "E"),
    (1, 1, "SE"),
    (0, 1, "S"),
    (-1, 1, "SW"),
    (-1, 0, "W"),
    (-1, -1, "NW"),
]


class WorldQueryTools:
    """Provides structured world queries for agent prompt injection.

    All methods return compact strings optimized for token efficiency.
    The WorldState is accessed read-only; no mutations occur.

    Args:
        world_state: The active WorldState instance to query against.
    """

    def __init__(self, world_state: "WorldState"):
        self.world = world_state

    def query_neighbors(self, x: int, y: int, radius: int = 3) -> str:
        """Compact neighbor summary within a radius.

        Scans tiles within ``radius`` of (x, y) and reports occupied neighbors
        grouped by cardinal direction from the query point.

        Args:
            x: Center tile X coordinate.
            y: Center tile Y coordinate.
            radius: Search radius in tiles (default 3).

        Returns:
            Compact string, e.g.:
            ``N:Temple(temple,h=12);E:road;S:Insula(insula,h=8)``
            Returns ``(no neighbors)`` if area is empty.
        """
        direction_entries: dict[str, list[str]] = {}

        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if dx == 0 and dy == 0:
                    continue
                tx, ty = x + dx, y + dy
                tile = self.world.get_tile(tx, ty)
                if tile is None or tile.terrain == "empty":
                    continue

                # Determine cardinal direction
                direction = self._classify_direction(dx, dy)
                name = tile.building_name or tile.terrain
                btype = tile.building_type or tile.terrain

                # Extract height from spec if available
                h_str = ""
                if tile.spec and isinstance(tile.spec, dict):
                    components = tile.spec.get("components", [])
                    if isinstance(components, list):
                        max_h = 0.0
                        for comp in components:
                            if isinstance(comp, dict):
                                ch = comp.get("height", 0)
                                if isinstance(ch, (int, float)):
                                    max_h = max(max_h, float(ch))
                        if max_h > 0:
                            h_str = f",h={round(max_h, 1)}"

                entry = f"{name}({btype}{h_str})"
                if direction not in direction_entries:
                    direction_entries[direction] = []
                # Deduplicate same building across multiple tiles
                if entry not in direction_entries[direction]:
                    direction_entries[direction].append(entry)

        if not direction_entries:
            return "(no neighbors)"

        # Order by cardinal direction
        ordered = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        parts = []
        for d in ordered:
            if d in direction_entries:
                entries = direction_entries[d][:2]  # Max 2 per direction
                parts.append(f"{d}:{','.join(entries)}")

        return ";".join(parts)

    def query_district_stats(self, x1: int, y1: int, x2: int, y2: int) -> str:
        """Compact statistics for a rectangular region.

        Args:
            x1, y1: Top-left corner of the region.
            x2, y2: Bottom-right corner of the region.

        Returns:
            Compact string, e.g.:
            ``built=15/40;types=temple:2,insula:5,road:8;gaps=NE,SW``
        """
        total_cells = (x2 - x1 + 1) * (y2 - y1 + 1)
        type_counts: Counter[str] = Counter()
        built = 0

        # Track which quadrants have gaps
        mid_x = (x1 + x2) / 2
        mid_y = (y1 + y2) / 2
        quadrant_built = {"NW": 0, "NE": 0, "SW": 0, "SE": 0}
        quadrant_total = {"NW": 0, "NE": 0, "SW": 0, "SE": 0}

        for tx in range(x1, x2 + 1):
            for ty in range(y1, y2 + 1):
                q = ("N" if ty < mid_y else "S") + ("W" if tx < mid_x else "E")
                quadrant_total[q] += 1

                tile = self.world.get_tile(tx, ty)
                if tile is not None and tile.terrain != "empty":
                    built += 1
                    btype = tile.building_type or tile.terrain
                    type_counts[btype] += 1
                    quadrant_built[q] += 1

        # Identify sparse quadrants (< 25% filled)
        gaps = []
        for q in ["NE", "NW", "SE", "SW"]:
            if quadrant_total[q] > 0 and quadrant_built[q] / quadrant_total[q] < 0.25:
                gaps.append(q)

        parts = [f"built={built}/{total_cells}"]

        if type_counts:
            top_types = type_counts.most_common(6)
            types_str = ",".join(f"{t}:{c}" for t, c in top_types)
            parts.append(f"types={types_str}")

        if gaps:
            parts.append(f"gaps={','.join(gaps)}")

        return ";".join(parts)

    def query_facing(self, x: int, y: int) -> dict:
        """Determine which directions from (x, y) face roads, forums, or open space.

        Checks adjacent tiles (distance 1) in all 8 cardinal directions.

        Args:
            x: Tile X coordinate.
            y: Tile Y coordinate.

        Returns:
            Dict with keys ``roads``, ``forums``, ``open`` — each a list of
            direction labels (e.g., ["N", "E"]).
        """
        result: dict[str, list[str]] = {"roads": [], "forums": [], "open": []}
        for dx, dy, label in _DIRECTIONS:
            tile = self.world.get_tile(x + dx, y + dy)
            if tile is None or tile.terrain == "empty":
                result["open"].append(label)
            elif tile.terrain == "road":
                result["roads"].append(label)
            elif tile.terrain == "forum":
                result["forums"].append(label)
        return result

    def query_style_precedents(self, building_type: str) -> str:
        """Survey existing buildings of a given type for style consistency.

        Scans all placed tiles to find buildings matching ``building_type``
        and extracts their materials and color patterns.

        Args:
            building_type: The building type to survey (e.g., "temple", "insula").

        Returns:
            Compact string, e.g.:
            ``temple_style:mats=travertine(3),marble(2);colors=#f5ead6,#ddd8c4;n=5``
            Returns empty string if no precedents exist.
        """
        mat_counts: Counter[str] = Counter()
        colors: set[str] = set()
        count = 0

        for tile in self.world.tiles.values():
            if tile.building_type != building_type:
                continue
            if tile.terrain == "empty":
                continue
            count += 1
            if tile.spec and isinstance(tile.spec, dict):
                components = tile.spec.get("components", [])
                if isinstance(components, list):
                    for comp in components:
                        if not isinstance(comp, dict):
                            continue
                        mat = comp.get("material")
                        if mat and isinstance(mat, str):
                            mat_counts[mat] += 1
                        color = comp.get("color")
                        if color and isinstance(color, str) and color.startswith("#"):
                            colors.add(color)

        if count == 0:
            return ""

        parts = []
        if mat_counts:
            top = mat_counts.most_common(4)
            parts.append(f"mats={','.join(f'{m}({c})' for m, c in top)}")
        if colors:
            parts.append(f"colors={','.join(sorted(colors)[:5])}")
        parts.append(f"n={count}")

        return f"{building_type}_style:{';'.join(parts)}"

    def format_context_block(self, x: int, y: int, building_type: str | None = None) -> str:
        """Full context block combining neighbors, facing, and style precedents.

        This is the primary method for agent prompt injection. It assembles
        all relevant spatial context into a single compact block.

        Args:
            x: Tile X coordinate.
            y: Tile Y coordinate.
            building_type: Optional building type for style precedent lookup.

        Returns:
            Multi-line compact context block, e.g.::

                CTX@(10,15):
                NBR:N:Temple(temple,h=12);E:road
                FACE:roads=N,E;forums=;open=S,W
                PREC:temple_style:mats=travertine(3);n=5
        """
        lines = [f"CTX@({x},{y}):"]

        neighbors = self.query_neighbors(x, y)
        lines.append(f"NBR:{neighbors}")

        facing = self.query_facing(x, y)
        face_parts = []
        for key in ("roads", "forums", "open"):
            dirs = facing.get(key, [])
            face_parts.append(f"{key}={','.join(dirs) if dirs else ''}")
        lines.append(f"FACE:{';'.join(face_parts)}")

        if building_type:
            precedents = self.query_style_precedents(building_type)
            if precedents:
                lines.append(f"PREC:{precedents}")

        return "\n".join(lines)

    @staticmethod
    def _classify_direction(dx: int, dy: int) -> str:
        """Map a (dx, dy) offset to the nearest cardinal direction label."""
        if dx == 0 and dy < 0:
            return "N"
        if dx == 0 and dy > 0:
            return "S"
        if dx > 0 and dy == 0:
            return "E"
        if dx < 0 and dy == 0:
            return "W"
        if dx > 0 and dy < 0:
            return "NE"
        if dx > 0 and dy > 0:
            return "SE"
        if dx < 0 and dy < 0:
            return "NW"
        return "SW"
