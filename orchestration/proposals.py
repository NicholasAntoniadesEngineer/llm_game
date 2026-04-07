"""Work proposal queue — agents propose, prioritize, and dequeue work items.

Agents can suggest new buildings, revisions to existing designs, or fixes to
problems they've noticed. The ProposalQueue provides a priority-ordered buffer
that the engine can drain at its own pace.

Usage:
    queue = ProposalQueue()
    queue.add(WorkProposal(
        proposer="urbanista",
        proposal_type="revise",
        target="Temple of Jupiter",
        reason="Colonnade proportions off — columns too thin for entablature weight",
        priority=0.8,
    ))
    while (proposal := queue.get_next()) is not None:
        await process(proposal)
"""

from __future__ import annotations

import heapq
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("eternal.proposals")


@dataclass(order=False)
class WorkProposal:
    """A single work item proposed by an agent.

    Attributes:
        proposer: Agent role that created the proposal (e.g., "urbanista").
        proposal_type: Kind of work — "add" (new building), "revise" (improve
            existing), or "fix" (correct a problem).
        target: Building name, district name, or tile coordinates targeted.
        reason: Brief explanation of why this work is needed.
        priority: Float in [0, 1] where 1.0 is most urgent. Defaults to 0.5.
        created_at: Unix timestamp of proposal creation (auto-set).
        metadata: Optional dict for extra context (building_type, tile coords, etc.).
    """

    proposer: str
    proposal_type: str  # "add", "revise", "fix"
    target: str
    reason: str
    priority: float = 0.5
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.priority = max(0.0, min(1.0, self.priority))

    def to_compact(self) -> str:
        """Compact string representation for logging and prompt injection.

        Returns:
            e.g. ``[0.8|revise] urbanista->Temple of Jupiter: proportions off``
        """
        reason_short = self.reason[:60]
        return f"[{self.priority:.1f}|{self.proposal_type}] {self.proposer}->{self.target}: {reason_short}"


class ProposalQueue:
    """Priority queue of agent-proposed work items.

    Proposals are stored in a max-heap (highest priority first). Equal-priority
    proposals are ordered by creation time (oldest first = FIFO within priority).

    Thread-safe for single-threaded async usage (no locks needed for asyncio).

    Attributes:
        max_size: Maximum proposals stored. Oldest low-priority items are
            dropped when exceeded. Defaults to 100.
    """

    def __init__(self, max_size: int = 100):
        self.max_size = max_size
        # Heap entries: (-priority, created_at, proposal) for max-heap via min-heap
        self._heap: list[tuple[float, float, WorkProposal]] = []
        self._counter = 0  # Tiebreaker for equal priority+time

    def add(self, proposal: WorkProposal) -> None:
        """Add a proposal to the queue.

        If the queue is at capacity, the lowest-priority proposal is dropped.

        Args:
            proposal: The work proposal to enqueue.
        """
        entry = (-proposal.priority, proposal.created_at, proposal)
        heapq.heappush(self._heap, entry)
        self._counter += 1

        # Trim if over capacity: rebuild keeping only top max_size
        if len(self._heap) > self.max_size:
            self._heap.sort()
            self._heap = self._heap[:self.max_size]
            heapq.heapify(self._heap)
            logger.debug(
                "Proposal queue trimmed to %d (dropped lowest priority)",
                len(self._heap),
            )

        logger.info("Proposal added: %s", proposal.to_compact())

    def get_next(self) -> WorkProposal | None:
        """Pop and return the highest-priority proposal, or None if empty.

        Returns:
            The highest-priority WorkProposal, or None.
        """
        if not self._heap:
            return None
        _neg_pri, _time, proposal = heapq.heappop(self._heap)
        return proposal

    def peek(self) -> WorkProposal | None:
        """View the highest-priority proposal without removing it.

        Returns:
            The highest-priority WorkProposal, or None.
        """
        if not self._heap:
            return None
        return self._heap[0][2]

    def pending_count(self) -> int:
        """Number of proposals waiting in the queue."""
        return len(self._heap)

    def pending_by_type(self) -> dict[str, int]:
        """Count of pending proposals grouped by proposal_type.

        Returns:
            e.g. ``{"add": 3, "revise": 2, "fix": 1}``
        """
        counts: dict[str, int] = {}
        for _, _, proposal in self._heap:
            t = proposal.proposal_type
            counts[t] = counts.get(t, 0) + 1
        return counts

    def drain(self, max_items: int = 10) -> list[WorkProposal]:
        """Pop up to ``max_items`` proposals in priority order.

        Args:
            max_items: Maximum number to dequeue.

        Returns:
            List of WorkProposal instances, highest priority first.
        """
        results = []
        for _ in range(max_items):
            p = self.get_next()
            if p is None:
                break
            results.append(p)
        return results

    def format_summary(self, max_items: int = 5) -> str:
        """Compact summary of top proposals for logging or prompt injection.

        Does NOT remove proposals from the queue.

        Args:
            max_items: Maximum proposals to include in summary.

        Returns:
            Multi-line compact summary, or ``(no proposals)`` if empty.
        """
        if not self._heap:
            return "(no proposals)"
        # Sort a copy to get top N
        sorted_entries = sorted(self._heap)[:max_items]
        lines = [entry[2].to_compact() for entry in sorted_entries]
        return "PROPOSALS:\n" + "\n".join(lines)

    def clear(self) -> None:
        """Remove all proposals."""
        self._heap.clear()
        self._counter = 0
