"""Discovery and survey boundary — delegates to ``Generators`` (single implementation site)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestration.generators import Generators


class DiscoveryCoordinator:
    """Callable surface for ``TaskManager`` survey prefetch without lambdas on ``BuildEngine``."""

    def __init__(self, generators: "Generators") -> None:
        self._generators = generators

    def survey_work_item(self, district_index: int):
        """Async work unit for one district survey (same contract as ``Generators.survey_work_item``)."""
        return self._generators.survey_work_item(district_index)
