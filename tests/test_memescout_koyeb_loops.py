import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

from condor.memescout_ai import loops
from condor.memescout_ai.store import MemeScoutStore
from handlers.memescout_ai import (
    _debug_last_scan_text,
    _scan_summary_text,
    _status_text,
    memescout_loop_status_command,
    memescout_monitor_start_command,
    memescout_monitor_stop_command,
    memescout_start_command,
    memescout_stop_command,
)
from routines.memescout_ai import Config, scan_once_summary


@pytest.fixture(autouse=True)
def cleanup_loops(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMESCOUT_DB_PATH", str(tmp_path / "memescout.sqlite"))
    monkeypatch.setenv("MEMESCOUT_SCAN_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("MEMESCOUT_MONITOR_INTERVAL_SECONDS", "120")
    loops.stop_scanner_loop()
    loops.stop_monitor_loop()
    yield
    loops.stop_scanner_loop()
    loops.stop_monitor_loop()


class FakeMessage:
    def __init__(self):
        self.texts = []
        self.chat_id = 123
    async def reply_text(self, text, **kwargs):
        self.texts.append(text)


def fake_update():
    message = FakeMessage()
    return SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=123), effective_user=SimpleNamespace(id=1, username="admin"))


def fake_context():
    return SimpleNamespace(bot=object(), args=[], user_data={})


async def tiny_sleep(*args, **kwargs):
    await asyncio.sleep(0)


def test_koyeb_autostarts_scanner_when_enabled(monkeypatch):
    async def run():
        monkeypatch.setenv("MEMESCOUT_AUTOSTART_SCANNER", "true")
        result = loops.autostart_loops(bot=object(), chat_id=123, store=MemeScoutStore())
        assert result["scanner_started"] is True
        assert loops.scanner_loop_running() is True
        loops.stop_scanner_loop()
    asyncio.run(run())


def test_koyeb_autostarts_monitor_when_enabled(monkeypatch):
    async def run():
        monkeypatch.setenv("MEMESCOUT_AUTOSTART_MONITOR", "true")
        result = loops.autostart_loops(bot=object(), chat_id=123, store=MemeScoutStore())
        assert result["monitor_started"] is True
        assert loops.monitor_loop_running() is True
        loops.stop_monitor_loop()
    asyncio.run(run())


def test_autostart_does_not_create_duplicate_loops(monkeypatch):
    async def run():
        monkeypatch.setenv("MEMESCOUT_AUTOSTART_SCANNER", "true")
        first = loops.autostart_loops(bot=object(), chat_id=123, store=MemeScoutStore())
        second = loops.autostart_loops(bot=object(), chat_id=123, store=MemeScoutStore())
        assert first["scanner_started"] is True
        assert second["scanner_started"] is False
        loops.stop_scanner_loop()
    asyncio.run(run())


def test_memescout_start_and_stop_commands_control_scanner_loop():
    async def run():
        update = fake_update()
        await memescout_start_command.__wrapped__(update, fake_context())
        assert loops.scanner_loop_running() is True
        await memescout_stop_command.__wrapped__(update, fake_context())
        assert loops.scanner_loop_running() is False
        assert "stopped" in update.message.texts[-1].lower()
    asyncio.run(run())


def test_memescout_monitor_start_and_stop_commands_control_monitor_loop():
    async def run():
        update = fake_update()
        await memescout_monitor_start_command.__wrapped__(update, fake_context())
        assert loops.monitor_loop_running() is True
        await memescout_monitor_stop_command.__wrapped__(update, fake_context())
        assert loops.monitor_loop_running() is False
        assert "stopped" in update.message.texts[-1].lower()
    asyncio.run(run())


def test_memescout_loop_status_returns_states():
    update = fake_update()
    asyncio.run(memescout_loop_status_command.__wrapped__(update, fake_context()))
    text = update.message.texts[-1]
    assert "scanner_loop_running" in text
    assert "monitor_loop_running" in text
    assert "next_scan_eta_seconds" in text


def test_memescout_status_explains_loop_and_scan_counts():
    store = MemeScoutStore()
    store.set_state("last_scan_summary", '{"pairs_fetched":2,"candidates_seen":2,"candidates_stored":1,"eligible_count":0,"telegram_signals_sent":0,"duplicate_suppressed":1,"rate_limited":false}')
    text = _status_text(store)
    assert "candidates_stored" in text
    assert "eligible_signals" in text
    assert "telegram_signals_sent" in text
    assert "scanner_loop_running" in text
    assert "last_scan_summary: pairs=2, seen=2, stored=1, eligible=0, sent=0" in text


def test_scan_now_summary_distinguishes_stored_vs_sent(monkeypatch):
    async def no_send(*args, **kwargs):
        raise AssertionError("no telegram signal should be sent for rejected candidate")

    bad_pair = {
        "chainId": "solana",
        "pairAddress": "BadPair",
        "baseToken": {"symbol": "BAD", "address": "BadMint"},
        "liquidity": {"usd": 1000},
        "volume": {"m5": 1, "h1": 1},
        "txns": {"m5": {"buys": 1, "sells": 50}},
        "priceUsd": "0.001",
    }
    store = MemeScoutStore()
    context = SimpleNamespace(bot=SimpleNamespace(send_message=no_send), _chat_id=123)
    with patch("routines.memescout_ai.DexScreenerClient") as cls:
        cls.return_value.latest_solana_pairs = AsyncMock(return_value=[bad_pair])
        summary = asyncio.run(scan_once_summary(Config(max_pairs_per_scan=1), context, store, 123))
    text = _scan_summary_text(summary)
    assert summary.candidates_stored == 1
    assert summary.telegram_signals_sent == 0
    assert "candidates_stored: 1" in text
    assert "telegram_signals_sent: 0" in text


def test_debug_last_scan_does_not_expose_secrets(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-key")
    store = MemeScoutStore()
    store.set_state("last_scan_summary", '{"top_filtered":[{"token_symbol":"BAD","pair_address":"Pair","score":1,"rug_risk":99,"main_rejection_reason":"high rug","liquidity":1,"volume":2,"age":3}]}')
    text = _debug_last_scan_text(store)
    assert "super-secret-key" not in text
    assert "BAD" in text


def test_routines_remains_disabled_in_koyeb_free_mode():
    source = Path("main.py").read_text()
    branch = source[source.index("if koyeb_free_mode():") : source.index("# Import fresh versions after reload")]
    assert 'CommandHandler("routines"' not in branch
    assert "memescout_start" in branch


def test_no_real_trading_path_is_referenced_by_memescout_loop_code():
    roots = [Path("condor/memescout_ai"), Path("handlers/memescout_ai.py"), Path("routines/memescout_ai.py")]
    forbidden = ["handlers.trading", "handlers.dex.swap", "gateway_swap", "gateway_clmm", "hummingbot_client", "place_order", "create_order", "execute_swap", "private_key", "sign_transaction", "jupiter"]
    combined = "\n".join(p.read_text() for root in roots for p in ([root] if root.is_file() else root.glob("*.py"))).lower()
    for token in forbidden:
        assert token.lower() not in combined
