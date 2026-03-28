"""Roma Aeterna — Configuration."""

# Grid settings
GRID_WIDTH = 40
GRID_HEIGHT = 40

# Agent execution
CLAUDE_MODEL = "sonnet"
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

# Initial scenario — everything else is discovered by agents
SCENARIO = {
    "location": "Rome",
    "period": "Late Roman Republic to Early Empire",
    "year_start": -50,
    "year_end": 100,
    "ruler": "From Julius Caesar through the Julio-Claudian dynasty",
}
