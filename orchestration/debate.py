"""Multi-agent debate protocol for quality control.

Implements a lightweight review/debate cycle where a critic agent evaluates
an author agent's output and can request revisions. Designed for token efficiency:
critic prompts use compact structured formats, not verbose prose.

The debate protocol is intentionally shallow (max 2 rounds) to avoid runaway
token costs while still catching major issues.

Usage:
    debate = DebateProtocol()
    result = await debate.debate(
        output=urbanista_result,
        author=urbanista_agent,
        critics=[reviewer_agent],
        context={"name": "Temple of Jupiter", "type": "temple", "period": "Republican"},
    )
    if result["revised"]:
        final_output = result["output"]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.base import BaseAgent

logger = logging.getLogger("eternal.debate")


def _compact_output_summary(output: dict) -> str:
    """Build a minimal summary of an agent output for critic review.

    Extracts only the structural facts a critic needs: shape count, materials,
    component types. Full JSON is NOT forwarded to save tokens.

    Args:
        output: The author agent's JSON output dict.

    Returns:
        Compact summary string, e.g.:
        ``shapes=12|mats=travertine,marble,brick|comps=colonnade,walls,roof``
    """
    parts = []
    shapes = 0
    materials: set[str] = set()
    comp_types: set[str] = set()

    tiles = output.get("tiles", [])
    for tile_data in tiles:
        if not isinstance(tile_data, dict):
            continue
        spec = tile_data.get("spec")
        if not isinstance(spec, dict):
            continue
        components = spec.get("components", [])
        if not isinstance(components, list):
            continue
        shapes += len(components)
        for comp in components:
            if not isinstance(comp, dict):
                continue
            mat = comp.get("material")
            if mat and isinstance(mat, str):
                materials.add(mat)
            shape = comp.get("shape")
            if shape and isinstance(shape, str):
                comp_types.add(shape)
            role = comp.get("role")
            if role and isinstance(role, str):
                comp_types.add(role)

    parts.append(f"shapes={shapes}")
    if materials:
        parts.append(f"mats={','.join(sorted(materials)[:6])}")
    if comp_types:
        parts.append(f"comps={','.join(sorted(comp_types)[:8])}")

    return "|".join(parts)


def _build_review_prompt(
    output: dict,
    context: dict[str, Any],
) -> str:
    """Build the compact review prompt for a critic agent.

    Uses a terse structured format to minimize tokens while providing
    all information the critic needs to evaluate the design.

    Args:
        output: Author's JSON output.
        context: Dict with keys like ``name``, ``type``, ``period``, ``expected_style``.

    Returns:
        Compact review prompt string.
    """
    name = context.get("name", "?")
    btype = context.get("type", "?")
    period = context.get("period", "?")
    expected_style = context.get("expected_style", "")
    summary = _compact_output_summary(output)

    commentary = (output.get("commentary") or "")[:150]

    prompt = (
        f"REVIEW:{name}|{btype}|{summary}\n"
        f"RULES:period={period}"
    )
    if expected_style:
        prompt += f";style={expected_style}"
    if commentary:
        prompt += f"\nCOMMENTARY:{commentary}"
    prompt += '\nRESPOND:{"ok":1} or {"ok":0,"issues":["..."],"suggestions":["..."]}'

    return prompt


def _build_revision_prompt(
    output: dict,
    issues: list[str],
    suggestions: list[str],
    context: dict[str, Any],
) -> str:
    """Build a compact revision prompt for the author to address critic feedback.

    Args:
        output: The original output dict.
        issues: List of issue strings from the critic.
        suggestions: List of suggestion strings from the critic.
        context: Same context dict used for review.

    Returns:
        Revision prompt string.
    """
    name = context.get("name", "?")
    btype = context.get("type", "?")

    issues_str = "; ".join(i[:100] for i in issues[:3])
    suggestions_str = "; ".join(s[:100] for s in suggestions[:3])

    prompt = (
        f"REVISE:{name}({btype})\n"
        f"ISSUES:{issues_str}\n"
    )
    if suggestions_str:
        prompt += f"SUGGESTIONS:{suggestions_str}\n"
    prompt += (
        "Fix the issues and return the COMPLETE corrected JSON. "
        "Same format, improved design."
    )
    return prompt


class DebateProtocol:
    """Multi-agent debate for quality control.

    Orchestrates review cycles between an author agent and one or more critic
    agents. The protocol is designed to be:

    - Token-efficient: critics receive compact summaries, not full JSON.
    - Bounded: maximum ``max_rounds`` iterations to prevent runaway costs.
    - Graceful: if critics approve or debate fails, the original output is returned.

    The debate does NOT modify the author or critic agents' internal state
    (memory recording is the caller's responsibility).
    """

    async def review(
        self,
        output: dict,
        author_agent: "BaseAgent",
        critic_agent: "BaseAgent",
        context: dict[str, Any],
    ) -> dict:
        """Single critic reviews author's output.

        Args:
            output: The author's JSON output to review.
            author_agent: The agent that produced the output (for logging).
            critic_agent: The agent performing the review.
            context: Contextual info (name, type, period, expected_style).

        Returns:
            Dict with keys:
                - ``approved`` (bool): Whether the critic approved.
                - ``issues`` (list[str]): Problems identified.
                - ``suggestions`` (list[str]): Improvement ideas.
        """
        prompt = _build_review_prompt(output, context)
        try:
            review_result = await critic_agent.generate(prompt)
        except Exception as e:
            logger.warning(
                "Critic %s review failed: %s — auto-approving",
                critic_agent.role, e,
            )
            return {"approved": True, "issues": [], "suggestions": []}

        ok = review_result.get("ok", 1)
        issues = review_result.get("issues", [])
        suggestions = review_result.get("suggestions", [])

        if not isinstance(issues, list):
            issues = [str(issues)] if issues else []
        if not isinstance(suggestions, list):
            suggestions = [str(suggestions)] if suggestions else []

        approved = bool(ok) and not issues
        logger.info(
            "Critic %s reviewed %s output for %s: approved=%s issues=%d",
            critic_agent.role,
            author_agent.role,
            context.get("name", "?"),
            approved,
            len(issues),
        )
        return {"approved": approved, "issues": issues, "suggestions": suggestions}

    async def debate(
        self,
        output: dict,
        author: "BaseAgent",
        critics: list["BaseAgent"],
        context: dict[str, Any],
        max_rounds: int = 2,
    ) -> dict:
        """Multi-round debate. Author defends, critics challenge.

        Each round: all critics review, if any disapprove the author revises,
        then critics review again. Stops when all approve or max_rounds reached.

        Args:
            output: The initial output from the author.
            author: The author agent.
            critics: List of critic agents.
            context: Contextual info for review prompts.
            max_rounds: Maximum debate rounds (default 2).

        Returns:
            Dict with keys:
                - ``output`` (dict): Final (possibly revised) output.
                - ``revised`` (bool): Whether the output was changed.
                - ``rounds`` (int): Number of rounds executed.
                - ``all_issues`` (list[str]): Accumulated issues across rounds.
        """
        if not critics:
            return {
                "output": output,
                "revised": False,
                "rounds": 0,
                "all_issues": [],
            }

        current_output = output
        revised = False
        all_issues: list[str] = []
        round_num = 0

        for round_num in range(1, max_rounds + 1):
            # Collect all critic reviews
            round_issues: list[str] = []
            round_suggestions: list[str] = []
            all_approved = True

            for critic in critics:
                review = await self.review(current_output, author, critic, context)
                if not review["approved"]:
                    all_approved = False
                    round_issues.extend(review["issues"])
                    round_suggestions.extend(review["suggestions"])

            all_issues.extend(round_issues)

            if all_approved:
                logger.info(
                    "Debate for %s: approved in round %d",
                    context.get("name", "?"), round_num,
                )
                break

            # Author revises
            revision_prompt = _build_revision_prompt(
                current_output, round_issues, round_suggestions, context,
            )
            try:
                revised_output = await author.generate(revision_prompt)
                current_output = revised_output
                revised = True
                logger.info(
                    "Debate for %s: author revised in round %d (%d issues)",
                    context.get("name", "?"), round_num, len(round_issues),
                )
            except Exception as e:
                logger.warning(
                    "Author revision failed in round %d: %s — keeping current output",
                    round_num, e,
                )
                break

        return {
            "output": current_output,
            "revised": revised,
            "rounds": min(round_num, max_rounds) if critics else 0,
            "all_issues": all_issues,
        }
