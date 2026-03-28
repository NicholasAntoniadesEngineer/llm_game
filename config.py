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

# Randomised time periods for variety on each fresh start
ERAS = [
    {"period": "Roman Kingdom", "year_start": -753, "year_end": -509,
     "ruler": "The legendary kings: Romulus, Numa Pompilius, Servius Tullius, Tarquinius Superbus"},
    {"period": "Early Roman Republic", "year_start": -509, "year_end": -264,
     "ruler": "The early Republic — consuls, tribunes, and the Twelve Tables"},
    {"period": "Middle Roman Republic", "year_start": -264, "year_end": -133,
     "ruler": "The Punic Wars era — Scipio Africanus, Cato the Elder"},
    {"period": "Late Roman Republic", "year_start": -133, "year_end": -27,
     "ruler": "From the Gracchi through Sulla, Pompey, Julius Caesar, and the civil wars"},
    {"period": "Augustan Rome", "year_start": -27, "year_end": 14,
     "ruler": "Augustus — the first emperor, who found Rome in brick and left it in marble"},
    {"period": "Julio-Claudian Dynasty", "year_start": 14, "year_end": 68,
     "ruler": "Tiberius, Caligula, Claudius, and Nero"},
    {"period": "Flavian Dynasty", "year_start": 69, "year_end": 96,
     "ruler": "Vespasian (built the Colosseum), Titus, and Domitian"},
    {"period": "Age of the Five Good Emperors", "year_start": 96, "year_end": 180,
     "ruler": "Nerva, Trajan, Hadrian, Antoninus Pius, Marcus Aurelius — Rome's golden age"},
    {"period": "Severan Dynasty", "year_start": 193, "year_end": 235,
     "ruler": "Septimius Severus, Caracalla (built the great baths)"},
    {"period": "Late Empire", "year_start": 284, "year_end": 395,
     "ruler": "Diocletian's reforms, Constantine the Great, and the move to Constantinople"},
]

era = random.choice(ERAS)

SCENARIO = {
    "location": "Rome",
    "period": era["period"],
    "year_start": era["year_start"],
    "year_end": era["year_end"],
    "ruler": era["ruler"],
}
