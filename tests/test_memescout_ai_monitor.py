import asyncio
import time
import sys
import types
from pathlib import Path
from unittest.mock import patch

if "telegram" not in sys.modules:
    telegram = types.ModuleType("telegram")
    class InlineKeyboardButton:
        def __init__(self, text, callback_data=None, **kwargs):
            self.text = text
            self.callback_data = callback_data
    class InlineKeyboardMarkup:
        def __init__(self, keyboard):
            self.inline_keyboard = keyboard
    telegram.InlineKeyboardButton = InlineKeyboardButton
    telegram.InlineKeyboardMarkup = InlineKeyboardMarkup
    telegram.Update = object
    telegram_ext = types.ModuleType("telegram.ext")
    telegram_ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    sys.modules["telegram"] = telegram
    sys.modules["telegram.ext"] = telegram_ext

if "pydantic" not in sys.modules:
    pydantic = types.ModuleType("pydantic")
    def Field(default=None, **kwargs):
        return default
    class BaseModel:
        def __init__(self, **kwargs):
            annotations = getattr(self.__class__, "__annotations__", {})
            for name in annotations:
                if hasattr(self.__class__, name):
                    setattr(self, name, getattr(self.__class__, name))
            for key, value in kwargs.items():
                setattr(self, key, value)
    pydantic.BaseModel = BaseModel
    pydantic.Field = Field
    sys.modules["pydantic"] = pydantic

utils_auth = types.ModuleType("utils.auth")
def restricted(func):
    async def wrapped(update, context, *args, **kwargs):
        return await func(update, context, *args, **kwargs)
    wrapped.__wrapped__ = func
    return wrapped
utils_auth.restricted = restricted
utils_auth.admin_required = restricted
sys.modules["utils.auth"] = utils_auth

import pytest

from condor.memescout_ai.learning import maybe_generate_learning_report
from condor.memescout_ai.monitor import monitor_once
from condor.memescout_ai.paper import approve_paper_buy
from condor.memescout_ai.scoring import score_signal
from condor.memescout_ai.store import MemeScoutStore
from handlers.memescout_ai import _force_close_text


@pytest.fixture()
def store(tmp_path, monkeypatch):
    db = tmp_path / "memescout.sqlite"
    monkeypatch.setenv("MEMESCOUT_DB_PATH", str(db))
    monkeypatch.setenv("MEMESCOUT_TRAILING_STOP_PCT", "30")
    return MemeScoutStore(db)


def add_open_trade(store, mint="MintM", pair="PairM"):
    features = {
        "token_symbol": "MON",
        "token_mint": mint,
        "pair_address": pair,
        "age_minutes": 300,
        "liquidity_usd": 100_000,
        "market_cap": 500_000,
        "volume_5m": 30_000,
        "volume_1h": 100_000,
        "buys_5m": 60,
        "sells_5m": 10,
        "price_change_5m": 20,
        "price_change_1h": 40,
        "slippage_estimate_bps": 0,
        "price_usd": 1.0,
    }
    verdict = score_signal(features, store.weights())
    signal_id = store.add_signal({
        "token_symbol": features["token_symbol"],
        "token_mint": features["token_mint"],
        "pair_address": features["pair_address"],
        "score": verdict["score"],
        "eligible": True,
        "status": "pending",
        "features": {**features, **verdict},
        "explanation": "test",
    })
    approve_paper_buy(signal_id, store)
    return 1


def pair_at(price, pair="PairM"):
    return {"pairAddress": pair, "baseToken": {"symbol": "MON", "address": "MintM"}, "priceUsd": str(price), "liquidity": {"usd": 100_000}}


def run_monitor_with_prices(store, prices):
    async def fake_fetch(pair_address):
        return pair_at(prices.pop(0), pair_address)
    with patch("condor.memescout_ai.monitor.fetch_pair_by_address", fake_fetch):
        return asyncio.run(monitor_once(store))


def test_monitor_closes_position_at_stoploss(store):
    add_open_trade(store)
    result = run_monitor_with_prices(store, [0.60])
    trade = store.get_trade(1)
    assert result["exits"] == 1
    assert trade["status"] == "closed"
    assert trade["stoploss_triggered"] == 1
    assert trade["realized_pnl"] < 0


def test_monitor_partially_sells_at_tp1(store):
    add_open_trade(store)
    run_monitor_with_prices(store, [2.0])
    trade = store.get_trade(1)
    assert trade["status"] == "open"
    assert trade["tp1_triggered"] == 1
    assert pytest.approx(trade["remaining_quantity"], rel=1e-6) == 5.0
    assert len(store.list_exits(1)) == 1


def test_monitor_partially_sells_at_tp2(store):
    add_open_trade(store)
    run_monitor_with_prices(store, [2.0])
    run_monitor_with_prices(store, [4.0])
    trade = store.get_trade(1)
    assert trade["tp1_triggered"] == 1
    assert trade["tp2_triggered"] == 1
    assert pytest.approx(trade["remaining_quantity"], rel=1e-6) == 2.5
    assert len(store.list_exits(1)) == 2


def test_trailing_stop_closes_remaining_position(store):
    add_open_trade(store)
    run_monitor_with_prices(store, [2.0])
    run_monitor_with_prices(store, [4.0])
    run_monitor_with_prices(store, [2.7])
    trade = store.get_trade(1)
    assert trade["status"] == "closed"
    assert trade["trailing_stop_triggered"] == 1
    assert len(store.list_exits(1)) == 3


def test_open_positions_update_unrealized_pnl(store):
    add_open_trade(store)
    run_monitor_with_prices(store, [1.5])
    trade = store.get_trade(1)
    assert trade["status"] == "open"
    assert trade["unrealized_pnl"] > 0
    assert trade["current_price"] == 1.5


def test_closed_positions_update_realized_pnl(store):
    add_open_trade(store)
    run_monitor_with_prices(store, [2.0])
    run_monitor_with_prices(store, [4.0])
    run_monitor_with_prices(store, [2.7])
    stats = store.stats()
    assert stats["closed_trades"] == 1
    assert stats["realized_pnl"] > 0


def test_monitor_survives_dex_failure(store):
    add_open_trade(store)
    async def fail_fetch(pair_address):
        raise RuntimeError("dex down")
    with patch("condor.memescout_ai.monitor.fetch_pair_by_address", fail_fetch):
        result = asyncio.run(monitor_once(store))
    assert result["errors"] == 1
    assert store.get_trade(1)["monitor_error"]


def test_monitor_survives_malformed_response(store):
    add_open_trade(store)
    async def malformed(pair_address):
        return {"priceUsd": "not-a-number"}
    with patch("condor.memescout_ai.monitor.fetch_pair_by_address", malformed):
        result = asyncio.run(monitor_once(store))
    assert result["errors"] == 1
    assert store.get_trade(1)["status"] == "open"


def test_force_close_is_paper_only(store):
    add_open_trade(store)
    async def fake_fetch(pair_address):
        return pair_at(1.2, pair_address)
    with patch("handlers.memescout_ai.fetch_pair_by_address", fake_fetch):
        text = asyncio.run(_force_close_text(1, store))
    assert "Paper sell closed" in text
    assert store.get_trade(1)["status"] == "closed"


def test_learning_only_uses_closed_trades(store):
    for i in range(49):
        add_open_trade(store, mint=f"M{i}", pair=f"P{i}")
        store.record_exit(i + 1, 1.1, store.get_trade(i + 1)["remaining_quantity"], "force_close")
    add_open_trade(store, mint="OPEN", pair="OPENPAIR")
    assert maybe_generate_learning_report(store) is None
    store.record_exit(50, 1.1, store.get_trade(50)["remaining_quantity"], "force_close")
    assert maybe_generate_learning_report(store) is not None


def test_jackpot_outlier_cannot_dominate_learning_weights(store):
    for i in range(50):
        add_open_trade(store, mint=f"J{i}", pair=f"JP{i}")
        price = 1000 if i == 0 else 0.8
        store.record_exit(i + 1, price, store.get_trade(i + 1)["remaining_quantity"], "force_close")
    before = store.weights()
    maybe_generate_learning_report(store)
    after = store.weights()
    assert max(abs(after[k] - before[k]) for k in before) < 0.03


def test_emergency_stop_behavior_allows_risk_reducing_closes_by_default(store):
    store.set_state("emergency_stop", "true")
    add_open_trade(store)
    assert "Emergency stop" in approve_paper_buy(1, store) or store.stats()["open_trades"] == 0
    store.set_state("emergency_stop", "false")
    # create an approved position, then enable emergency stop before monitor close
    if store.stats()["open_trades"] == 0:
        add_open_trade(store, mint="M2", pair="P2")
    store.set_state("emergency_stop", "true")
    run_monitor_with_prices(store, [0.6])
    assert store.get_trade(1)["status"] == "closed"


def test_monitor_no_real_trading_method_is_referenced():
    roots = [Path("condor/memescout_ai"), Path("handlers/memescout_ai.py"), Path("routines/memescout_ai.py"), Path("routines/memescout_position_monitor.py")]
    forbidden = ["handlers.trading", "handlers.dex.swap", "gateway_swap", "gateway_clmm", "hummingbot_client", "place_order", "create_order", "execute_swap", "private_key", "sign_transaction"]
    combined = "\n".join(p.read_text() for root in roots for p in ([root] if root.is_file() else root.glob("*.py"))).lower()
    for token in forbidden:
        assert token.lower() not in combined



def pair_with_features(price=1.0, liquidity=100_000, buys=60, sells=10, pc5=20, pair="PairM"):
    return {
        "pairAddress": pair,
        "baseToken": {"symbol": "MON", "address": "MintM"},
        "priceUsd": str(price),
        "liquidity": {"usd": liquidity},
        "txns": {"m5": {"buys": buys, "sells": sells}},
        "priceChange": {"m5": pc5, "h1": 10},
    }


def test_auto_exit_by_max_hold_time(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_MAX_HOLD_MINUTES", "1")
    add_open_trade(store)
    with store.connect() as db:
        db.execute("UPDATE paper_trades SET opened_at=? WHERE id=1", (time.time() - 3600,))
    async def fake_fetch(pair_address):
        return pair_with_features(price=1.01)
    with patch("condor.memescout_ai.monitor.fetch_pair_by_address", fake_fetch):
        asyncio.run(monitor_once(store))
    trade = store.get_trade(1)
    assert trade["status"] == "closed"
    assert trade["exit_reason"] == "max_hold_time"


def test_auto_exit_by_momentum_decay(store):
    add_open_trade(store)
    async def fake_fetch(pair_address):
        return pair_with_features(price=0.95, buys=5, sells=12, pc5=-5)
    with patch("condor.memescout_ai.monitor.fetch_pair_by_address", fake_fetch):
        asyncio.run(monitor_once(store))
    assert store.get_trade(1)["exit_reason"] == "momentum_decay"


def test_auto_exit_by_liquidity_drop(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_LIQUIDITY_DROP_EXIT_PCT", "35")
    add_open_trade(store)
    async def fake_fetch(pair_address):
        return pair_with_features(price=0.98, liquidity=50_000)
    with patch("condor.memescout_ai.monitor.fetch_pair_by_address", fake_fetch):
        asyncio.run(monitor_once(store))
    assert store.get_trade(1)["exit_reason"] == "liquidity_drop"


def test_auto_exit_by_sell_pressure(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_SELL_PRESSURE_RATIO_EXIT", "1.4")
    add_open_trade(store)
    async def fake_fetch(pair_address):
        return pair_with_features(price=0.98, buys=10, sells=20, pc5=1)
    with patch("condor.memescout_ai.monitor.fetch_pair_by_address", fake_fetch):
        asyncio.run(monitor_once(store))
    assert store.get_trade(1)["exit_reason"] == "sell_pressure"


def test_stale_price_mark_stale_behavior(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_STALE_PRICE_EXIT_MINUTES", "0")
    monkeypatch.setenv("MEMESCOUT_STALE_PRICE_ACTION", "mark_stale")
    add_open_trade(store)
    async def fail_fetch(pair_address):
        raise RuntimeError("stale")
    with patch("condor.memescout_ai.monitor.fetch_pair_by_address", fail_fetch):
        asyncio.run(monitor_once(store))
    trade = store.get_trade(1)
    assert trade["status"] == "open"
    assert "stale" in (trade.get("monitor_error") or "")
