"""Paper-only live position monitor for MemeScout AI."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .dexscreener import fetch_pair_by_address, pair_to_features
from .learning import maybe_generate_learning_report
from .settings import get_settings
from .store import MemeScoutStore

logger = logging.getLogger(__name__)


async def monitor_loop(context: Any | None = None, store: MemeScoutStore | None = None) -> None:
    store = store or MemeScoutStore()
    while True:
        settings = get_settings()
        if settings.monitor_enabled:
            await monitor_once(store)
        await asyncio.sleep(max(10, settings.monitor_interval_seconds))


async def monitor_once(store: MemeScoutStore | None = None) -> dict[str, int]:
    store = store or MemeScoutStore()
    settings = get_settings()
    results = {"checked": 0, "updated": 0, "errors": 0, "exits": 0}
    emergency_blocks_closes = (
        store.bool_state("emergency_stop")
        and not settings.allow_risk_reducing_closes_during_emergency
    )
    import time

    store.set_state("last_monitor_at", str(time.time()))
    for trade in store.list_open_trades():
        results["checked"] += 1
        pair_address = trade.get("pair_address") or ""
        if not pair_address:
            store.mark_monitor_error(int(trade["id"]), "missing pair address")
            results["errors"] += 1
            continue
        try:
            pair = await fetch_pair_by_address(pair_address)
            if not isinstance(pair, dict):
                raise ValueError("DEX Screener returned no pair")
            features = pair_to_features(pair)
            price = float(features.get("price_usd") or 0)
            if price <= 0:
                raise ValueError("DEX Screener returned invalid price")
        except Exception as exc:
            logger.warning("MemeScout monitor skipped trade %s without crashing: %s", trade.get("id"), exc)
            store.mark_monitor_error(int(trade["id"]), f"price fetch failed: {exc}")
            store.set_state("last_monitor_error", str(exc)[:250])
            _handle_stale_price(store, trade)
            results["errors"] += 1
            continue

        before_exits = len(store.list_exits(int(trade["id"])))
        updated = store.update_trade_mark(int(trade["id"]), price)
        results["updated"] += 1
        if emergency_blocks_closes:
            continue
        if updated and store.get_state("exit_mode_override", settings.exit_mode) == "auto":
            await apply_exit_rules(store, updated, price, features)
            after_exits = len(store.list_exits(int(trade["id"])))
            results["exits"] += max(0, after_exits - before_exits)
    if results["errors"] == 0:
        store.set_state("last_monitor_error", "")
    maybe_generate_learning_report(store)
    return results


def _handle_stale_price(store: MemeScoutStore, trade: dict[str, Any]) -> None:
    settings = get_settings()
    import time
    last_update = float(trade.get("last_monitor_update_at") or trade.get("opened_at") or time.time())
    stale_minutes = (time.time() - last_update) / 60
    if stale_minutes < settings.stale_price_exit_minutes:
        return
    if settings.stale_price_action == "close":
        price = float(trade.get("current_price") or trade.get("entry_price") or 0)
        qty = float(trade.get("remaining_quantity") or 0)
        if price > 0 and qty > 0:
            store.record_exit(int(trade["id"]), price, qty, "stale_price")
    else:
        store.mark_monitor_error(int(trade["id"]), "stale_price")


async def apply_exit_rules(store: MemeScoutStore, trade: dict[str, Any], price: float, features: dict[str, Any] | None = None) -> None:
    entry = float(trade["entry_price"])
    if entry <= 0 or price <= 0:
        return
    original_qty = float(trade["quantity"])
    remaining_qty = float(trade.get("remaining_quantity") or 0)
    if remaining_qty <= 0:
        return
    plan = trade.get("plan") or {}
    stop_loss_pct = abs(float(plan.get("stop_loss_pct", get_settings().stop_loss_pct)))
    settings = get_settings()
    trailing_pct = float(plan.get("trailing_stop_pct", settings.trailing_stop_pct))
    features = features or {}

    opened_at = float(trade.get("opened_at") or 0)
    import time
    age_minutes = max(0, (time.time() - opened_at) / 60) if opened_at else 0
    if age_minutes >= settings.max_hold_minutes and not int(trade.get("tp1_triggered") or 0):
        store.record_exit(int(trade["id"]), price, remaining_qty, "max_hold_time")
        return

    if settings.momentum_decay_exit:
        buys = float(features.get("buys_5m") or 0)
        sells = float(features.get("sells_5m") or 0)
        if float(features.get("price_change_5m") or 0) < 0 and sells > buys * 1.2 and sells >= 5:
            store.record_exit(int(trade["id"]), price, remaining_qty, "momentum_decay")
            return

    if settings.liquidity_drop_exit:
        entry_liq = float((trade.get("entry_features") or {}).get("liquidity_usd") or 0)
        current_liq = float(features.get("liquidity_usd") or 0)
        if entry_liq > 0 and current_liq > 0 and current_liq <= entry_liq * (1 - settings.liquidity_drop_exit_pct / 100):
            store.record_exit(int(trade["id"]), price, remaining_qty, "liquidity_drop")
            return

    if settings.sell_pressure_exit:
        buys = float(features.get("buys_5m") or 0)
        sells = float(features.get("sells_5m") or 0)
        if sells >= max(5, buys * settings.sell_pressure_ratio_exit):
            store.record_exit(int(trade["id"]), price, remaining_qty, "sell_pressure")
            return

    if price <= entry * (1 - stop_loss_pct / 100):
        store.record_exit(int(trade["id"]), price, remaining_qty, "stoploss")
        return

    if not int(trade.get("tp1_triggered") or 0) and price >= entry * 2:
        qty = min(original_qty * 0.50, remaining_qty)
        store.record_exit(int(trade["id"]), price, qty, "tp1", close_if_empty=False)
        trade = store.get_trade(int(trade["id"])) or trade
        remaining_qty = float(trade.get("remaining_quantity") or 0)

    if remaining_qty > 0 and not int(trade.get("tp2_triggered") or 0) and price >= entry * 4:
        qty = min(original_qty * 0.25, remaining_qty)
        store.record_exit(int(trade["id"]), price, qty, "tp2", close_if_empty=False)
        trade = store.get_trade(int(trade["id"])) or trade
        remaining_qty = float(trade.get("remaining_quantity") or 0)

    if remaining_qty <= 0 or int(trade.get("trailing_stop_triggered") or 0):
        return
    highest = float(trade.get("highest_price") or price)
    trailing_active = highest >= entry * 2
    if trailing_active and price <= highest * (1 - trailing_pct / 100):
        store.record_exit(int(trade["id"]), price, remaining_qty, "trailing_stop")
