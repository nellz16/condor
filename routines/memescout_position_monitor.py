"""MemeScout AI paper-only position monitor."""

from __future__ import annotations

from pydantic import BaseModel, Field

from condor.memescout_ai.monitor import monitor_loop

CONTINUOUS = True
CATEGORY = "MemeScout AI"


class Config(BaseModel):
    """Paper-only monitor for MemeScout positions and exits."""

    enabled: bool = Field(default=True, description="Run the paper monitor loop")


async def run(config: Config, context) -> str:
    if not config.enabled:
        return "MemeScout paper monitor disabled"
    await monitor_loop(context)
    return "MemeScout paper monitor stopped"
