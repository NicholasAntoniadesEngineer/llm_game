"""Vocabulary for society JSON schema validation (periods, orders, complexity).

Not grid/runtime parameters — those live in data/system_config.csv via Config.
"""

from typing import Final

HISTORICAL_PERIODS: Final[dict[str, tuple[int, int]]] = {
    "ancient": (-3000, 500),
    "classical": (-500, 500),
    "medieval": (500, 1500),
    "renaissance": (1400, 1700),
    "industrial": (1700, 1900),
    "modern": (1900, 2100),
}

COLUMN_ORDERS: Final[list[str]] = ["doric", "ionic", "corinthian", "tuscan", "composite"]
