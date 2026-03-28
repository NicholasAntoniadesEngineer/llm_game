"""Roma Aeterna — Entry point."""

import asyncio
import logging
import uvicorn

from server.app import app, world, bus, broadcast
from orchestration.engine import BuildEngine

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

BANNER = """
╔══════════════════════════════════════════════════╗
║                                                  ║
║              R O M A   A E T E R N A             ║
║                                                  ║
║     Five AI Agents Build Ancient Rome — Live      ║
║                                                  ║
║          Open: http://localhost:8000              ║
║                                                  ║
╚══════════════════════════════════════════════════╝
"""

engine = BuildEngine(world, bus, broadcast)


@app.on_event("startup")
async def startup():
    print(BANNER)
    asyncio.create_task(engine.run())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
