"""Strict district dict normalization for orchestration (coordinates from ``system_config.csv``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.config import Config
from core.errors import ConfigLoadError


@dataclass(frozen=True)
class DistrictSpec:
    """Validated view of one district entry (serializable dict kept for persistence)."""

    name: str
    region_x1: int
    region_y1: int
    region_x2: int
    region_y2: int
    period: str
    year: int
    description: str
    raw: dict[str, Any]

    def as_engine_dict(self) -> dict[str, Any]:
        """Mutable dict aligned with planner / cache shape (same keys as validated input)."""
        out = dict(self.raw)
        out["name"] = self.name
        out["region"] = {
            "x1": self.region_x1,
            "y1": self.region_y1,
            "x2": self.region_x2,
            "y2": self.region_y2,
        }
        out["period"] = self.period
        out["year"] = self.year
        out["description"] = self.description
        return out


def parse_district_dict(raw: dict[str, Any], *, system_configuration: Config) -> DistrictSpec:
    """Parse and clamp region integers; raises ``ConfigLoadError`` if required fields are invalid."""
    if not isinstance(raw, dict):
        raise ConfigLoadError("District entry must be a dict")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ConfigLoadError("District missing non-empty name")
    region = raw.get("region")
    if not isinstance(region, dict):
        raise ConfigLoadError(f"District {name!r} missing region dict")

    def _int_coord(key: str, default: int) -> int:
        try:
            v = int(region.get(key, default))
        except (TypeError, ValueError) as exc:
            raise ConfigLoadError(f"District {name!r} region.{key} invalid") from exc
        lo = int(system_configuration.minimum_coordinate_value)
        hi = int(system_configuration.maximum_coordinate_value)
        if v < lo or v > hi:
            raise ConfigLoadError(
                f"District {name!r} region.{key}={v} out of bounds [{lo}, {hi}]"
            )
        return v

    x1 = _int_coord("x1", 0)
    y1 = _int_coord("y1", 0)
    x2 = _int_coord("x2", x1)
    y2 = _int_coord("y2", y1)

    period = str(raw.get("period", "")).strip()
    try:
        year = int(raw.get("year", 0))
    except (TypeError, ValueError) as exc:
        raise ConfigLoadError(f"District {name!r} year invalid") from exc
    description = str(raw.get("description", ""))

    return DistrictSpec(
        name=name,
        region_x1=x1,
        region_y1=y1,
        region_x2=x2,
        region_y2=y2,
        period=period,
        year=year,
        description=description,
        raw=raw,
    )
