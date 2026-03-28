"""Save and load world state + chat history to disk."""

import json
import logging
from pathlib import Path

from world.state import WorldState

logger = logging.getLogger("roma.persistence")

SAVE_FILE = Path(__file__).parent / "roma_save.json"
DISTRICTS_CACHE = Path(__file__).parent / "roma_districts_cache.json"


def save_state(world: WorldState, chat_history: list[dict], district_index: int, districts: list[dict] = None):
    data = {
        "district_index": district_index,
        "districts": districts or [],
        "turn": world.turn,
        "current_period": world.current_period,
        "current_year": world.current_year,
        "chat_history": chat_history,
        "tiles": [],
    }
    for y in range(world.height):
        for x in range(world.width):
            tile = world.grid[y][x]
            if tile.terrain != "empty":
                data["tiles"].append(tile.to_dict())

    SAVE_FILE.write_text(json.dumps(data, indent=2))
    logger.info(f"Saved: {len(data['tiles'])} tiles, district #{district_index}, {len(districts or [])} districts")


def save_districts_cache(districts: list[dict], map_description: str = ""):
    data = {"districts": districts, "map_description": map_description}
    DISTRICTS_CACHE.write_text(json.dumps(data, indent=2))
    logger.info(f"Cached {len(districts)} districts")


def load_districts_cache() -> tuple[list[dict], str] | None:
    if not DISTRICTS_CACHE.exists():
        return None
    try:
        data = json.loads(DISTRICTS_CACHE.read_text())
        districts = data.get("districts", [])
        map_desc = data.get("map_description", "")
        if districts:
            logger.info(f"Loaded {len(districts)} cached districts")
            return districts, map_desc
    except Exception as e:
        logger.error(f"Failed to load districts cache: {e}")
    return None


def load_state(world: WorldState) -> tuple[list[dict], int, list[dict]] | None:
    """Returns (chat_history, district_index, districts) or None."""
    if not SAVE_FILE.exists():
        return None

    try:
        data = json.loads(SAVE_FILE.read_text())
        world.turn = data.get("turn", 0)
        world.current_period = data.get("current_period", "")
        world.current_year = data.get("current_year", -44)

        for tile_data in data.get("tiles", []):
            x, y = tile_data.get("x", 0), tile_data.get("y", 0)
            world.place_tile(x, y, tile_data)

        chat_history = data.get("chat_history", [])
        district_index = data.get("district_index", 0)
        districts = data.get("districts", [])

        logger.info(f"Loaded: {len(data['tiles'])} tiles, district #{district_index}, {len(districts)} districts")
        return chat_history, district_index, districts

    except Exception as e:
        logger.error(f"Failed to load save: {e}")
        return None
