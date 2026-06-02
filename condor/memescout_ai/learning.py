"""Conservative learning reports for MemeScout AI paper results."""

from __future__ import annotations

import json
import time

from .store import MemeScoutStore


def _winsorized(values: list[float], cap_multiple: float = 3.0) -> list[float]:
    if not values:
        return []
    abs_values = sorted(abs(v) for v in values)
    median_abs = abs_values[len(abs_values) // 2] or 1.0
    cap = median_abs * cap_multiple
    return [max(-cap, min(cap, v)) for v in values]


def maybe_generate_learning_report(store: MemeScoutStore | None = None) -> str | None:
    store = store or MemeScoutStore()
    if store.bool_state("emergency_stop"):
        return None
    closed = [t for t in store.list_trades(limit=10_000) if t.get("status") == "closed"]
    completed = len(closed)
    if completed < 50 or completed % 50 != 0:
        return None
    if int(store.get_state("last_learning_closed_count", "0") or 0) >= completed:
        return None
    raw_pnl = [float(t.get("realized_pnl") or 0) for t in closed]
    clipped_pnl = _winsorized(raw_pnl)
    clipped_total = sum(clipped_pnl)
    before = store.weights()
    after = before.copy()
    # Conservative phase 1.5: tiny bounded nudges from closed, winsorized outcomes only.
    if clipped_total < 0:
        after["rug_safety"] = after.get("rug_safety", 0.1) + 0.02
        after["liquidity"] = after.get("liquidity", 0.22) + 0.01
        after["price_momentum"] = max(0.01, after.get("price_momentum", 0.12) - 0.01)
    elif completed >= 200 and clipped_total > 0:
        after["volume"] = after.get("volume", 0.20) + 0.005
        after["buy_pressure"] = after.get("buy_pressure", 0.16) + 0.005
    store.update_weights(after)
    after = store.weights()
    live_note = (
        "At least 200 closed paper trades exist; live mode may be reviewed separately."
        if completed >= 200
        else "Live mode must not be considered until at least 200 closed paper trades."
    )
    report = (
        f"MemeScout learning report after {completed} closed paper trades. "
        "Open trades were ignored and PnL was winsorized so one jackpot cannot dominate. "
        f"{live_note}"
    )
    store.set_state("last_learning_closed_count", str(completed))
    with store.connect() as db:
        db.execute(
            "INSERT INTO learning_reports(created_at, trades_reviewed, before_json, after_json, report) VALUES(?,?,?,?,?)",
            (time.time(), completed, json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True), report),
        )
    return f"{report}\nBefore: {before}\nAfter: {after}"
