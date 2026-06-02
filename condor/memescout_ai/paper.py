"""Paper wallet execution for MemeScout AI. No real orders are placed."""

from __future__ import annotations

from .settings import get_settings
from .store import MemeScoutStore


def approve_paper_buy(signal_id: int, store: MemeScoutStore | None = None, entry_mode: str = "manual_approval", size_override: float | None = None, auto_entry_reason: str | None = None) -> str:
    store = store or MemeScoutStore()
    settings = get_settings()
    if not settings.paper_only:
        return "MemeScout live trading is not implemented. Paper-only enforcement blocked this action."
    if store.bool_state("emergency_stop"):
        return "🛑 Emergency stop is ON. No paper trade opened."
    signal = store.get_signal(signal_id)
    if not signal:
        return "Signal not found."
    if signal["status"] == "approved":
        return "Signal was already approved."
    if not signal["eligible"]:
        store.set_signal_status(signal_id, "rejected", "not eligible after deterministic scoring")
        return "Rejected: deterministic safety score says this token is not eligible."

    features = signal["features"]
    balance = float(store.get_state("paper_balance_usdc", str(settings.default_balance_usdc)))
    requested_size = size_override if size_override is not None else settings.trade_size_usdc
    size = min(requested_size, balance)
    if size <= 0:
        return "Paper wallet has no USDC left."
    raw_price = float(features.get("price_usd") or 0)
    if raw_price <= 0:
        store.set_signal_status(signal_id, "rejected", "missing price for paper buy")
        return "Rejected: missing price for paper buy."
    slippage_bps = int(features.get("slippage_estimate_bps", settings.slippage_bps))
    entry_price = raw_price * (1 + slippage_bps / 10_000)
    quantity = size / entry_price
    plan = {
        "max_loss_plan": f"Default stoploss at {settings.stop_loss_pct:.0f}%.",
        "take_profit_plan": "Sell 50% at 2x, sell 25% at 4x, then trail a stop for the rest.",
        "stop_loss_pct": settings.stop_loss_pct,
        "take_profit_1_multiple": 2.0,
        "take_profit_1_fraction": 0.50,
        "take_profit_2_multiple": 4.0,
        "take_profit_2_fraction": 0.25,
        "trailing_stop_fraction": 0.25,
        "trailing_stop_pct": settings.trailing_stop_pct,
        "slippage_bps": slippage_bps,
        "paper_only": True,
        "entry_mode": entry_mode,
        "exit_mode": settings.exit_mode,
    }
    trade_id = store.add_paper_trade(signal, entry_price, size, quantity, plan, entry_mode=entry_mode, auto_entry_reason=auto_entry_reason)
    store.set_signal_status(signal_id, "approved")
    prefix = "🤖 AUTO PAPER BUY opened" if entry_mode == "auto_paper" else "✅ Paper buy opened only"
    return f"{prefix}. Trade #{trade_id}: ${size:.2f} at simulated price ${entry_price:.8f}."


def auto_paper_buy(signal_id: int, store: MemeScoutStore | None = None) -> str:
    settings = get_settings()
    return approve_paper_buy(signal_id, store, entry_mode="auto_paper", size_override=settings.auto_trade_size_usdc, auto_entry_reason="eligible signal met auto-paper constraints")


def simulate_paper_sell(trade_id: int, market_price: float, store: MemeScoutStore | None = None) -> str:
    """Close an open paper trade at a simulated slippage-adjusted sell price."""
    store = store or MemeScoutStore()
    if market_price <= 0:
        return "Invalid market price for paper sell."
    trade = store.get_trade(trade_id)
    if not trade:
        return "Paper trade not found."
    if trade["status"] != "open":
        return "Paper trade is already closed."
    slippage_bps = int(trade["plan"].get("slippage_bps", get_settings().slippage_bps))
    exit_price = market_price * (1 - slippage_bps / 10_000)
    closed = store.record_exit(trade_id, exit_price, float(trade.get("remaining_quantity") or trade.get("quantity") or 0), "force_close")
    pnl = float(closed["realized_pnl"] if closed else 0)
    return f"✅ Paper sell closed trade #{trade_id} at ${exit_price:.8f}. PnL: ${pnl:.2f}."


def reject_signal(signal_id: int, reason: str = "rejected from Telegram", store: MemeScoutStore | None = None) -> str:
    store = store or MemeScoutStore()
    signal = store.get_signal(signal_id)
    if not signal:
        return "Signal not found."
    store.set_signal_status(signal_id, "rejected", reason)
    return "❌ Signal rejected. No paper trade was opened."
