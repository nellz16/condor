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
            results["errors"] += 1
            continue

        before_exits = len(store.list_exits(int(trade["id"])))
        updated = store.update_trade_mark(int(trade["id"]), price)
        results["updated"] += 1
        if emergency_blocks_closes:
            continue
        if updated:
            await apply_exit_rules(store, updated, price)
            after_exits = len(store.list_exits(int(trade["id"])))
            results["exits"] += max(0, after_exits - before_exits)
    if results["errors"] == 0:
        store.set_state("last_monitor_error", "")
    maybe_generate_learning_report(store)
    return results


async def apply_exit_rules(store: MemeScoutStore, trade: dict[str, Any], price: float) -> None:
    entry = float(trade["entry_price"])
    if entry <= 0 or price <= 0:
        return
    original_qty = float(trade["quantity"])
    remaining_qty = float(trade.get("remaining_quantity") or 0)
    if remaining_qty <= 0:
        return
    plan = trade.get("plan") or {}
    stop_loss_pct = abs(float(plan.get("stop_loss_pct", get_settings().stop_loss_pct)))
    trailing_pct = float(plan.get("trailing_stop_pct", get_settings().trailing_stop_pct))

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
