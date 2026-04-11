"""Eternal Cities — Constants and Configuration Values.

Centralized constants to eliminate magic numbers and improve maintainability.
All constants are organized by functional area.
"""

from typing import Final

# ═══════════════════════════════════════════════════════════════════════════════
# GRID AND WORLD CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Grid dimensions (tiles)
GRID_WIDTH: Final[int] = 320
GRID_HEIGHT: Final[int] = 320

# World scale (meters per tile)
WORLD_SCALE_METERS: Final[float] = 10.0

# Elevation scaling
ELEVATION_SCALE: Final[float] = 0.5
MAX_ELEVATION: Final[float] = 50.0

# ═══════════════════════════════════════════════════════════════════════════════
# BUILDING AND ARCHITECTURE CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Building size limits
MAX_BUILDING_HEIGHT: Final[float] = 25.0  # meters
MIN_BUILDING_HEIGHT: Final[float] = 2.0   # meters

# Column proportions (diameter to height ratios)
DORIC_RATIO: Final[float] = 1.0 / 8.0
IONIC_RATIO: Final[float] = 1.0 / 9.0
CORINTHIAN_RATIO: Final[float] = 1.0 / 10.0
COMPOSITE_RATIO: Final[float] = 1.0 / 9.5

# Architectural orders
COLUMN_ORDERS: Final[list[str]] = ["doric", "ionic", "corinthian", "tuscan", "composite"]

# Roof pitch angles (degrees)
FLAT_ROOF_PITCH: Final[float] = 0.0
GENTLE_ROOF_PITCH: Final[float] = 15.0
STEEP_ROOF_PITCH: Final[float] = 45.0

# ═══════════════════════════════════════════════════════════════════════════════
# CULTURAL AND HISTORICAL CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Historical periods (year ranges)
HISTORICAL_PERIODS: Final[dict[str, tuple[int, int]]] = {
    "ancient": (-3000, 500),
    "classical": (-500, 500),
    "medieval": (500, 1500),
    "renaissance": (1400, 1700),
    "industrial": (1700, 1900),
    "modern": (1900, 2100),
}

# Cultural complexity levels
CULTURAL_COMPLEXITY: Final[dict[str, int]] = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "very_high": 4,
}

# ═══════════════════════════════════════════════════════════════════════════════
# AGENT AND AI CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Agent timeouts (seconds)
AGENT_TIMEOUT_SHORT: Final[int] = 30
AGENT_TIMEOUT_MEDIUM: Final[int] = 120
AGENT_TIMEOUT_LONG: Final[int] = 300

# Token limits
MAX_PROMPT_TOKENS: Final[int] = 4000
MAX_RESPONSE_TOKENS: Final[int] = 2000
TOKEN_BUFFER: Final[int] = 500

# Retry configuration
MAX_RETRIES: Final[int] = 3
RETRY_BACKOFF_BASE: Final[float] = 2.0
RETRY_JITTER: Final[float] = 0.1

# ═══════════════════════════════════════════════════════════════════════════════
# PERFORMANCE AND SCALING CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Concurrency limits
MAX_CONCURRENT_URBANISTA: Final[int] = 5
MAX_CONCURRENT_SURVEY: Final[int] = 3
MAX_CONCURRENT_CARTOGRAPHUS: Final[int] = 2

# Batch processing
MAX_BATCH_SIZE: Final[int] = 3
MAX_BATCH_TILES: Final[int] = 12

# Memory management
MAX_CHAT_HISTORY: Final[int] = 500
MAX_DISTRICT_CACHE: Final[int] = 50

# ═══════════════════════════════════════════════════════════════════════════════
# FILE AND DATA CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# File extensions
SOCIETY_FILE_EXTENSION: Final[str] = ".society.json"
BLUEPRINT_FILE_EXTENSION: Final[str] = ".blueprint.json"
SAVE_FILE_EXTENSION: Final[str] = ".json"

# Directory names
DATA_DIR: Final[str] = "data"
SOCIETIES_DIR: Final[str] = "societies"
STATIC_DIR: Final[str] = "static"
TEMPLATES_DIR: Final[str] = "templates"

# ═══════════════════════════════════════════════════════════════════════════════
# TIME AND TIMING CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Time windows (seconds)
STEP_DELAY: Final[float] = 0.3
EXPANSION_COOLDOWN: Final[int] = 10
SAVE_INTERVAL: Final[int] = 60

# Timeline configuration
TIMELINE_WINDOW: Final[int] = 50  # years around focus year

# ═══════════════════════════════════════════════════════════════════════════════
# UI AND DISPLAY CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Colors (hex codes)
AGENT_COLORS: Final[dict[str, str]] = {
    "cartographus": "#e67e22",
    "urbanista": "#4a9eff",
}

# Status indicators
AGENT_STATUS_IDLE: Final[str] = "idle"
AGENT_STATUS_ACTIVE: Final[str] = "active"
AGENT_STATUS_ERROR: Final[str] = "error"

# ═══════════════════════════════════════════════════════════════════════════════
# MATERIAL AND VISUAL CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Material properties
MATERIAL_ROUGHNESS_LOW: Final[float] = 0.1
MATERIAL_ROUGHNESS_MEDIUM: Final[float] = 0.5
MATERIAL_ROUGHNESS_HIGH: Final[float] = 0.9

MATERIAL_METALNESS_LOW: Final[float] = 0.0
MATERIAL_METALNESS_MEDIUM: Final[float] = 0.3
MATERIAL_METALNESS_HIGH: Final[float] = 0.8

# Color palettes
WARM_MATERIALS: Final[set[str]] = {"brick", "terracotta", "wood", "ochre", "adobe", "mud", "thatch", "coral"}
COOL_MATERIALS: Final[set[str]] = {"marble", "travertine", "limestone", "granite", "concrete", "tufa", "slate"}

# ═══════════════════════════════════════════════════════════════════════════════
# TERRAIN AND ENVIRONMENT CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Terrain types
OPEN_TERRAIN_TYPES: Final[set[str]] = frozenset({
    "road", "forum", "garden", "water", "grass"
})

# Wave building phases
WAVE1_BUILDING_TYPES: Final[set[str]] = frozenset({
    "temple", "basilica", "gate", "wall", "monument", "amphitheater",
    "thermae", "circus", "bridge", "aqueduct",
    "road", "forum", "garden", "water", "grass",
})

# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION AND CONSTRAINTS
# ═══════════════════════════════════════════════════════════════════════════════

# File size limits (bytes)
MAX_SOCIETY_FILE_SIZE: Final[int] = 1024 * 1024  # 1MB
MAX_BLUEPRINT_FILE_SIZE: Final[int] = 10 * 1024 * 1024  # 10MB

# Name length limits
MAX_CITY_NAME_LENGTH: Final[int] = 100
MAX_BUILDING_NAME_LENGTH: Final[int] = 200
MAX_DISTRICT_NAME_LENGTH: Final[int] = 150

# Coordinate limits
MAX_COORDINATE_VALUE: Final[int] = 10000
MIN_COORDINATE_VALUE: Final[int] = -10000

# ═══════════════════════════════════════════════════════════════════════════════
# EXTERNAL API CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# HTTP timeouts
HTTP_TIMEOUT_SHORT: Final[int] = 5
HTTP_TIMEOUT_MEDIUM: Final[int] = 30
HTTP_TIMEOUT_LONG: Final[int] = 120

# Rate limiting
RATE_LIMIT_REQUESTS: Final[int] = 100
RATE_LIMIT_WINDOW: Final[int] = 60  # seconds

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING AND MONITORING
# ═══════════════════════════════════════════════════════════════════════════════

# Log levels
LOG_LEVEL_DEBUG: Final[str] = "DEBUG"
LOG_LEVEL_INFO: Final[str] = "INFO"
LOG_LEVEL_WARNING: Final[str] = "WARNING"
LOG_LEVEL_ERROR: Final[str] = "ERROR"

# Performance thresholds
PERFORMANCE_WARNING_THRESHOLD: Final[float] = 5.0  # seconds
PERFORMANCE_ERROR_THRESHOLD: Final[float] = 30.0   # seconds