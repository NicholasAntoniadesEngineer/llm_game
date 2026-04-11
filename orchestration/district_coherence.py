"""District coherence and building relationship management.

Ensures buildings form coherent districts with appropriate relationships,
cultural consistency, and functional zoning.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Any

logger = logging.getLogger("eternal.district_coherence")


class DistrictCoherenceManager:
    """Manages district coherence and building relationships."""

    def __init__(self):
        self.districts: Dict[str, Dict[str, Any]] = {}
        self.building_relationships: Dict[str, Set[str]] = defaultdict(set)
        self.cultural_contexts: Dict[str, str] = {}

    def register_building(self, building: Dict[str, Any], district: str) -> None:
        """Register a building in a district and establish relationships."""
        building_name = building.get("name", "")
        building_type = building.get("building_type", "")

        if district not in self.districts:
            self.districts[district] = {
                "buildings": [],
                "types": defaultdict(int),
                "center": None,
                "bounds": None,
                "coherence_score": 0.0
            }

        # Add to district
        self.districts[district]["buildings"].append(building)
        self.districts[district]["types"][building_type] += 1

        # Establish relationships
        self._establish_relationships(building_name, building_type, district)

        # Update district coherence
        self._update_district_coherence(district)

    def _establish_relationships(self, building_name: str, building_type: str, district: str) -> None:
        """Establish relationships between buildings based on type and function."""
        district_buildings = self.districts[district]["buildings"]

        # Functional relationships
        if building_type in ("temple", "shrine", "monument"):
            # Religious buildings relate to other civic buildings
            for other in district_buildings:
                other_type = other.get("building_type", "")
                if other_type in ("basilica", "forum", "temple"):
                    self.building_relationships[building_name].add(other.get("name", ""))
                    self.building_relationships[other.get("name", "")].add(building_name)

        elif building_type in ("market", "taberna", "warehouse"):
            # Commercial buildings relate to each other and roads
            for other in district_buildings:
                other_type = other.get("building_type", "")
                if other_type in ("market", "taberna", "warehouse", "road"):
                    self.building_relationships[building_name].add(other.get("name", ""))
                    self.building_relationships[other.get("name", "")].add(building_name)

        elif building_type in ("thermae", "amphitheater", "circus"):
            # Public entertainment buildings relate to civic areas
            for other in district_buildings:
                other_type = other.get("building_type", "")
                if other_type in ("forum", "basilica", "temple"):
                    self.building_relationships[building_name].add(other.get("name", ""))
                    self.building_relationships[other.get("name", "")].add(building_name)

    def _update_district_coherence(self, district: str) -> None:
        """Update the coherence score for a district."""
        district_data = self.districts[district]
        buildings = district_data["buildings"]
        types = district_data["types"]

        if not buildings:
            district_data["coherence_score"] = 0.0
            return

        # Calculate coherence based on functional zoning
        coherence_score = 0.0

        # Residential coherence
        residential_types = {"insula", "domus", "villa"}
        residential_count = sum(types.get(t, 0) for t in residential_types)
        if residential_count > 0:
            residential_ratio = residential_count / len(buildings)
            coherence_score += residential_ratio * 0.4  # Residential districts get coherence bonus

        # Commercial coherence
        commercial_types = {"market", "taberna", "warehouse"}
        commercial_count = sum(types.get(t, 0) for t in commercial_types)
        if commercial_count > 0:
            commercial_ratio = commercial_count / len(buildings)
            coherence_score += commercial_ratio * 0.3  # Commercial districts get coherence bonus

        # Civic coherence
        civic_types = {"temple", "basilica", "forum", "monument"}
        civic_count = sum(types.get(t, 0) for t in civic_types)
        if civic_count > 0:
            civic_ratio = civic_count / len(buildings)
            coherence_score += civic_ratio * 0.3  # Civic districts get coherence bonus

        # Diversity penalty (too many different types reduces coherence)
        unique_types = len([t for t in types.values() if t > 0])
        if unique_types > 5:
            diversity_penalty = (unique_types - 5) * 0.1
            coherence_score -= min(diversity_penalty, 0.3)

        district_data["coherence_score"] = max(0.0, min(1.0, coherence_score))

    def get_district_recommendations(self, district: str) -> List[str]:
        """Get recommendations for improving district coherence."""
        if district not in self.districts:
            return []

        district_data = self.districts[district]
        buildings = district_data["buildings"]
        types = district_data["types"]
        coherence = district_data["coherence_score"]

        recommendations = []

        if coherence < 0.5:
            # Low coherence - suggest functional zoning
            if len(types) > 6:
                recommendations.append("Consider splitting this district - too many different building types reduce coherence")

            # Check for missing functional elements
            has_civic = any(t in ("temple", "basilica", "forum") for t in types.keys())
            has_commercial = any(t in ("market", "taberna") for t in types.keys())
            has_residential = any(t in ("insula", "domus") for t in types.keys())

            if not has_civic and len(buildings) > 3:
                recommendations.append("Add civic buildings (temple, basilica, forum) for district focus")

            if not has_commercial and len(buildings) > 5:
                recommendations.append("Add commercial buildings (market, tabernae) for economic activity")

            if not has_residential and len(buildings) > 4:
                recommendations.append("Add residential buildings (insulae, domus) for population")

        elif coherence > 0.8:
            # High coherence - suggest enhancements
            recommendations.append("District has excellent functional coherence - consider adding complementary buildings")

        return recommendations

    def get_building_context(self, building_name: str) -> Dict[str, Any]:
        """Get contextual information for a building based on its relationships."""
        context = {
            "district": None,
            "related_buildings": [],
            "district_coherence": 0.0,
            "functional_role": "unknown",
            "cultural_context": "unknown"
        }

        # Find district
        for district_name, district_data in self.districts.items():
            for building in district_data["buildings"]:
                if building.get("name") == building_name:
                    context["district"] = district_name
                    context["district_coherence"] = district_data["coherence_score"]
                    context["functional_role"] = self._infer_functional_role(building, district_data)
                    break
            if context["district"]:
                break

        # Get related buildings
        context["related_buildings"] = list(self.building_relationships.get(building_name, []))

        return context

    def _infer_functional_role(self, building: Dict[str, Any], district_data: Dict[str, Any]) -> str:
        """Infer the functional role of a building within its district."""
        building_type = building.get("building_type", "")
        district_types = district_data["types"]

        # Determine primary district function
        max_type = max(district_types.items(), key=lambda x: x[1])[0]

        if building_type == max_type:
            return "primary"  # Dominant type in district
        elif building_type in ("temple", "basilica", "forum"):
            return "civic"
        elif building_type in ("market", "taberna", "warehouse"):
            return "commercial"
        elif building_type in ("insula", "domus"):
            return "residential"
        elif building_type in ("thermae", "amphitheater"):
            return "public"
        else:
            return "specialized"

    def validate_district_integrity(self, master_plan: List[Dict[str, Any]]) -> List[str]:
        """Validate overall district integrity and return issues."""
        issues = []

        # Check for orphaned buildings (not in any coherent district)
        district_membership = set()
        for district_data in self.districts.values():
            for building in district_data["buildings"]:
                district_membership.add(building.get("name", ""))

        for struct in master_plan:
            building_name = struct.get("name", "")
            if building_name and building_name not in district_membership:
                issues.append(f"Building '{building_name}' is not part of any coherent district")

        # Check district sizes
        for district_name, district_data in self.districts.items():
            building_count = len(district_data["buildings"])
            coherence = district_data["coherence_score"]

            if building_count < 3 and coherence < 0.6:
                issues.append(f"District '{district_name}' is too small and incoherent - consider merging or expanding")

            if building_count > 15 and coherence < 0.4:
                issues.append(f"District '{district_name}' is large but functionally incoherent - consider splitting")

        return issues


def analyze_urban_patterns(master_plan: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze urban patterns and provide insights for city planning."""
    analysis = {
        "districts": {},
        "urban_density": 0.0,
        "functional_mixing": 0.0,
        "spatial_organization": 0.0,
        "cultural_coherence": 0.0,
        "recommendations": []
    }

    if not master_plan:
        return analysis

    # Calculate urban density
    total_tiles = sum(len(s.get("tiles", [])) for s in master_plan)
    # Assume city area is roughly 100x100 for density calculation
    city_area = 10000
    analysis["urban_density"] = total_tiles / city_area

    # Analyze functional mixing
    building_types = [s.get("building_type", "") for s in master_plan]
    type_counts = defaultdict(int)
    for btype in building_types:
        type_counts[btype] += 1

    # Calculate entropy as measure of functional mixing
    total_buildings = len(master_plan)
    entropy = 0.0
    for count in type_counts.values():
        if count > 0:
            p = count / total_buildings
            entropy -= p * math.log2(p)

    max_entropy = math.log2(len(type_counts)) if type_counts else 0
    analysis["functional_mixing"] = entropy / max_entropy if max_entropy > 0 else 0

    # Generate recommendations based on analysis
    if analysis["urban_density"] < 0.05:
        analysis["recommendations"].append("City density is very low - consider adding more buildings")
    elif analysis["urban_density"] > 0.3:
        analysis["recommendations"].append("City density is very high - consider adding more open spaces")

    if analysis["functional_mixing"] < 0.3:
        analysis["recommendations"].append("City has low functional diversity - consider adding different building types")
    elif analysis["functional_mixing"] > 0.8:
        analysis["recommendations"].append("City has high functional mixing - consider creating more specialized districts")

    return analysis


# Global instance for district coherence management
DISTRICT_MANAGER = DistrictCoherenceManager()