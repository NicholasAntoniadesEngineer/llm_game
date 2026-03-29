"""Eternal Cities — Configuration."""

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
    "cartographus": {"name": "Cartographer",  "purpose": "Surveyor & Mapmaker", "color": "#e67e22"},
    "urbanista":    {"name": "Architect",      "purpose": "Master Architect",    "color": "#4a9eff"},
    "historicus":   {"name": "Historian",       "purpose": "Research & Detail",   "color": "#2ecc71"},
}

# ═══════════════════════════════════════════════════
# CITIES — 10 historically rich cities with time windows
# Each has a valid year range and rich context for the AI
# ═══════════════════════════════════════════════════

CITIES = [
    {
        "name": "Rome",
        "year_min": -753, "year_max": 1500,
        "description": "The Eternal City. Capital of the Roman Republic, Roman Empire, and later the Papal States. Seven hills along the Tiber.",
        "features": "Seven hills (Palatine, Capitoline, Aventine, Caelian, Esquiline, Viminal, Quirinal), Tiber River, Forum Romanum, Colosseum, Pantheon",
        "grid_note": "Tiber runs north-south along the western edge. Hills cluster in the center-east.",
    },
    {
        "name": "Athens",
        "year_min": -800, "year_max": 1500,
        "description": "Birthplace of democracy and Western philosophy. Dominated by the Acropolis, surrounded by the Agora and residential quarters.",
        "features": "Acropolis (high rocky outcrop), Agora (central marketplace), Pnyx (assembly hill), Kerameikos (cemetery district), Long Walls to Piraeus",
        "grid_note": "Acropolis is a high flat-topped hill in the center. Agora to the northwest. Residential spreads in all directions.",
    },
    {
        "name": "Constantinople",
        "year_min": 330, "year_max": 1500,
        "description": "Capital of the Eastern Roman/Byzantine Empire. Built on a triangular peninsula between the Golden Horn and Sea of Marmara.",
        "features": "Hagia Sophia, Hippodrome, Great Palace, Theodosian Walls, Golden Horn harbor, Forum of Constantine, Cistern of Philoxenus",
        "grid_note": "Peninsula shape: water on south and north edges, land walls on west. Hills rise from shore.",
    },
    {
        "name": "Alexandria",
        "year_min": -331, "year_max": 1500,
        "description": "Founded by Alexander the Great. Center of Hellenistic learning. Home of the Great Library and Pharos Lighthouse.",
        "features": "Pharos Lighthouse, Great Library/Mouseion, Serapeum, Royal Quarter (Bruchion), Heptastadion causeway, Lake Mareotis",
        "grid_note": "Coastal city on Mediterranean. Harbor to the north. Pharos island connected by causeway. Grid plan by Dinocrates.",
    },
    {
        "name": "Jerusalem",
        "year_min": -1000, "year_max": 1500,
        "description": "Holy city of three faiths. Built on hills with deep valleys. Temple Mount dominates the eastern side.",
        "features": "Temple Mount/Haram al-Sharif, Western Wall, Church of Holy Sepulchre, City of David, Kidron Valley, Mount of Olives",
        "grid_note": "Hilly terrain with steep valleys (Kidron, Tyropoeon). Temple Mount is a large platform on the east. Old City is walled.",
    },
    {
        "name": "Carthage",
        "year_min": -814, "year_max": 200,
        "description": "Great Phoenician/Punic trading city. Rival of Rome. Famous for its circular harbor and Byrsa hill citadel.",
        "features": "Byrsa hill citadel, circular military harbor (cothon), rectangular commercial harbor, Tophet sanctuary, Punic residential quarter",
        "grid_note": "Coastal city on a peninsula. Byrsa hill in center. Twin harbors on the south coast. Residential grid radiates from Byrsa.",
    },
    {
        "name": "Pompeii",
        "year_min": -600, "year_max": 79,
        "description": "Preserved Roman city near Vesuvius. Frozen in time by the 79 CE eruption. Remarkably complete urban plan.",
        "features": "Forum, Amphitheater, Large Palaestra, House of the Faun, Via dell'Abbondanza, Stabian Baths, Temple of Apollo",
        "grid_note": "Walled city with regular grid streets. Forum in the southwest. Amphitheater in the southeast corner. Vesuvius looms to the north.",
    },
    {
        "name": "Baghdad",
        "year_min": 762, "year_max": 1500,
        "description": "The Round City. Capital of the Abbasid Caliphate. Center of the Islamic Golden Age. Built as a perfect circle by al-Mansur.",
        "features": "Round City walls, Palace of the Golden Gate, Grand Mosque, House of Wisdom (Bayt al-Hikma), Tigris River, East Baghdad markets",
        "grid_note": "The Round City is a perfect circle with 4 gates. Tigris runs through the middle. East bank develops later with markets.",
    },
    {
        "name": "Tenochtitlan",
        "year_min": 1325, "year_max": 1521,
        "description": "Aztec island capital in Lake Texcoco. Connected to shore by causeways. Centered on the Templo Mayor pyramid.",
        "features": "Templo Mayor (Great Temple), Sacred Precinct, Tlatelolco market, causeways (north/south/west), chinampas (floating gardens), aqueducts",
        "grid_note": "Island city in a lake. Four causeways lead to shore. Sacred precinct in the center. Canals serve as streets. Chinampas around edges.",
    },
    {
        "name": "Chang'an",
        "year_min": -200, "year_max": 900,
        "description": "Capital of Han and Tang dynasties. Largest city in the world during the Tang. Perfect grid plan with walled wards.",
        "features": "Imperial Palace (north-center), Daming Palace, East/West Markets, Great Wild Goose Pagoda, city wall with gates, ward system",
        "grid_note": "Perfect rectangular grid. Imperial palace complex dominates the north. Symmetrical east-west layout. Walled wards like a chessboard.",
    },
]

# Random city and year selection
city = random.choice(CITIES)
RANDOM_YEAR = random.randint(city["year_min"], min(city["year_max"], 2024))
WINDOW = 50

SCENARIO = {
    "location": city["name"],
    "description": city["description"],
    "features": city["features"],
    "grid_note": city["grid_note"],
    "period": f"around {abs(RANDOM_YEAR)} {'BCE' if RANDOM_YEAR < 0 else 'CE'}",
    "year_start": RANDOM_YEAR - WINDOW // 2,
    "year_end": RANDOM_YEAR + WINDOW // 2,
    "ruler": "Research who ruled and what the city looked like at this exact time",
}
