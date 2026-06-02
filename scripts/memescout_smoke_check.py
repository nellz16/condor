"""Runtime smoke checks for MemeScout AI.

Usage from repo root:
    PYTHONPATH=. python scripts/memescout_smoke_check.py
    PYTHONPATH=. python scripts/memescout_smoke_check.py --live-dex

The default run uses a fixture and never calls Telegram or any trading API.
The optional --live-dex check performs one read-only DEX Screener fetch.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import types
import time
from pathlib import Path

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
    sys.modules["telegram"] = telegram

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

from unittest.mock import patch

from condor.memescout_ai.dexscreener import DexScreenerClient
from condor.memescout_ai.paper import approve_paper_buy, reject_signal
from condor.memescout_ai.monitor import monitor_once
from condor.memescout_ai.store import MemeScoutStore
from routines.memescout_ai import Config, scan_once


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class FakeContext:
    def __init__(self):
        self.bot = FakeBot()
        self._chat_id = 123


class FixtureDexClient:
    async def latest_solana_pairs(self, limit: int = 20):
        return [fixture_pair()]


def fixture_pair():
    return {
        "chainId": "solana",
        "pairAddress": "SmokePair111",
        "pairCreatedAt": int((time.time() - 3600) * 1000),
        "baseToken": {"symbol": "SMOKE", "address": "SmokeMint111"},
        "liquidity": {"usd": 100_000},
        "marketCap": 500_000,
        "volume": {"m5": 30_000, "h1": 100_000},
        "txns": {"m5": {"buys": 60, "sells": 10}},
        "priceChange": {"m5": 20, "h1": 40},
        "priceUsd": "0.001",
        "url": "https://dexscreener.com/solana/SmokePair111",
    }


async def run_fixture_smoke() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemeScoutStore(Path(tmp) / "memescout.sqlite")
        context = FakeContext()
        with patch("routines.memescout_ai.DexScreenerClient", FixtureDexClient):
            sent = await scan_once(Config(max_pairs_per_scan=1), context, store, context._chat_id)
        assert sent == 1, "fixture scan should send one signal"
        assert store.stats()["signals"] == 1, "signal should persist"
        approve_text = approve_paper_buy(1, store)
        assert "Paper buy opened only" in approve_text, approve_text
        assert store.stats()["open_trades"] == 1, "approve should open one paper trade"
        reject_text = reject_signal(1, "smoke reject after approval", store)
        assert "No paper trade" in reject_text, reject_text
        assert store.stats()["signals"] == 1
        assert store.stats()["open_trades"] == 1
        async def fake_price(pair_address):
            return fixture_pair() | {"priceUsd": "2.0"}
        with patch("condor.memescout_ai.monitor.fetch_pair_by_address", fake_price):
            monitor_result = await monitor_once(store)
        assert monitor_result["updated"] == 1
        assert len(store.list_exits(1)) >= 1, "monitor should record a paper exit at 2x"
        store.set_state("emergency_stop", "true")
        blocked = approve_paper_buy(1, store)
        assert "Emergency stop" in blocked or "already approved" in blocked
        restarted = MemeScoutStore(Path(tmp) / "memescout.sqlite")
        assert restarted.stats()["signals"] == 1, "SQLite state should survive store restart"
    print("fixture smoke: PASS")


async def run_live_dex_smoke() -> None:
    pairs = await DexScreenerClient().latest_solana_pairs(limit=1)
    assert isinstance(pairs, list), "DEX Screener fetch should return a list"
    print(f"live DEX Screener smoke: PASS ({len(pairs)} pair(s))")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-dex", action="store_true", help="perform one read-only DEX Screener request")
    args = parser.parse_args()
    await run_fixture_smoke()
    if args.live_dex:
        await run_live_dex_smoke()


if __name__ == "__main__":
    asyncio.run(main())
