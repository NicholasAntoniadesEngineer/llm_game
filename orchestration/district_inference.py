"""Single source for inferring district character from planner description text."""


def infer_district_character_from_description(
    description: str,
    *,
    elevation: float | None = None,
) -> dict:
    """Return district character fields (may be empty).

    When ``elevation`` is not None, ``height_range`` is always set from elevation
    (``CityBlueprint.from_districts``), with optional style/wealth from keywords.

    When ``elevation`` is None, only keyword-derived fields are returned, with
    ``height_range`` set from style when a style matched (``BuildEngine._create_blueprint``).
    """
    char: dict = {}
    desc_lower = description.lower()
    if any(w in desc_lower for w in ("monumental", "sacred", "temple", "imperial", "forum")):
        char["style"] = "monumental"
        char["wealth"] = 9
    elif any(w in desc_lower for w in ("market", "commerce", "trade", "mercantile")):
        char["style"] = "commercial"
        char["wealth"] = 6
    elif any(w in desc_lower for w in ("residential", "insula", "domus", "housing")):
        char["style"] = "residential"
        char["wealth"] = 4
    elif any(w in desc_lower for w in ("military", "barracks", "fortress", "wall")):
        char["style"] = "military"
        char["wealth"] = 5
    elif any(w in desc_lower for w in ("garden", "park", "grove")):
        char["style"] = "garden"
        char["wealth"] = 7

    if elevation is not None:
        elev = float(elevation)
        if elev > 0.4:
            char["height_range"] = [2, 4]
        elif elev > 0.2:
            char["height_range"] = [1, 3]
        else:
            char["height_range"] = [1, 2]
    elif char:
        style = char.get("style")
        if style == "monumental":
            char["height_range"] = [2, 4]
        elif style in ("commercial", "residential"):
            char["height_range"] = [1, 3]
        elif style == "military":
            char["height_range"] = [1, 2]
        elif style == "garden":
            char["height_range"] = [1, 2]

    return char


def infer_district_style_string(description: str, *, district_name: str, blueprint) -> str | None:
    """Style for spacing: blueprint district_characters, else keyword inference (style key only)."""
    if blueprint and district_name and district_name in blueprint.district_characters:
        style = blueprint.district_characters[district_name].get("style")
        if isinstance(style, str) and style.strip():
            return str(style).strip()
    inferred = infer_district_character_from_description(description, elevation=None)
    out = inferred.get("style")
    return str(out).strip() if isinstance(out, str) and out.strip() else None
