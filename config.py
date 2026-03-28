"""Roma Aeterna — Configuration."""

import random

# Grid settings
GRID_WIDTH = 40
GRID_HEIGHT = 40

# Agent execution
CLAUDE_MODEL = "haiku"
CLAUDE_MODEL_FAST = "haiku"
STEP_DELAY = 0.3

# Agent display info
AGENTS = {
    "imperator":    {"name": "Imperator",    "purpose": "Project Director",    "color": "#ffd700"},
    "cartographus": {"name": "Cartographus", "purpose": "Surveyor & Mapmaker", "color": "#e67e22"},
    "urbanista":    {"name": "Urbanista",    "purpose": "Master Architect",    "color": "#4a9eff"},
    "historicus":   {"name": "Historicus",   "purpose": "Fact Checker",        "color": "#2ecc71"},
    "faber":        {"name": "Faber",        "purpose": "Master Builder",      "color": "#ff8c00"},
    "civis":        {"name": "Civis",        "purpose": "Life & Culture",      "color": "#e056a0"},
}

# Random year within Rome's history — agents research who ruled and what existed
RANDOM_YEAR = random.randint(-753, 476)  # Kingdom founding to fall of Western Empire
WINDOW = 30  # build a 30-year snapshot around this year

SCENARIO = {
    "location": "Rome",
    "period": f"around {abs(RANDOM_YEAR)} {'BCE' if RANDOM_YEAR < 0 else 'CE'}",
    "year_start": RANDOM_YEAR - WINDOW // 2,
    "year_end": RANDOM_YEAR + WINDOW // 2,
    "ruler": "Research who ruled Rome at this time and what the city looked like",
}
