"""MemeScout AI routine: paper-only Solana memecoin scanner."""

from __future__ import annotations

import asyncio
import json
import inspect
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from condor.memescout_ai.dexscreener import DexScreenerClient, pair_to_features
from condor.memescout_ai.llm import explain_signal, rule_based_explanation
from condor.memescout_ai.paper import auto_paper_buy
from condor.memescout_ai.scoring import SUPPORTED_STRATEGIES, candidate_strategies, score_strategy_signal
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
    pairs_fetched_by_source: dict[str, int] = field(default_factory=dict)
    source_errors: dict[str, str] = field(default_factory=dict)
    candidates_by_strategy: dict[str, int] = field(default_factory=dict)
    eligible_by_strategy: dict[str, int] = field(default_factory=dict)
    rejected_by_strategy: dict[str, int] = field(default_factory=dict)
    duplicate_suppressed_by_strategy: dict[str, int] = field(default_factory=dict)
    top_rejection_reasons: dict[str, int] = field(default_factory=dict)
    close_to_eligibility: list[dict[str, Any]] = field(default_factory=list)
    dex_request_rate_limited: bool = False
    candidate_storage_rate_limited: bool = False
    telegram_signal_rate_limited: bool = False
    candidates_seen_this_hour: int = 0
    candidates_stored_this_hour: int = 0
    telegram_signals_sent_this_hour: int = 0
    candidate_storage_quota_remaining: int = 0
    telegram_signal_quota_remaining: int = 0
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
    _refresh_quota_summary(summary, store, settings)
    if store.bool_state("emergency_stop") or store.bool_state("paused"):
        summary.scanner_error = "scanner paused or emergency stop enabled"
        store.set_state("last_scan_summary", summary.to_json())
        return summary

    if store.counter_value("dex_requests", 60) >= settings.max_dex_requests_per_minute:
        summary.dex_request_rate_limited = True
        store.set_state("last_scan_summary", summary.to_json())
        return summary

    store.increment_counter("dex_requests", 1, 60)
    store.set_state("last_scan_at", str(time.time()))
    client = DexScreenerClient()
    try:
        method = getattr(client, "latest_solana_pairs_by_source", None)
        pairs_by_source = None
        if callable(method):
            maybe_pairs_by_source = method(config.max_pairs_per_scan)
            if inspect.isawaitable(maybe_pairs_by_source):
                pairs_by_source = await maybe_pairs_by_source
            elif isinstance(maybe_pairs_by_source, dict):
                pairs_by_source = maybe_pairs_by_source
        if not isinstance(pairs_by_source, dict):
            pairs_by_source = {"legacy_latest_solana_pairs": await client.latest_solana_pairs(config.max_pairs_per_scan)}
        for source, pairs in pairs_by_source.items():
            summary.pairs_fetched_by_source[source] = len(pairs) if isinstance(pairs, list) else 0
        source_errors = getattr(client, "source_errors", {})
        if isinstance(source_errors, dict):
            summary.source_errors = {str(k): str(v)[:250] for k, v in source_errors.items()}
        summary.pairs_fetched = sum(summary.pairs_fetched_by_source.values())
    except Exception as exc:
        logger.warning("MemeScout DEX Screener scan failed without crashing: %s", exc)
        summary.scanner_error = str(exc)[:250]
        store.set_state("last_scan_error", summary.scanner_error)
        _refresh_quota_summary(summary, store, settings)
        store.set_state("last_scan_summary", summary.to_json())
        return summary

    processed = 0
    for source, pairs in pairs_by_source.items():
        for pair in pairs:
            if processed >= config.max_pairs_per_scan:
                break
            if store.bool_state("emergency_stop") or store.bool_state("paused"):
                summary.scanner_error = "scanner paused or emergency stop enabled"
                break
            processed += 1
            summary.candidates_seen += 1
            store.increment_counter("candidates_seen", 1, 3600)
            pair = {**pair, "source_id": source}
            try:
                features = pair_to_features(pair)
            except Exception as exc:
                logger.warning("MemeScout skipped malformed DEX Screener pair without crashing: %s", exc)
                _record_filtered(summary, {}, {"score": 0, "rug_risk_score": 0, "strategy_id": "rug_defense_only"}, "malformed_pair", str(exc))
                continue
            if not features["token_mint"] or not features["pair_address"]:
                _record_filtered(summary, features, {"score": 0, "rug_risk_score": 0, "strategy_id": "rug_defense_only"}, "malformed_pair", "missing token mint or pair address")
                continue
            if float(features.get("price_usd") or 0) <= 0:
                _record_filtered(summary, features, {"score": 0, "rug_risk_score": 0, "strategy_id": "rug_defense_only"}, "missing_price", "missing price")
                continue

            strategies_to_try = [sid for sid in candidate_strategies(features) if _strategy_enabled(store, sid)]
            if not strategies_to_try:
                continue
            for strategy_id in strategies_to_try[:1]:
                _inc(summary.candidates_by_strategy, strategy_id)
                previous = [s for s in store.seen_strategies(features["token_mint"], features["pair_address"]) if s != strategy_id]
                if store.has_recent_signal(features["token_mint"], features["pair_address"], settings.duplicate_signal_window_seconds, strategy_id=strategy_id):
                    summary.duplicate_suppressed += 1
                    _inc(summary.duplicate_suppressed_by_strategy, strategy_id)
                    continue
                verdict = score_strategy_signal(features, strategy_id, store.weights())
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
                    "strategy_id": strategy_id,
                    "score": verdict["score"],
                    "eligible": verdict["eligible"],
                    "status": "pending" if verdict["eligible"] else "auto_rejected",
                    "reject_reason": verdict.get("reject_reason"),
                    "features": {**features, **verdict, "previous_strategies": previous, "duplicate_cooldown_remaining_seconds": 0},
                    "explanation": explanation,
                }

                signal_id: int | None = None
                candidate_storage_available = store.counter_value("candidates_stored", 3600) < settings.max_candidates_stored_per_hour
                if verdict["eligible"] or candidate_storage_available:
                    signal_id = store.add_signal(signal)
                    store.increment_counter("candidates_stored", 1, 3600)
                    summary.candidates_stored += 1
                else:
                    summary.candidate_storage_rate_limited = True

                if verdict["eligible"]:
                    summary.eligible_count += 1
                    _inc(summary.eligible_by_strategy, strategy_id)
                else:
                    _inc(summary.rejected_by_strategy, strategy_id)
                    _record_filtered(summary, features, verdict, reason, verdict.get("reject_reason") or reason)
                if not verdict["eligible"] and verdict["score"] >= 52:
                    _record_close(summary, features, verdict)

                if signal_id is not None and strategy_id != "rug_defense_only":
                    entry_mode = store.get_state("entry_mode_override", settings.entry_mode)
                    if entry_mode == "auto_paper" and verdict["eligible"]:
                        message = _try_auto_entry(store, signal_id, signal, settings)
                        if message:
                            await send_auto_notification(context, chat_id, signal_id, signal, message)
                    elif entry_mode == "observe_only":
                        if verdict["eligible"] or config.send_rejected:
                            await send_watch_notification(context, chat_id, signal_id, signal)
                    else:
                        should_send = verdict["eligible"] or config.send_rejected
                        if should_send:
                            if store.counter_value("telegram_signals_sent", 3600) >= settings.max_signals_per_hour:
                                summary.telegram_signal_rate_limited = True
                            else:
                                sent = await send_signal(context, chat_id, signal_id, signal)
                                if sent:
                                    summary.telegram_signals_sent += 1
                                    store.increment_counter("telegram_signals_sent", 1, 3600)
        if processed >= config.max_pairs_per_scan:
            break
    if summary.telegram_signals_sent:
        total = int(store.get_state("telegram_signals_sent_total", "0") or 0) + summary.telegram_signals_sent
        store.set_state("telegram_signals_sent_total", str(total))
    _refresh_quota_summary(summary, store, settings)
    store.set_state("last_scan_error", summary.scanner_error or "")
    store.set_state("last_scan_summary", summary.to_json())
    return summary


def _inc(target: dict[str, int], key: str, amount: int = 1) -> None:
    target[key] = target.get(key, 0) + amount


def _strategy_enabled(store: MemeScoutStore, strategy_id: str) -> bool:
    return store.get_state(f"strategy:{strategy_id}:enabled", "true").lower() != "false"


def set_strategy_enabled(store: MemeScoutStore, strategy_id: str, enabled: bool) -> bool:
    if strategy_id not in SUPPORTED_STRATEGIES:
        return False
    store.set_state(f"strategy:{strategy_id}:enabled", "true" if enabled else "false")
    return True


def strategy_status(store: MemeScoutStore, strategy_id: str) -> dict[str, Any] | None:
    if strategy_id not in SUPPORTED_STRATEGIES:
        return None
    return {"strategy_id": strategy_id, "enabled": _strategy_enabled(store, strategy_id)}


def _refresh_quota_summary(summary: ScanSummary, store: MemeScoutStore, settings: Any) -> None:
    summary.candidates_seen_this_hour = store.counter_value("candidates_seen", 3600)
    summary.candidates_stored_this_hour = store.counter_value("candidates_stored", 3600)
    summary.telegram_signals_sent_this_hour = store.counter_value("telegram_signals_sent", 3600)
    summary.candidate_storage_quota_remaining = max(0, settings.max_candidates_stored_per_hour - summary.candidates_stored_this_hour)
    summary.telegram_signal_quota_remaining = max(0, settings.max_signals_per_hour - summary.telegram_signals_sent_this_hour)


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
    _inc(summary.top_rejection_reasons, rejection or reason)
    if len(summary.top_filtered) < 5:
        summary.top_filtered.append({
            "token_symbol": features.get("token_symbol", "UNKNOWN"),
            "pair_address": features.get("pair_address", ""),
            "strategy_id": verdict.get("strategy_id", "unknown"),
            "score": verdict.get("score", 0),
            "rug_risk": verdict.get("rug_risk_score", 0),
            "main_rejection_reason": rejection,
            "liquidity": features.get("liquidity_usd", 0),
            "volume": features.get("volume_5m", 0),
            "age": features.get("age_minutes", 0),
        })


def _record_close(summary: ScanSummary, features: dict[str, Any], verdict: dict[str, Any]) -> None:
    summary.close_to_eligibility.append({
        "token_symbol": features.get("token_symbol", "UNKNOWN"),
        "pair_address": features.get("pair_address", ""),
        "strategy_id": verdict.get("strategy_id", "unknown"),
        "score": verdict.get("score", 0),
        "rug_risk": verdict.get("rug_risk_score", 0),
        "main_rejection_reason": verdict.get("reject_reason") or "below threshold",
    })
    summary.close_to_eligibility = sorted(summary.close_to_eligibility, key=lambda item: float(item.get("score") or 0), reverse=True)[:5]


def _try_auto_entry(store: MemeScoutStore, signal_id: int, signal: dict[str, Any], settings: Any) -> str | None:
    if not settings.paper_only or store.bool_state("emergency_stop") or store.bool_state("paused"):
        return None
    if len(store.list_open_trades()) >= settings.auto_max_open_positions:
        return None
    features = signal.get("features") or {}
    if float(signal.get("score") or 0) < settings.auto_min_score:
        return None
    if float(features.get("rug_risk_score") or 100) > settings.auto_max_rug_risk:
        return None
    if store.has_recent_auto_trade(signal["token_mint"], settings.auto_cooldown_same_token_seconds):
        return None
    return auto_paper_buy(signal_id, store)


async def send_signal(context: Any, chat_id: int | None, signal_id: int, signal: dict[str, Any]) -> bool:
    if not chat_id:
        return False
    features = signal["features"]
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Approve paper buy", callback_data=f"memescout:approve:{signal_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"memescout:reject:{signal_id}"),
        ], [InlineKeyboardButton("🛑 Emergency stop", callback_data="memescout:emergency_stop")]]
    )
    text = format_signal(signal_id, signal, features)
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, disable_web_page_preview=True)
    return True


async def send_watch_notification(context: Any, chat_id: int | None, signal_id: int, signal: dict[str, Any]) -> bool:
    if not chat_id:
        return False
    text = format_signal(signal_id, signal, signal["features"]).replace("ELIGIBLE FOR PAPER APPROVAL", "OBSERVE ONLY WATCHLIST")
    await context.bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
    return True


async def send_auto_notification(context: Any, chat_id: int | None, signal_id: int, signal: dict[str, Any], result: str) -> bool:
    if not chat_id:
        return False
    f = signal["features"]
    settings = get_settings()
    text = (
        f"🤖 AUTO PAPER BUY opened\n"
        f"Signal #{signal_id} / {signal['token_symbol']} / strategy={signal.get('strategy_id')}\n"
        f"{result}\n"
        f"Score: {signal.get('score')} | Rug risk: {f.get('rug_risk_score')}/100\n"
        f"Entry price source: ${float(f.get('price_usd') or 0):.8f} | Size: ${settings.auto_trade_size_usdc:.2f} paper USDC\n"
        f"Stoploss: {settings.stop_loss_pct:.0f}% | TP: 50% at 2x, 25% at 4x, trailing rest\n"
        f"Reason: {f.get('strategy_match_reason', 'eligible deterministic signal')}\n"
        "Paper-only. No wallet or real order was used."
    )
    await context.bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
    return True


def format_signal(signal_id: int, signal: dict[str, Any], f: dict[str, Any]) -> str:
    eligible = "ELIGIBLE FOR PAPER APPROVAL" if signal["eligible"] else "AUTO-REJECTED"
    return (
        f"🧭 MemeScout AI Signal #{signal_id} ({eligible})\n\n"
        f"Strategy: {signal.get('strategy_id', 'unknown')}\n"
        f"Why this strategy matched: {f.get('strategy_match_reason', 'rule-based strategy match')}\n"
        f"Main risk: {f.get('main_risk', 'memecoin risk')}\n"
        f"Main reason it can fail: {f.get('main_failure_reason', 'market can reverse quickly')}\n"
        f"Previously seen under other strategies: {', '.join(f.get('previous_strategies') or []) or 'no'}\n"
        f"Duplicate cooldown remaining: {f.get('duplicate_cooldown_remaining_seconds', 0)} seconds\n\n"
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
