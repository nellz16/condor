"""MemeScout AI routine: paper-only Solana memecoin scanner."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
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


FILTER_KEYS = (
    "low_liquidity",
    "low_score",
    "high_rug_risk",
    "malformed_pair",
    "missing_price",
    "too_old",
    "too_new",
    "other",
)


@dataclass
class ScanSummary:
    pairs_fetched: int = 0
    candidates_seen: int = 0
    candidates_stored: int = 0
    eligible_count: int = 0
    telegram_signals_sent: int = 0
    duplicate_suppressed: int = 0
    rate_limited: bool = False
    filtered_by_reason: dict[str, int] = field(default_factory=lambda: {k: 0 for k in FILTER_KEYS})
    scanner_error: str | None = None
    top_filtered: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | None) -> "ScanSummary":
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
            summary = cls()
            for key, value in data.items():
                setattr(summary, key, value)
            for key in FILTER_KEYS:
                summary.filtered_by_reason.setdefault(key, 0)
            return summary
        except Exception:
            return cls(scanner_error="could not parse last scan summary")


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
            summary = await scan_once_summary(config, context, store, chat_id)
            sent = summary.telegram_signals_sent
            scans += 1
            logger.info("MemeScout scan %s complete; sent %s signals", scans, sent)
        await asyncio.sleep(max(60, config.interval_seconds or settings.scan_interval_seconds))


async def scan_once(config: Config, context: Any, store: MemeScoutStore | None = None, chat_id: int | None = None) -> int:
    summary = await scan_once_summary(config, context, store, chat_id)
    return summary.telegram_signals_sent


async def scan_once_summary(config: Config, context: Any, store: MemeScoutStore | None = None, chat_id: int | None = None) -> ScanSummary:
    store = store or MemeScoutStore()
    settings = get_settings()
    summary = ScanSummary()
    if store.bool_state("emergency_stop") or store.bool_state("paused"):
        summary.scanner_error = "scanner paused or emergency stop enabled"
        store.set_state("last_scan_summary", summary.to_json())
        return summary
    if store.recent_signal_count(3600) >= settings.max_signals_per_hour:
        logger.info("MemeScout rate limit reached: %s signals/hour", settings.max_signals_per_hour)
        summary.rate_limited = True
        store.set_state("last_scan_summary", summary.to_json())
        return summary

    store.set_state("last_scan_at", str(time.time()))
    client = DexScreenerClient()
    try:
        pairs = await client.latest_solana_pairs(config.max_pairs_per_scan)
        summary.pairs_fetched = len(pairs)
    except Exception as exc:
        logger.warning("MemeScout DEX Screener scan failed without crashing: %s", exc)
        summary.scanner_error = str(exc)[:250]
        store.set_state("last_scan_error", summary.scanner_error)
        store.set_state("last_scan_summary", summary.to_json())
        return summary
    for pair in pairs:
        if store.recent_signal_count(3600) >= settings.max_signals_per_hour:
            summary.rate_limited = True
            break
        if store.bool_state("emergency_stop") or store.bool_state("paused"):
            summary.scanner_error = "scanner paused or emergency stop enabled"
            break
        summary.candidates_seen += 1
        try:
            features = pair_to_features(pair)
        except Exception as exc:
            logger.warning("MemeScout skipped malformed DEX Screener pair without crashing: %s", exc)
            _record_filtered(summary, {}, {"score": 0, "rug_risk_score": 0}, "malformed_pair", str(exc))
            continue
        if not features["token_mint"] or not features["pair_address"]:
            _record_filtered(summary, features, {"score": 0, "rug_risk_score": 0}, "malformed_pair", "missing token mint or pair address")
            continue
        if float(features.get("price_usd") or 0) <= 0:
            _record_filtered(summary, features, {"score": 0, "rug_risk_score": 0}, "missing_price", "missing price")
            continue
        if store.has_recent_signal(features["token_mint"], features["pair_address"], settings.duplicate_signal_window_seconds):
            summary.duplicate_suppressed += 1
            continue
        verdict = score_signal(features, store.weights())
        reason = _main_filter_reason(features, verdict)
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
        summary.candidates_stored += 1
        if verdict["eligible"]:
            summary.eligible_count += 1
        else:
            _record_filtered(summary, features, verdict, reason, verdict.get("reject_reason") or reason)
        if verdict["eligible"] or config.send_rejected:
            await send_signal(context, chat_id, signal_id, signal)
            summary.telegram_signals_sent += 1
    if summary.telegram_signals_sent:
        total = int(store.get_state("telegram_signals_sent_total", "0") or 0) + summary.telegram_signals_sent
        store.set_state("telegram_signals_sent_total", str(total))
    store.set_state("last_scan_error", summary.scanner_error or "")
    store.set_state("last_scan_summary", summary.to_json())
    return summary


def _main_filter_reason(features: dict[str, Any], verdict: dict[str, Any]) -> str:
    reject = (verdict.get("reject_reason") or "").lower()
    if "liquidity" in reject or float(features.get("liquidity_usd") or 0) < 10_000:
        return "low_liquidity"
    if "rug" in reject or float(verdict.get("rug_risk_score") or 0) >= 70:
        return "high_rug_risk"
    if not verdict.get("eligible"):
        return "low_score"
    return "other"


def _record_filtered(summary: ScanSummary, features: dict[str, Any], verdict: dict[str, Any], reason: str, rejection: str) -> None:
    if reason not in summary.filtered_by_reason:
        reason = "other"
    summary.filtered_by_reason[reason] += 1
    if len(summary.top_filtered) < 5:
        summary.top_filtered.append({
            "token_symbol": features.get("token_symbol", "UNKNOWN"),
            "pair_address": features.get("pair_address", ""),
            "score": verdict.get("score", 0),
            "rug_risk": verdict.get("rug_risk_score", 0),
            "main_rejection_reason": rejection,
            "liquidity": features.get("liquidity_usd", 0),
            "volume": features.get("volume_5m", 0),
            "age": features.get("age_minutes", 0),
        })


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
