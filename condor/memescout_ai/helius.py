"""Optional Helius enrichment helpers.

MemeScout must run without Helius. This module only activates when HELIUS_API_KEY
is configured and failures are treated as missing enrichment.
"""

from __future__ import annotations

import logging
from typing import Any

from .settings import get_settings

logger = logging.getLogger(__name__)


async def get_optional_token_enrichment(token_mint: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.helius_api_key or not token_mint:
        return {}
    try:
        import aiohttp

        url = "https://api.helius.xyz/v0/token-metadata"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                params={"api-key": settings.helius_api_key},
                json={"mintAccounts": [token_mint]},
                timeout=15,
            ) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"Helius returned HTTP {resp.status}")
                data = await resp.json()
        return {"helius_metadata_available": bool(data)}
    except Exception as exc:
        logger.warning("Optional Helius enrichment failed; continuing without it: %s", exc)
        return {}
