import asyncio
import sys
import types

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

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from condor.memescout_ai.dexscreener import DexScreenerClient
from condor.memescout_ai.paper import approve_paper_buy, reject_signal, simulate_paper_sell
from condor.memescout_ai.scoring import score_signal
from condor.memescout_ai.store import MemeScoutStore
from handlers.memescout_ai import _pnl_text, _status_text, memescout_callback_handler, memescout_reset_hourly_limits_command
from routines.memescout_ai import Config, scan_once, scan_once_summary


@pytest.fixture()
def store(tmp_path, monkeypatch):
    db = tmp_path / "memescout.sqlite"
    monkeypatch.setenv("MEMESCOUT_DB_PATH", str(db))
    monkeypatch.setenv("MEMESCOUT_DEX_REQUEST_MIN_INTERVAL_SECONDS", "1.05")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return MemeScoutStore(db)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class FakeContext:
    def __init__(self):
        self.bot = FakeBot()
        self.user_data = {}
        self._chat_id = 123


class FakeMessage:
    def __init__(self):
        self.chat_id = 123
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


def fake_update(callback_data):
    query = FakeQuery(callback_data)
    return SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=1, username="tester"),
        message=None,
    )


def good_pair(symbol="SAFE", mint="Mint111", pair="Pair111"):
    return {
        "chainId": "solana",
        "pairAddress": pair,
        "pairCreatedAt": int((time.time() - 3600) * 1000),
        "baseToken": {"symbol": symbol, "address": mint},
        "liquidity": {"usd": 100_000},
        "marketCap": 500_000,
        "volume": {"m5": 30_000, "h1": 100_000},
        "txns": {"m5": {"buys": 60, "sells": 10}},
        "priceChange": {"m5": 20, "h1": 40},
        "priceUsd": "0.001",
        "url": "https://dexscreener.com/solana/Pair111",
    }


def bad_pair():
    pair = good_pair("RUG", "RugMint", "RugPair")
    pair["liquidity"] = {"usd": 1_000}
    pair["txns"] = {"m5": {"buys": 1, "sells": 50}}
    return pair


def add_signal(store, eligible=True):
    features = {
        "token_symbol": "SAFE",
        "token_mint": "Mint111",
        "pair_address": "Pair111",
        "age_minutes": 300,
        "liquidity_usd": 90_000,
        "market_cap": 400_000,
        "volume_5m": 20_000,
        "volume_1h": 60_000,
        "buys_5m": 40,
        "sells_5m": 12,
        "price_change_5m": 15,
        "price_change_1h": 30,
        "slippage_estimate_bps": 150,
        "price_usd": 0.001,
    }
    verdict = score_signal(features, store.weights())
    return store.add_signal({
        "token_symbol": features["token_symbol"],
        "token_mint": features["token_mint"],
        "pair_address": features["pair_address"],
        "score": verdict["score"],
        "eligible": eligible,
        "status": "pending",
        "features": {**features, **verdict},
        "explanation": "test",
    })


@pytest.mark.parametrize("pairs,expected", [([good_pair()], 1), ([bad_pair()], 0)])
def test_mocked_dex_screener_scan_generates_only_eligible_telegram_signal(store, pairs, expected):
    context = FakeContext()
    with patch("routines.memescout_ai.DexScreenerClient") as client_cls:
        client_cls.return_value.latest_solana_pairs = AsyncMock(return_value=pairs)
        sent = asyncio.run(scan_once(Config(max_pairs_per_scan=1), context, store, context._chat_id))
    assert sent == expected
    assert len(context.bot.sent) == expected


def test_callback_approve_reject_and_emergency_stop(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_DB_PATH", str(store.path))
    signal_id = add_signal(store)
    context = FakeContext()

    update = fake_update(f"memescout:approve:{signal_id}")
    asyncio.run(memescout_callback_handler.__wrapped__(update, context))
    assert store.stats()["open_trades"] == 1
    assert "Paper buy opened only" in update.callback_query.message.replies[-1][0]

    rejected_signal_id = add_signal(store, eligible=True)
    update = fake_update(f"memescout:reject:{rejected_signal_id}")
    asyncio.run(memescout_callback_handler.__wrapped__(update, context))
    assert store.stats()["open_trades"] == 1
    assert store.get_signal(rejected_signal_id)["status"] == "rejected"

    update = fake_update("memescout:emergency_stop")
    asyncio.run(memescout_callback_handler.__wrapped__(update, context))
    assert store.bool_state("emergency_stop") is True
    blocked_signal_id = add_signal(store, eligible=True)
    assert "Emergency stop" in approve_paper_buy(blocked_signal_id, store)


def test_no_real_trading_module_is_imported_by_memescout_code():
    roots = [Path("condor/memescout_ai"), Path("handlers/memescout_ai.py"), Path("routines/memescout_ai.py")]
    forbidden = [
        "handlers.trading",
        "handlers.dex.swap",
        "gateway_swap",
        "gateway_clmm",
        "hummingbot_client",
        "place_order",
        "create_order",
        "execute_swap",
        "private_key",
        "sign_transaction",
    ]
    combined = "\n".join(
        p.read_text()
        for root in roots
        for p in ([root] if root.is_file() else root.glob("*.py"))
    ).lower()
    for token in forbidden:
        assert token.lower() not in combined


def test_dex_screener_failure_does_not_crash_routine(store):
    context = FakeContext()
    with patch("routines.memescout_ai.DexScreenerClient") as client_cls:
        client_cls.return_value.latest_solana_pairs = AsyncMock(side_effect=RuntimeError("network down"))
        sent = asyncio.run(scan_once(Config(max_pairs_per_scan=1), context, store, context._chat_id))
    assert sent == 0
    assert store.stats()["signals"] == 0


def test_invalid_missing_dex_fields_do_not_crash_scanner(store):
    context = FakeContext()
    with patch("routines.memescout_ai.DexScreenerClient") as client_cls:
        client_cls.return_value.latest_solana_pairs = AsyncMock(return_value=[{"chainId": "solana", "baseToken": None}, {"not": "a pair"}])
        sent = asyncio.run(scan_once(Config(max_pairs_per_scan=2), context, store, context._chat_id))
    assert sent == 0
    assert store.stats()["signals"] == 0


def test_duplicate_token_signals_are_rate_limited(store):
    context = FakeContext()
    with patch("routines.memescout_ai.DexScreenerClient") as client_cls:
        client_cls.return_value.latest_solana_pairs = AsyncMock(return_value=[good_pair()])
        first = asyncio.run(scan_once(Config(max_pairs_per_scan=1), context, store, context._chat_id))
        second = asyncio.run(scan_once(Config(max_pairs_per_scan=1), context, store, context._chat_id))
    assert first == 1
    assert second == 0
    assert store.stats()["signals"] == 1


def test_paper_balance_cannot_go_negative(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_TRADE_SIZE_USDC", "10")
    store.set_state("paper_balance_usdc", "5")
    signal_id = add_signal(store)
    approve_paper_buy(signal_id, store)
    assert store.stats()["paper_balance_usdc"] == 0


def test_stoploss_take_profit_simulation_updates_pnl(store):
    win_signal = add_signal(store)
    approve_paper_buy(win_signal, store)
    assert "PnL" in simulate_paper_sell(1, 0.0021, store)
    stats = store.stats()
    assert stats["closed_trades"] == 1
    assert stats["total_pnl"] > 0
    assert stats["win_rate"] == 100

    loss_signal = add_signal(store)
    approve_paper_buy(loss_signal, store)
    simulate_paper_sell(2, 0.0006, store)
    assert store.stats()["worst_trade"] < 0


def test_pause_blocks_scanning_but_status_and_pnl_still_work(store):
    context = FakeContext()
    store.set_state("paused", "true")
    with patch("routines.memescout_ai.DexScreenerClient") as client_cls:
        client_cls.return_value.latest_solana_pairs = AsyncMock(return_value=[good_pair()])
        sent = asyncio.run(scan_once(Config(max_pairs_per_scan=1), context, store, context._chat_id))
    assert sent == 0
    assert "MemeScout AI Status" in _status_text(store)
    assert "MemeScout Paper PnL" in _pnl_text(store)


def test_llm_is_called_only_after_deterministic_filters_pass(store):
    context = FakeContext()
    calls = 0

    async def fake_explain(features, verdict, settings):
        nonlocal calls
        calls += 1
        return "llm explanation"

    with patch("routines.memescout_ai.DexScreenerClient") as client_cls, patch("routines.memescout_ai.explain_signal", fake_explain):
        client_cls.return_value.latest_solana_pairs = AsyncMock(return_value=[bad_pair(), good_pair("SAFE2", "Mint222", "Pair222")])
        sent = asyncio.run(scan_once(Config(max_pairs_per_scan=2, send_rejected=True), context, store, context._chat_id))
    assert sent == 2
    assert calls == 1


def test_dex_client_uses_conservative_request_spacing(monkeypatch):
    monkeypatch.setenv("MEMESCOUT_DEX_REQUEST_MIN_INTERVAL_SECONDS", "1.05")
    assert DexScreenerClient().min_request_interval >= 1.0


def test_emergency_stop_blocks_learning_updates(store):
    from condor.memescout_ai.learning import maybe_generate_learning_report

    store.set_state("emergency_stop", "true")
    assert maybe_generate_learning_report(store) is None


def unique_bad_pair(symbol: str, mint: str, pair: str):
    item = bad_pair()
    item["baseToken"] = {"symbol": symbol, "address": mint}
    item["pairAddress"] = pair
    item["url"] = f"https://dexscreener.com/solana/{pair}"
    return item


def test_rejected_candidates_do_not_consume_telegram_signal_quota(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_MAX_SIGNALS_PER_HOUR", "1")
    context = FakeContext()
    pairs = [unique_bad_pair("BAD1", "BadMint1", "BadPair1"), unique_bad_pair("BAD2", "BadMint2", "BadPair2")]
    with patch("routines.memescout_ai.DexScreenerClient") as client_cls:
        client_cls.return_value.latest_solana_pairs = AsyncMock(return_value=pairs)
        summary = asyncio.run(scan_once_summary(Config(max_pairs_per_scan=2), context, store, context._chat_id))
    assert summary.candidates_stored == 2
    assert summary.telegram_signals_sent == 0
    assert summary.telegram_signals_sent_this_hour == 0
    assert summary.telegram_signal_quota_remaining == 1
    assert not summary.telegram_signal_rate_limited


def test_stored_candidates_do_not_consume_telegram_signal_quota(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_MAX_SIGNALS_PER_HOUR", "1")
    context = FakeContext()
    with patch("routines.memescout_ai.DexScreenerClient") as client_cls:
        client_cls.return_value.latest_solana_pairs = AsyncMock(return_value=[unique_bad_pair("BAD", "BadMint3", "BadPair3")])
        summary = asyncio.run(scan_once_summary(Config(max_pairs_per_scan=1), context, store, context._chat_id))
    assert store.stats()["signals"] == 1
    assert summary.telegram_signals_sent_this_hour == 0
    assert len(context.bot.sent) == 0


def test_only_sent_telegram_signals_consume_signal_quota(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_MAX_SIGNALS_PER_HOUR", "1")
    context = FakeContext()
    with patch("routines.memescout_ai.DexScreenerClient") as client_cls:
        client_cls.return_value.latest_solana_pairs = AsyncMock(return_value=[good_pair("SAFE1", "MintSignal1", "PairSignal1")])
        summary = asyncio.run(scan_once_summary(Config(max_pairs_per_scan=1), context, store, context._chat_id))
    assert summary.telegram_signals_sent == 1
    assert summary.telegram_signals_sent_this_hour == 1
    assert summary.telegram_signal_quota_remaining == 0
    assert len(context.bot.sent) == 1


def test_scanner_continues_scoring_and_storing_when_telegram_signal_rate_limited(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_MAX_SIGNALS_PER_HOUR", "0")
    context = FakeContext()
    with patch("routines.memescout_ai.DexScreenerClient") as client_cls:
        client_cls.return_value.latest_solana_pairs = AsyncMock(return_value=[good_pair("SAFE2", "MintSignal2", "PairSignal2")])
        summary = asyncio.run(scan_once_summary(Config(max_pairs_per_scan=1), context, store, context._chat_id))
    assert summary.pairs_fetched == 1
    assert summary.candidates_seen == 1
    assert summary.candidates_stored == 1
    assert summary.eligible_count == 1
    assert summary.telegram_signals_sent == 0
    assert summary.telegram_signal_rate_limited is True
    assert store.stats()["signals"] == 1


def test_candidate_storage_limit_skips_extra_rejected_candidates(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_MAX_CANDIDATES_STORED_PER_HOUR", "1")
    context = FakeContext()
    pairs = [unique_bad_pair("BAD4", "BadMint4", "BadPair4"), unique_bad_pair("BAD5", "BadMint5", "BadPair5")]
    with patch("routines.memescout_ai.DexScreenerClient") as client_cls:
        client_cls.return_value.latest_solana_pairs = AsyncMock(return_value=pairs)
        summary = asyncio.run(scan_once_summary(Config(max_pairs_per_scan=2), context, store, context._chat_id))
    assert summary.candidates_seen == 2
    assert summary.candidates_stored == 1
    assert summary.candidate_storage_rate_limited is True
    assert store.stats()["signals"] == 1


def test_memescout_status_shows_separate_quota_counters(store):
    store.increment_counter("candidates_seen", 3, 3600)
    store.increment_counter("candidates_stored", 2, 3600)
    store.increment_counter("telegram_signals_sent", 1, 3600)
    store.set_state("last_scan_summary", '{"candidates_seen_this_hour":3,"candidates_stored_this_hour":2,"telegram_signals_sent_this_hour":1,"candidate_storage_quota_remaining":98,"telegram_signal_quota_remaining":5}')
    text = _status_text(store)
    assert "candidates_seen_this_hour: 3" in text
    assert "candidates_stored_this_hour: 2" in text
    assert "telegram_signals_sent_this_hour: 1" in text
    assert "candidate_storage_quota_remaining: 98" in text
    assert "telegram_signal_quota_remaining: 5" in text


def test_memescout_reset_hourly_limits_resets_counters_only(store, monkeypatch):
    monkeypatch.setenv("MEMESCOUT_DB_PATH", str(store.path))
    signal_id = add_signal(store, eligible=False)
    store.increment_counter("candidates_seen", 3, 3600)
    store.increment_counter("candidates_stored", 2, 3600)
    store.increment_counter("telegram_signals_sent", 1, 3600)
    update = SimpleNamespace(message=FakeMessage(), effective_user=SimpleNamespace(id=1, username="admin"))
    asyncio.run(memescout_reset_hourly_limits_command.__wrapped__(update, FakeContext()))
    assert store.get_signal(signal_id) is not None
    assert store.counter_value("candidates_seen", 3600) == 0
    assert store.counter_value("candidates_stored", 3600) == 0
    assert store.counter_value("telegram_signals_sent", 3600) == 0
    assert "trades and signals were not deleted" in update.message.replies[-1][0]
