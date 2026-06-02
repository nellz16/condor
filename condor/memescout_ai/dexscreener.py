"""DEX Screener market-data client for Solana memecoin research."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .settings import get_settings

logger = logging.getLogger(__name__)
BASE = "https://api.dexscreener.com"


class DexScreenerClient:
    """Small async DEX Screener client with conservative request spacing.

    DEX Screener documents 60 requests/minute for token profile endpoints and
    300 requests/minute for pair/search endpoints. MemeScout spaces all requests
    by default at ~1 request/second, keeping the scanner below the stricter
    profile endpoint limit even during manual scans.
    """

    def __init__(self, min_request_interval: float | None = None):
        settings = get_settings()
        self.min_request_interval = (
            settings.dex_request_min_interval_seconds
            if min_request_interval is None
            else min_request_interval
        )
        self._last_request_at = 0.0

    async def latest_solana_pairs(self, limit: int = 20) -> list[dict[str, Any]]:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            token_addresses = await self._latest_solana_token_addresses(session, limit * 2)
            pairs: list[dict[str, Any]] = []
            for token in token_addresses[:limit]:
                data = await self._request_json(session, f"{BASE}/token-pairs/v1/solana/{token}")
                if isinstance(data, list):
                    pairs.extend([p for p in data if p.get("chainId") == "solana"])
            if not pairs:
                pairs = await self._search_solana_pairs(session, "solana meme", limit)
        return sorted(pairs, key=lambda p: safe_float((p.get("volume") or {}).get("h1")), reverse=True)[:limit]

    async def _request_json(self, session: Any, url: str, **kwargs: Any) -> Any:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_request_interval:
            await asyncio.sleep(self.min_request_interval - elapsed)
        self._last_request_at = time.monotonic()
        try:
            async with session.get(url, timeout=15, **kwargs) as resp:
                if resp.status != 200:
                    logger.warning("DEX Screener request returned HTTP %s", resp.status)
                    return None
                return await resp.json()
        except Exception as exc:
            logger.warning("DEX Screener request failed without crashing scanner: %s", exc)
            return None

    async def _latest_solana_token_addresses(self, session: Any, limit: int) -> list[str]:
        data = await self._request_json(session, f"{BASE}/token-profiles/latest/v1")
        tokens = []
        for item in data if isinstance(data, list) else []:
            if item.get("chainId") == "solana" and item.get("tokenAddress"):
                tokens.append(str(item["tokenAddress"]))
            if len(tokens) >= limit:
                break
        return tokens

    async def _search_solana_pairs(self, session: Any, query: str, limit: int) -> list[dict[str, Any]]:
        data = await self._request_json(session, f"{BASE}/latest/dex/search", params={"q": query})
        if not isinstance(data, dict):
            return []
        return [p for p in data.get("pairs", []) if isinstance(p, dict) and p.get("chainId") == "solana"][:limit]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def pair_to_features(pair: dict[str, Any]) -> dict[str, Any]:
    txns = pair.get("txns") or {}
    volume = pair.get("volume") or {}
    price_change = pair.get("priceChange") or {}
    base = pair.get("baseToken") or {}
    created = pair.get("pairCreatedAt")
    age_minutes = 0.0
    if created:
        created_value = safe_float(created)
        if created_value > 0:
            created_s = created_value / 1000 if created_value > 10_000_000_000 else created_value
            age_minutes = max(0.0, (time.time() - created_s) / 60)
    liquidity_usd = safe_float((pair.get("liquidity") or {}).get("usd"))
    price_usd = safe_float(pair.get("priceUsd"))
    return {
        "token_symbol": str(base.get("symbol") or "UNKNOWN"),
        "token_mint": str(base.get("address") or ""),
        "pair_address": str(pair.get("pairAddress") or ""),
        "age_minutes": round(age_minutes, 2),
        "liquidity_usd": liquidity_usd,
        "market_cap": safe_float(pair.get("marketCap") or pair.get("fdv")),
        "volume_5m": safe_float(volume.get("m5")),
        "volume_1h": safe_float(volume.get("h1")),
        "buys_5m": safe_int((txns.get("m5") or {}).get("buys")),
        "sells_5m": safe_int((txns.get("m5") or {}).get("sells")),
        "price_change_5m": safe_float(price_change.get("m5")),
        "price_change_1h": safe_float(price_change.get("h1")),
        "slippage_estimate_bps": estimate_slippage_bps(liquidity_usd),
        "price_usd": price_usd,
        "dex_url": str(pair.get("url") or ""),
    }


def estimate_slippage_bps(liquidity_usd: float) -> int:
    if liquidity_usd <= 0:
        return 1000
    if liquidity_usd < 10_000:
        return 500
    if liquidity_usd < 50_000:
        return 250
    return 150

async def fetch_pair_by_address(pair_address: str) -> dict[str, Any] | None:
    if not pair_address:
        return None
    import aiohttp

    client = DexScreenerClient()
    async with aiohttp.ClientSession() as session:
        data = await client._request_json(session, f"{BASE}/latest/dex/pairs/solana/{pair_address}")
    if isinstance(data, dict):
        pairs = data.get("pairs") or []
        if pairs and isinstance(pairs[0], dict):
            return pairs[0]
        pair = data.get("pair")
        if isinstance(pair, dict):
            return pair
    return None
