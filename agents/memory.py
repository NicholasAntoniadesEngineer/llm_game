"""Agent memory system — conversation history and style tracking.

Provides lightweight, token-efficient memory that agents accumulate across calls.
All formatted output targets <100 tokens for prompt injection.

Classes:
    ConversationMemory — Rolling window of recent interactions per agent.
    StyleMemory — Tracks Urbanista's evolving design preferences for a city.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


class ConversationMemory:
    """Tracks conversation history per agent instance.

    Stores compact summaries of recent interactions (instruction/response pairs)
    so agents can reference their own recent decisions without full transcript replay.

    Attributes:
        history: Rolling list of {role, summary, turn} entries.
        max_entries: Maximum entries retained (oldest dropped on overflow).
    """

    def __init__(self, max_entries: int = 10):
        self.history: list[dict[str, Any]] = []
        self.max_entries = max_entries

    def add(self, role: str, content: str, turn: int) -> None:
        """Record an interaction summary.

        Args:
            role: "user" (instruction sent) or "assistant" (response received).
            content: Raw text — truncated to 200 chars for storage.
            turn: World turn number at time of interaction.
        """
        self.history.append({
            "role": role,
            "summary": content[:200],
            "turn": turn,
        })
        if len(self.history) > self.max_entries:
            self.history = self.history[-self.max_entries:]

    def format_context(self) -> str:
        """Compact context string for injection into prompts.

        Returns a terse multi-line block summarizing recent turns.
        Empty string if no history exists (no wasted tokens).

        Format per line: ``t{turn}|{role}:{summary_snippet}``
        """
        if not self.history:
            return ""
        lines = []
        for entry in self.history[-5:]:  # Last 5 for brevity
            snippet = entry["summary"][:80].replace("\n", " ")
            lines.append(f"t{entry['turn']}|{entry['role']}:{snippet}")
        return "MEMORY:\n" + "\n".join(lines)

    def clear(self) -> None:
        """Reset all stored history."""
        self.history.clear()

    def __len__(self) -> int:
        return len(self.history)


class StyleMemory:
    """Tracks Urbanista's evolving design preferences for a city.

    Records materials, heights, colors, and motifs from each completed design
    so that future designs can maintain visual coherence without explicit
    repetition in prompts.

    All output methods produce compact, token-efficient strings.
    """

    def __init__(self):
        self.materials_used: Counter[str] = Counter()
        self.heights: list[float] = []
        self.color_palette: set[str] = set()
        self.motifs: list[str] = []

    def record_design(self, spec: dict) -> None:
        """Extract and record style choices from a building spec.

        Parses the Urbanista output format: expects a dict with optional keys
        ``tiles`` (list of tile dicts, each with optional ``spec.components``),
        ``materials``, ``colors``, ``motifs``.

        Args:
            spec: Urbanista JSON output dict.
        """
        # Extract from tiles[].spec.components[].material
        tiles = spec.get("tiles", [])
        for tile_data in tiles:
            if not isinstance(tile_data, dict):
                continue
            tile_spec = tile_data.get("spec")
            if not isinstance(tile_spec, dict):
                continue
            components = tile_spec.get("components", [])
            if not isinstance(components, list):
                continue
            for comp in components:
                if not isinstance(comp, dict):
                    continue
                mat = comp.get("material")
                if mat and isinstance(mat, str):
                    self.materials_used[mat] += 1
                h = comp.get("height")
                if isinstance(h, (int, float)) and h > 0:
                    self.heights.append(float(h))
                color = comp.get("color")
                if color and isinstance(color, str) and color.startswith("#"):
                    self.color_palette.add(color)

        # Top-level convenience fields (some prompts return these)
        if isinstance(spec.get("materials"), list):
            for m in spec["materials"]:
                if isinstance(m, str):
                    self.materials_used[m] += 1
        if isinstance(spec.get("colors"), list):
            for c in spec["colors"]:
                if isinstance(c, str) and c.startswith("#"):
                    self.color_palette.add(c)
        if isinstance(spec.get("motifs"), list):
            for m in spec["motifs"]:
                if isinstance(m, str) and m not in self.motifs:
                    self.motifs.append(m)

    def format_style_context(self) -> str:
        """Compact summary for Urbanista prompt injection.

        Returns empty string if no data recorded yet.

        Format example:
            ``STYLE:mats=travertine(12),brick(8);h_avg=1.2;palette=#f5ead6,#b5603a;motifs=Corinthian columns``
        """
        parts = []

        # Top materials (up to 5)
        if self.materials_used:
            top = self.materials_used.most_common(5)
            mats_str = ",".join(f"{m}({c})" for m, c in top)
            parts.append(f"mats={mats_str}")

        # Average height
        if self.heights:
            avg_h = round(sum(self.heights) / len(self.heights), 1)
            parts.append(f"h_avg={avg_h}")

        # Color palette (up to 6 colors)
        if self.color_palette:
            colors = sorted(self.color_palette)[:6]
            parts.append(f"palette={','.join(colors)}")

        # Motifs (up to 3)
        if self.motifs:
            parts.append(f"motifs={';'.join(self.motifs[:3])}")

        if not parts:
            return ""
        return "STYLE:" + ";".join(parts)

    def clear(self) -> None:
        """Reset all style tracking."""
        self.materials_used.clear()
        self.heights.clear()
        self.color_palette.clear()
        self.motifs.clear()
