"""Telegram commands for MemeScout AI."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from condor.memescout_ai.dexscreener import fetch_pair_by_address, pair_to_features
from condor.memescout_ai.koyeb import koyeb_free_mode
from condor.memescout_ai.paper import approve_paper_buy, reject_signal, simulate_paper_sell
from condor.memescout_ai.store import MemeScoutStore
from routines.memescout_ai import Config, scan_once
from utils.auth import restricted


def _menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Scan now", callback_data="memescout:scan")],
        [InlineKeyboardButton("📊 Status", callback_data="memescout:status"), InlineKeyboardButton("💰 PnL", callback_data="memescout:pnl")],
        [InlineKeyboardButton("⏸ Pause", callback_data="memescout:pause"), InlineKeyboardButton("▶️ Resume", callback_data="memescout:resume")],
        [InlineKeyboardButton("🛑 Emergency stop", callback_data="memescout:emergency_stop")],
    ])


@restricted
async def memescout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hint = "Tap Scan now for Koyeb Free mode." if koyeb_free_mode() else "Use /routines to run the continuous memescout_ai scanner."
    await update.message.reply_text(_status_text() + f"\n\n{hint}", reply_markup=_menu())


@restricted
async def memescout_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_status_text())


@restricted
async def memescout_signals_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_signals_text())


@restricted
async def memescout_pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_pnl_text())


@restricted
async def memescout_positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_positions_text())


@restricted
async def memescout_position_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trade_id = _first_int_arg(context)
    if trade_id is None:
        await update.message.reply_text("Usage: /memescout_position <id>")
        return
    await update.message.reply_text(_position_text(trade_id))


@restricted
async def memescout_force_close_paper_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    trade_id = _first_int_arg(context)
    if trade_id is None:
        await update.message.reply_text("Usage: /memescout_force_close_paper <id>")
        return
    await update.message.reply_text(await _force_close_text(trade_id))


@restricted
async def memescout_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = MemeScoutStore()
    backup_dir = Path("data/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"memescout_backup_{int(time.time())}.sqlite"
    shutil.copy2(store.path, backup_path)
    if hasattr(update.message, "reply_document"):
        with backup_path.open("rb") as fh:
            await update.message.reply_document(document=fh, filename=backup_path.name, caption="MemeScout SQLite backup (paper data only).")
    else:
        await update.message.reply_text(f"Backup created: {backup_path}")


@restricted
async def memescout_daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_daily_text())


@restricted
async def memescout_pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    MemeScoutStore().set_state("paused", "true")
    await update.message.reply_text("⏸ MemeScout paused. No new scans will run until /memescout_resume.")


@restricted
async def memescout_resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = MemeScoutStore()
    store.set_state("paused", "false")
    store.set_state("emergency_stop", "false")
    await update.message.reply_text("▶️ MemeScout resumed in paper-only mode.")


@restricted
async def memescout_emergency_stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    MemeScoutStore().set_state("emergency_stop", "true")
    await update.message.reply_text("🛑 Emergency stop enabled. New signals and paper trades are blocked.")


@restricted
async def memescout_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    store = MemeScoutStore()

    if action == "approve" and len(parts) == 3:
        text = approve_paper_buy(int(parts[2]), store)
    elif action == "reject" and len(parts) == 3:
        text = reject_signal(int(parts[2]), "rejected from Telegram button", store)
    elif action == "scan":
        if store.bool_state("emergency_stop"):
            text = "🛑 Emergency stop is ON. Scan blocked."
        else:
            context._chat_id = query.message.chat_id
            sent = await scan_once(Config(max_pairs_per_scan=10), context, store, query.message.chat_id)
            text = f"Scan complete. Sent {sent} signal(s)."
    elif action == "status":
        text = _status_text(store)
    elif action == "pnl":
        text = _pnl_text(store)
    elif action == "pause":
        store.set_state("paused", "true")
        text = "⏸ MemeScout paused."
    elif action == "resume":
        store.set_state("paused", "false")
        store.set_state("emergency_stop", "false")
        text = "▶️ MemeScout resumed in paper-only mode."
    elif action == "emergency_stop":
        store.set_state("emergency_stop", "true")
        text = "🛑 Emergency stop enabled. New signals and paper trades are blocked."
    else:
        text = "Unknown MemeScout action."
    await query.message.reply_text(text, reply_markup=_menu() if action in {"status", "pnl"} else None)


def _status_text(store: MemeScoutStore | None = None) -> str:
    store = store or MemeScoutStore()
    stats = store.stats()
    return (
        "🧭 MemeScout AI Status\n"
        "Mode: PAPER ONLY (real trading disabled)\n"
        f"Paused: {store.bool_state('paused')}\n"
        f"Emergency stop: {store.bool_state('emergency_stop')}\n"
        f"Signals stored: {stats['signals']}\n"
        f"Open paper trades: {stats['open_trades']}\n"
        f"Paper balance: ${stats['paper_balance_usdc']:.2f} USDC"
    )


def _pnl_text(store: MemeScoutStore | None = None) -> str:
    stats = (store or MemeScoutStore()).stats()
    return (
        "💰 MemeScout Paper PnL\n"
        f"Open positions: {stats['open_trades']}\n"
        f"Closed positions: {stats['closed_trades']}\n"
        f"Realized PnL: ${stats['realized_pnl']:.2f}\n"
        f"Unrealized PnL: ${stats['unrealized_pnl']:.2f}\n"
        f"Win rate: {stats['win_rate']}%\n"
        f"Profit factor: {stats['profit_factor']}\n"
        f"Max drawdown: ${stats['drawdown']:.2f}\n"
        f"Best trade: ${stats['best_trade']:.2f}\n"
        f"Worst trade: ${stats['worst_trade']:.2f}"
    )


def _daily_text(store: MemeScoutStore | None = None) -> str:
    store = store or MemeScoutStore()
    stats = store.stats()
    warning = "⚠️ Sample size is too small for conclusions." if stats["closed_trades"] < 50 else ""
    return (
        "📅 MemeScout Daily Report\n"
        f"Total signals: {stats['signals']}\n"
        f"Approved paper trades: {stats['approved_trades']}\n"
        f"Rejected signals: {stats['rejected_signals']}\n"
        f"Open positions: {stats['open_trades']}\n"
        f"Closed positions: {stats['closed_trades']}\n"
        f"Win rate: {stats['win_rate']}%\n"
        f"Average win: ${stats['average_win']:.2f}\n"
        f"Average loss: ${stats['average_loss']:.2f}\n"
        f"Profit factor: {stats['profit_factor']}\n"
        f"Realized PnL: ${stats['realized_pnl']:.2f}\n"
        f"Unrealized PnL: ${stats['unrealized_pnl']:.2f}\n"
        f"Max drawdown: ${stats['drawdown']:.2f}\n"
        f"Best/worst trade: ${stats['best_trade']:.2f} / ${stats['worst_trade']:.2f}\n"
        f"Stoploss hits: {stats['stoploss_hits']}\n"
        f"TP1/TP2/trailing exits: {stats['tp1_hits']}/{stats['tp2_hits']}/{stats['trailing_stop_hits']}\n"
        f"{warning}"
    )


def _signals_text(store: MemeScoutStore | None = None, limit: int = 10) -> str:
    rows = (store or MemeScoutStore()).list_signals(limit)
    if not rows:
        return "No MemeScout signals stored yet."
    lines = ["📡 Recent MemeScout Signals"]
    for row in rows:
        lines.append(f"#{row['id']} {row['token_symbol']} score={row['score']} status={row['status']}")
    return "\n".join(lines)


def _first_int_arg(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    args = getattr(context, "args", None) or []
    if not args:
        return None
    try:
        return int(args[0])
    except (TypeError, ValueError):
        return None


def _positions_text(store: MemeScoutStore | None = None) -> str:
    store = store or MemeScoutStore()
    trades = store.list_trades(limit=15)
    if not trades:
        return "No MemeScout paper positions yet."
    lines = ["📌 MemeScout Paper Positions"]
    for t in trades:
        lines.append(
            f"#{t['id']} {t['token_symbol']} {t['status']} rem=${float(t.get('remaining_size_usdc') or 0):.2f} "
            f"realized=${float(t.get('realized_pnl') or 0):.2f} unrealized=${float(t.get('unrealized_pnl') or 0):.2f}"
        )
    return "\n".join(lines)


def _position_text(trade_id: int, store: MemeScoutStore | None = None) -> str:
    store = store or MemeScoutStore()
    t = store.get_trade(trade_id)
    if not t:
        return "Paper position not found."
    exits = store.list_exits(trade_id)
    return (
        f"📌 MemeScout Position #{t['id']}\n"
        f"Token: {t['token_symbol']}\n"
        f"Status: {t['status']}\n"
        f"Entry/current: ${float(t['entry_price']):.8f} / ${float(t.get('current_price') or 0):.8f}\n"
        f"Highest/lowest: ${float(t.get('highest_price') or 0):.8f} / ${float(t.get('lowest_price') or 0):.8f}\n"
        f"Remaining size: ${float(t.get('remaining_size_usdc') or 0):.2f}\n"
        f"Realized/unrealized PnL: ${float(t.get('realized_pnl') or 0):.2f} / ${float(t.get('unrealized_pnl') or 0):.2f}\n"
        f"Flags SL/TP1/TP2/Trail: {t.get('stoploss_triggered')}/{t.get('tp1_triggered')}/{t.get('tp2_triggered')}/{t.get('trailing_stop_triggered')}\n"
        f"Monitor error: {t.get('monitor_error') or 'none'}\n"
        f"Recorded exits: {len(exits)}"
    )


async def _force_close_text(trade_id: int, store: MemeScoutStore | None = None) -> str:
    store = store or MemeScoutStore()
    trade = store.get_trade(trade_id)
    if not trade:
        return "Paper position not found."
    if trade["status"] != "open":
        return "Paper position is already closed."
    try:
        pair = await fetch_pair_by_address(trade.get("pair_address") or "")
        price = float(pair_to_features(pair or {}).get("price_usd") or 0)
        if price <= 0:
            raise ValueError("latest price unavailable")
    except Exception as exc:
        return f"Could not force close paper position: {exc}"
    return simulate_paper_sell(trade_id, price, store)
