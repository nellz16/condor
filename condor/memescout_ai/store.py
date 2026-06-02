"""SQLite persistence for MemeScout AI signals, paper trades, and learning."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .koyeb import ensure_data_dir, warn_if_local_sqlite_on_koyeb
from .settings import get_settings

DEFAULT_WEIGHTS = {
    "liquidity": 0.22,
    "volume": 0.20,
    "buy_pressure": 0.16,
    "age": 0.12,
    "price_momentum": 0.12,
    "market_cap": 0.08,
    "rug_safety": 0.10,
}


class MemeScoutStore:
    def __init__(self, path: Path | None = None):
        self.path = path or get_settings().database_path
        ensure_data_dir(self.path)
        warn_if_local_sqlite_on_koyeb(self.path)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    token_symbol TEXT NOT NULL,
                    token_mint TEXT NOT NULL,
                    pair_address TEXT NOT NULL,
                    strategy_id TEXT NOT NULL DEFAULT 'momentum_continuation',
                    score REAL NOT NULL,
                    eligible INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reject_reason TEXT,
                    features_json TEXT NOT NULL,
                    explanation TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL,
                    token_symbol TEXT NOT NULL,
                    token_mint TEXT NOT NULL,
                    pair_address TEXT NOT NULL,
                    strategy_id TEXT NOT NULL DEFAULT 'momentum_continuation',
                    opened_at REAL NOT NULL,
                    closed_at REAL,
                    status TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    size_usdc REAL NOT NULL,
                    quantity REAL NOT NULL,
                    remaining_quantity REAL NOT NULL DEFAULT 0,
                    remaining_size_usdc REAL NOT NULL DEFAULT 0,
                    current_price REAL,
                    highest_price REAL,
                    lowest_price REAL,
                    realized_pnl REAL DEFAULT 0,
                    unrealized_pnl REAL DEFAULT 0,
                    max_profit_seen REAL DEFAULT 0,
                    max_drawdown_seen REAL DEFAULT 0,
                    stoploss_triggered INTEGER DEFAULT 0,
                    tp1_triggered INTEGER DEFAULT 0,
                    tp2_triggered INTEGER DEFAULT 0,
                    trailing_stop_triggered INTEGER DEFAULT 0,
                    monitor_error TEXT,
                    last_monitor_update_at REAL,
                    plan_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_trade_exits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    reason TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    proceeds_usdc REAL NOT NULL,
                    realized_pnl REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS weights (
                    key TEXT PRIMARY KEY,
                    value REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    trades_reviewed INTEGER NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    report TEXT NOT NULL
                );
                """
            )
            self._migrate(db)
            for key, value in DEFAULT_WEIGHTS.items():
                db.execute("INSERT OR IGNORE INTO weights(key, value) VALUES(?, ?)", (key, value))
            db.execute("INSERT OR IGNORE INTO state(key, value) VALUES('paper_balance_usdc', ?)", (str(get_settings().default_balance_usdc),))
            db.execute("INSERT OR IGNORE INTO state(key, value) VALUES('paused', 'false')")
            db.execute("INSERT OR IGNORE INTO state(key, value) VALUES('emergency_stop', 'false')")


    def _migrate(self, db: sqlite3.Connection) -> None:
        signal_cols = {row["name"] for row in db.execute("PRAGMA table_info(signals)")}
        if "strategy_id" not in signal_cols:
            db.execute("ALTER TABLE signals ADD COLUMN strategy_id TEXT NOT NULL DEFAULT 'momentum_continuation'")
        cols = {row["name"] for row in db.execute("PRAGMA table_info(paper_trades)")}
        migrations = {
            "strategy_id": "ALTER TABLE paper_trades ADD COLUMN strategy_id TEXT NOT NULL DEFAULT 'momentum_continuation'",
            "remaining_quantity": "ALTER TABLE paper_trades ADD COLUMN remaining_quantity REAL NOT NULL DEFAULT 0",
            "remaining_size_usdc": "ALTER TABLE paper_trades ADD COLUMN remaining_size_usdc REAL NOT NULL DEFAULT 0",
            "current_price": "ALTER TABLE paper_trades ADD COLUMN current_price REAL",
            "highest_price": "ALTER TABLE paper_trades ADD COLUMN highest_price REAL",
            "lowest_price": "ALTER TABLE paper_trades ADD COLUMN lowest_price REAL",
            "unrealized_pnl": "ALTER TABLE paper_trades ADD COLUMN unrealized_pnl REAL DEFAULT 0",
            "max_profit_seen": "ALTER TABLE paper_trades ADD COLUMN max_profit_seen REAL DEFAULT 0",
            "max_drawdown_seen": "ALTER TABLE paper_trades ADD COLUMN max_drawdown_seen REAL DEFAULT 0",
            "stoploss_triggered": "ALTER TABLE paper_trades ADD COLUMN stoploss_triggered INTEGER DEFAULT 0",
            "tp1_triggered": "ALTER TABLE paper_trades ADD COLUMN tp1_triggered INTEGER DEFAULT 0",
            "tp2_triggered": "ALTER TABLE paper_trades ADD COLUMN tp2_triggered INTEGER DEFAULT 0",
            "trailing_stop_triggered": "ALTER TABLE paper_trades ADD COLUMN trailing_stop_triggered INTEGER DEFAULT 0",
            "monitor_error": "ALTER TABLE paper_trades ADD COLUMN monitor_error TEXT",
            "last_monitor_update_at": "ALTER TABLE paper_trades ADD COLUMN last_monitor_update_at REAL",
        }
        for col, sql in migrations.items():
            if col not in cols:
                db.execute(sql)
        db.execute(
            """UPDATE paper_trades SET remaining_quantity=quantity
            WHERE remaining_quantity=0 AND status='open'"""
        )
        db.execute(
            """UPDATE paper_trades SET remaining_size_usdc=size_usdc
            WHERE remaining_size_usdc=0 AND status='open'"""
        )

    def get_state(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state(key, value) VALUES(?, ?)", (key, value))

    def bool_state(self, key: str) -> bool:
        return self.get_state(key, "false").lower() == "true"

    def counter_value(self, name: str, period_seconds: int = 3600) -> int:
        bucket = int(time.time() // period_seconds)
        return int(self.get_state(f"counter:{name}:{period_seconds}:{bucket}", "0") or 0)

    def increment_counter(self, name: str, amount: int = 1, period_seconds: int = 3600) -> int:
        bucket = int(time.time() // period_seconds)
        key = f"counter:{name}:{period_seconds}:{bucket}"
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            value = int(row["value"]) if row else 0
            value += amount
            db.execute("INSERT OR REPLACE INTO state(key, value) VALUES(?, ?)", (key, str(value)))
        return value

    def reset_hourly_counters(self) -> int:
        with self.connect() as db:
            cur = db.execute("DELETE FROM state WHERE key LIKE 'counter:%:3600:%'")
            return int(cur.rowcount or 0)

    def weights(self) -> dict[str, float]:
        with self.connect() as db:
            return {row["key"]: float(row["value"]) for row in db.execute("SELECT key, value FROM weights")}

    def update_weights(self, values: dict[str, float]) -> None:
        total = sum(max(v, 0.01) for v in values.values()) or 1.0
        with self.connect() as db:
            for key, value in values.items():
                db.execute("INSERT OR REPLACE INTO weights(key, value) VALUES(?, ?)", (key, max(value, 0.01) / total))

    def recent_signal_count(self, seconds: int = 3600) -> int:
        cutoff = time.time() - seconds
        with self.connect() as db:
            row = db.execute("SELECT COUNT(*) AS c FROM signals WHERE created_at >= ?", (cutoff,)).fetchone()
        return int(row["c"])

    def has_recent_signal(self, token_mint: str, pair_address: str, seconds: int = 3600, strategy_id: str | None = None) -> bool:
        cutoff = time.time() - seconds
        with self.connect() as db:
            if strategy_id:
                row = db.execute(
                    """SELECT COUNT(*) AS c FROM signals
                    WHERE created_at >= ? AND strategy_id=? AND (token_mint=? OR pair_address=?)""",
                    (cutoff, strategy_id, token_mint, pair_address),
                ).fetchone()
            else:
                row = db.execute(
                    """SELECT COUNT(*) AS c FROM signals
                    WHERE created_at >= ? AND (token_mint=? OR pair_address=?)""",
                    (cutoff, token_mint, pair_address),
                ).fetchone()
        return int(row["c"]) > 0

    def seen_strategies(self, token_mint: str, pair_address: str) -> list[str]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT DISTINCT strategy_id FROM signals
                WHERE token_mint=? OR pair_address=? ORDER BY strategy_id""",
                (token_mint, pair_address),
            ).fetchall()
        return [str(row["strategy_id"]) for row in rows]

    def add_signal(self, signal: dict[str, Any]) -> int:
        with self.connect() as db:
            cur = db.execute(
                """INSERT INTO signals(created_at, token_symbol, token_mint, pair_address, strategy_id, score,
                eligible, status, reject_reason, features_json, explanation) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    time.time(), signal["token_symbol"], signal["token_mint"], signal["pair_address"],
                    signal.get("strategy_id", "momentum_continuation"), signal["score"], int(signal["eligible"]), signal.get("status", "pending"),
                    signal.get("reject_reason"), json.dumps(signal["features"], sort_keys=True), signal["explanation"],
                ),
            )
            return int(cur.lastrowid)

    def get_signal(self, signal_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["features"] = json.loads(data.pop("features_json"))
        return data

    def set_signal_status(self, signal_id: int, status: str, reason: str | None = None) -> None:
        with self.connect() as db:
            db.execute("UPDATE signals SET status=?, reject_reason=? WHERE id=?", (status, reason, signal_id))

    def add_paper_trade(self, signal: dict[str, Any], entry_price: float, size_usdc: float, quantity: float, plan: dict[str, Any]) -> int:
        with self.connect() as db:
            cur = db.execute(
                """INSERT INTO paper_trades(signal_id, token_symbol, token_mint, pair_address, strategy_id, opened_at,
                status, entry_price, size_usdc, quantity, remaining_quantity, remaining_size_usdc,
                current_price, highest_price, lowest_price, plan_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (signal["id"], signal["token_symbol"], signal["token_mint"], signal["pair_address"],
                 signal.get("strategy_id", "momentum_continuation"), time.time(),
                 "open", entry_price, size_usdc, quantity, quantity, size_usdc, entry_price, entry_price, entry_price,
                 json.dumps(plan, sort_keys=True)),
            )
            row = db.execute("SELECT value FROM state WHERE key='paper_balance_usdc'").fetchone()
            balance = float(row["value"] if row else "100") - size_usdc
            db.execute("INSERT OR REPLACE INTO state(key, value) VALUES('paper_balance_usdc', ?)", (str(max(balance, 0)),))
            return int(cur.lastrowid)

    def get_trade(self, trade_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM paper_trades WHERE id=?", (trade_id,)).fetchone()
        if not row:
            return None
        return self._trade_from_row(row)

    def list_open_trades(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM paper_trades WHERE status='open' ORDER BY id").fetchall()
        return [self._trade_from_row(row) for row in rows]

    def list_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._trade_from_row(row) for row in rows]

    def _trade_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["plan"] = json.loads(data.pop("plan_json"))
        return data

    def update_trade_mark(self, trade_id: int, price: float, error: str | None = None) -> dict[str, Any] | None:
        trade = self.get_trade(trade_id)
        if not trade:
            return None
        now = time.time()
        entry = float(trade["entry_price"])
        remaining_qty = float(trade.get("remaining_quantity") or 0)
        current_unrealized = (price - entry) * remaining_qty if price > 0 else float(trade.get("unrealized_pnl") or 0)
        total_pnl = float(trade.get("realized_pnl") or 0) + current_unrealized
        highest = max(float(trade.get("highest_price") or entry), price) if price > 0 else trade.get("highest_price")
        lowest = min(float(trade.get("lowest_price") or entry), price) if price > 0 else trade.get("lowest_price")
        max_profit = max(float(trade.get("max_profit_seen") or 0), total_pnl)
        max_drawdown = min(float(trade.get("max_drawdown_seen") or 0), total_pnl)
        with self.connect() as db:
            db.execute(
                """UPDATE paper_trades SET current_price=?, highest_price=?, lowest_price=?,
                unrealized_pnl=?, max_profit_seen=?, max_drawdown_seen=?, monitor_error=?,
                last_monitor_update_at=? WHERE id=?""",
                (price if price > 0 else trade.get("current_price"), highest, lowest, current_unrealized,
                 max_profit, max_drawdown, error, now, trade_id),
            )
        return self.get_trade(trade_id)

    def mark_monitor_error(self, trade_id: int, error: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE paper_trades SET monitor_error=?, last_monitor_update_at=? WHERE id=?",
                (error[:250], time.time(), trade_id),
            )

    def record_exit(self, trade_id: int, price: float, quantity: float, reason: str, close_if_empty: bool = True) -> dict[str, Any] | None:
        trade = self.get_trade(trade_id)
        if not trade or trade["status"] != "open":
            return trade
        remaining_qty = max(float(trade.get("remaining_quantity") or 0), 0.0)
        quantity = max(0.0, min(quantity, remaining_qty))
        if quantity <= 0 or price <= 0:
            return trade
        entry = float(trade["entry_price"])
        proceeds = quantity * price
        realized_delta = (price - entry) * quantity
        new_remaining_qty = max(0.0, remaining_qty - quantity)
        new_remaining_size = new_remaining_qty * entry
        realized_total = float(trade.get("realized_pnl") or 0) + realized_delta
        unrealized = (price - entry) * new_remaining_qty
        now = time.time()
        status = "closed" if close_if_empty and new_remaining_qty <= max(float(trade["quantity"]) * 1e-9, 1e-12) else "open"
        closed_at = now if status == "closed" else trade.get("closed_at")
        with self.connect() as db:
            db.execute(
                """INSERT INTO paper_trade_exits(trade_id, created_at, reason, price, quantity, proceeds_usdc, realized_pnl)
                VALUES(?,?,?,?,?,?,?)""",
                (trade_id, now, reason, price, quantity, proceeds, realized_delta),
            )
            flags = {
                "stoploss": "stoploss_triggered",
                "tp1": "tp1_triggered",
                "tp2": "tp2_triggered",
                "trailing_stop": "trailing_stop_triggered",
                "force_close": None,
            }
            flag_col = flags.get(reason)
            flag_sql = f", {flag_col}=1" if flag_col else ""
            db.execute(
                f"""UPDATE paper_trades SET status=?, closed_at=?, exit_price=?, current_price=?,
                remaining_quantity=?, remaining_size_usdc=?, realized_pnl=?, unrealized_pnl=?,
                last_monitor_update_at=?{flag_sql} WHERE id=?""",
                (status, closed_at, price, price, new_remaining_qty, new_remaining_size, realized_total, unrealized, now, trade_id),
            )
            row = db.execute("SELECT value FROM state WHERE key='paper_balance_usdc'").fetchone()
            balance = float(row["value"] if row else "100") + proceeds
            db.execute("INSERT OR REPLACE INTO state(key, value) VALUES('paper_balance_usdc', ?)", (str(max(balance, 0)),))
        return self.update_trade_mark(trade_id, price)

    def close_trade(self, trade_id: int, exit_price: float) -> dict[str, Any] | None:
        trade = self.get_trade(trade_id)
        if not trade:
            return None
        return self.record_exit(trade_id, exit_price, float(trade.get("remaining_quantity") or trade.get("quantity") or 0), "force_close")

    def list_exits(self, trade_id: int | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            if trade_id is None:
                rows = db.execute("SELECT * FROM paper_trade_exits ORDER BY id").fetchall()
            else:
                rows = db.execute("SELECT * FROM paper_trade_exits WHERE trade_id=? ORDER BY id", (trade_id,)).fetchall()
        return [dict(row) for row in rows]

    def list_signals(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self.connect() as db:
            trades = [dict(r) for r in db.execute("SELECT * FROM paper_trades")]
            exits = [dict(r) for r in db.execute("SELECT * FROM paper_trade_exits")]
            signals = db.execute("SELECT COUNT(*) AS c FROM signals").fetchone()["c"]
            rejected = db.execute("SELECT COUNT(*) AS c FROM signals WHERE status LIKE '%rejected%' OR status='rejected'").fetchone()["c"]
        closed = [t for t in trades if t.get("status") == "closed"]
        pnl = [float(t.get("realized_pnl") or 0) for t in closed]
        wins = [p for p in pnl if p > 0]
        losses = [p for p in pnl if p < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        unrealized = sum(float(t.get("unrealized_pnl") or 0) for t in trades if t.get("status") == "open")
        return {
            "paper_balance_usdc": float(self.get_state("paper_balance_usdc", "100")),
            "signals": int(signals),
            "approved_trades": len(trades),
            "rejected_signals": int(rejected),
            "open_trades": sum(1 for t in trades if t.get("status") == "open"),
            "closed_trades": len(pnl),
            "total_pnl": round(sum(pnl), 4),
            "realized_pnl": round(sum(pnl), 4),
            "unrealized_pnl": round(unrealized, 4),
            "win_rate": round((len(wins) / len(pnl) * 100), 2) if pnl else 0,
            "average_win": round(gross_profit / len(wins), 4) if wins else 0,
            "average_loss": round(sum(losses) / len(losses), 4) if losses else 0,
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (round(gross_profit, 4) if gross_profit else 0),
            "best_trade": round(max(pnl), 4) if pnl else 0,
            "worst_trade": round(min(pnl), 4) if pnl else 0,
            "drawdown": round(min([0] + [float(t.get("max_drawdown_seen") or 0) for t in trades]), 4),
            "stoploss_hits": sum(1 for t in trades if int(t.get("stoploss_triggered") or 0)),
            "tp1_hits": sum(1 for t in trades if int(t.get("tp1_triggered") or 0)),
            "tp2_hits": sum(1 for t in trades if int(t.get("tp2_triggered") or 0)),
            "trailing_stop_hits": sum(1 for t in trades if int(t.get("trailing_stop_triggered") or 0)),
            "paper_exits": len(exits),
        }
