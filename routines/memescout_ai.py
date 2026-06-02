"""MemeScout AI routine: paper-only Solana memecoin scanner."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, Field
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from condor.memescout_ai.dexscreener import DexScreenerClient, pair_to_features
from condor.memescout_ai.llm import explain_signal, rule_based_explanation
from condor.memescout_ai.scoring import score_signal
from condor.memescout_ai.settings import get_settings
from condor.memescout_ai.store import MemeScoutStore

logger = logging.getLogger(__name__)
CONTINUOUS = True
CATEGORY = "MemeScout AI"


class Config(BaseModel):
    """Beginner-safe Solana memecoin research scanner (paper-only)."""

    interval_seconds: int = Field(default=300, ge=60, description="Seconds between scans")
    max_pairs_per_scan: int = Field(default=10, ge=1, le=30, description="DEX Screener pairs to inspect")
    send_rejected: bool = Field(default=False, description="Also send rejected tokens to Telegram")


async def run(config: Config, context: Any) -> str:
    store = MemeScoutStore()
    settings = get_settings()
    chat_id = getattr(context, "_chat_id", None)
    scans = 0
    while True:
        if store.bool_state("emergency_stop"):
            logger.info("MemeScout emergency stop is enabled; scanner sleeping")
        elif not store.bool_state("paused"):
            sent = await scan_once(config, context, store, chat_id)
            scans += 1
            logger.info("MemeScout scan %s complete; sent %s signals", scans, sent)
        await asyncio.sleep(max(60, config.interval_seconds or settings.scan_interval_seconds))


async def scan_once(config: Config, context: Any, store: MemeScoutStore | None = None, chat_id: int | None = None) -> int:
    store = store or MemeScoutStore()
    settings = get_settings()
    if store.bool_state("emergency_stop") or store.bool_state("paused"):
        return 0
    if store.recent_signal_count(3600) >= settings.max_signals_per_hour:
        logger.info("MemeScout rate limit reached: %s signals/hour", settings.max_signals_per_hour)
        return 0

    client = DexScreenerClient()
    try:
        pairs = await client.latest_solana_pairs(config.max_pairs_per_scan)
    except Exception as exc:
        logger.warning("MemeScout DEX Screener scan failed without crashing: %s", exc)
        return 0
    sent = 0
    for pair in pairs:
        if store.recent_signal_count(3600) >= settings.max_signals_per_hour:
            break
        if store.bool_state("emergency_stop") or store.bool_state("paused"):
            break
        try:
            features = pair_to_features(pair)
        except Exception as exc:
            logger.warning("MemeScout skipped malformed DEX Screener pair without crashing: %s", exc)
            continue
        if not features["token_mint"] or not features["pair_address"]:
            continue
        if store.has_recent_signal(features["token_mint"], features["pair_address"], settings.duplicate_signal_window_seconds):
            continue
        verdict = score_signal(features, store.weights())
        explanation = (
            await explain_signal(features, verdict, settings)
            if verdict["eligible"]
            else rule_based_explanation(features, verdict)
        )
        signal = {
            "token_symbol": features["token_symbol"],
            "token_mint": features["token_mint"],
            "pair_address": features["pair_address"],
            "score": verdict["score"],
            "eligible": verdict["eligible"],
            "status": "pending" if verdict["eligible"] else "auto_rejected",
            "reject_reason": verdict.get("reject_reason"),
            "features": {**features, **verdict},
            "explanation": explanation,
        }
        signal_id = store.add_signal(signal)
        if verdict["eligible"] or config.send_rejected:
            await send_signal(context, chat_id, signal_id, signal)
            sent += 1
    return sent


async def send_signal(context: Any, chat_id: int | None, signal_id: int, signal: dict[str, Any]) -> None:
    if not chat_id:
        return
    features = signal["features"]
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Approve paper buy", callback_data=f"memescout:approve:{signal_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"memescout:reject:{signal_id}"),
        ], [InlineKeyboardButton("🛑 Emergency stop", callback_data="memescout:emergency_stop")]]
    )
    text = format_signal(signal_id, signal, features)
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, disable_web_page_preview=True)


def format_signal(signal_id: int, signal: dict[str, Any], f: dict[str, Any]) -> str:
    eligible = "ELIGIBLE FOR PAPER APPROVAL" if signal["eligible"] else "AUTO-REJECTED"
    return (
        f"🧭 MemeScout AI Signal #{signal_id} ({eligible})\n\n"
        f"Token: {signal['token_symbol']}\n"
        f"Mint: {signal['token_mint']}\n"
        f"Pair: {signal['pair_address']}\n"
        f"Age: {f.get('age_minutes')} min\n"
        f"Liquidity: ${f.get('liquidity_usd'):,.0f}\n"
        f"Market cap: ${f.get('market_cap'):,.0f}\n"
        f"Volume: 5m ${f.get('volume_5m'):,.0f} / 1h ${f.get('volume_1h'):,.0f}\n"
        f"Buys/Sells 5m: {f.get('buys_5m')} / {f.get('sells_5m')}\n"
        f"Price change: 5m {f.get('price_change_5m')}% / 1h {f.get('price_change_1h')}%\n"
        f"Slippage estimate: {f.get('slippage_estimate_bps')} bps\n"
        f"Rug risk: {f.get('rug_risk_score')}/100\n"
        f"Graduation probability: {f.get('graduation_probability_score')}/100\n"
        f"Expected upside: {f.get('expected_upside_range')}\n"
        "Max loss plan: stop at -35% (paper simulation).\n"
        "Take profit plan: sell 50% at 2x, 25% at 4x, trail the rest.\n\n"
        f"Explanation: {signal['explanation']}\n\n"
        "Paper-only. No private key, exchange key, or real order is used. Profit is not guaranteed."
    )
