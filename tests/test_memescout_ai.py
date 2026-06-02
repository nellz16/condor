import asyncio

import pytest

from condor.memescout_ai.llm import explain_signal
from condor.memescout_ai.paper import approve_paper_buy, reject_signal
from condor.memescout_ai.scoring import score_signal
from condor.memescout_ai.settings import get_settings, mask_secret
from condor.memescout_ai.store import MemeScoutStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    db = tmp_path / "memescout.sqlite"
    monkeypatch.setenv("MEMESCOUT_DB_PATH", str(db))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return MemeScoutStore(db)


def good_features():
    return {
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


def add_signal(store, eligible=True):
    features = good_features()
    verdict = score_signal(features, store.weights())
    verdict["eligible"] = eligible
    signal = {
        "token_symbol": features["token_symbol"],
        "token_mint": features["token_mint"],
        "pair_address": features["pair_address"],
        "score": verdict["score"],
        "eligible": eligible,
        "status": "pending",
        "features": {**features, **verdict},
        "explanation": "test",
    }
    return store.add_signal(signal)


def test_high_rug_risk_token_is_rejected(store):
    features = good_features() | {"liquidity_usd": 1_000, "sells_5m": 50, "buys_5m": 1}
    verdict = score_signal(features, store.weights())
    assert verdict["rug_risk_score"] >= 70
    assert verdict["eligible"] is False


def test_reject_button_never_opens_paper_trade(store):
    signal_id = add_signal(store)
    text = reject_signal(signal_id, "test reject", store)
    assert "No paper trade" in text
    assert store.stats()["open_trades"] == 0


def test_approve_button_opens_only_paper_trade(store):
    signal_id = add_signal(store)
    text = approve_paper_buy(signal_id, store)
    assert "Paper buy opened only" in text
    assert store.stats()["open_trades"] == 1
    assert store.get_signal(signal_id)["status"] == "approved"


def test_emergency_stop_blocks_new_paper_trades(store):
    signal_id = add_signal(store)
    store.set_state("emergency_stop", "true")
    text = approve_paper_buy(signal_id, store)
    assert "Emergency stop" in text
    assert store.stats()["open_trades"] == 0


def test_no_llm_key_uses_rule_based_mode(store, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    verdict = score_signal(good_features(), store.weights())
    explanation = asyncio.run(explain_signal(good_features(), verdict))
    assert "Rule-based mode" in explanation


def test_signal_rate_limit_count(store):
    for _ in range(3):
        add_signal(store)
    assert store.recent_signal_count(3600) == 3


def test_safe_defaults_require_no_real_trading_keys(monkeypatch):
    monkeypatch.delenv("MEMESCOUT_PAPER_ONLY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = get_settings()
    assert settings.paper_only is True
    assert settings.default_balance_usdc == 100.0
    assert settings.gemini_api_key == ""


def test_gemini_failure_falls_back_without_crashing(store, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "bad-test-key")
    verdict = score_signal(good_features(), store.weights())
    explanation = asyncio.run(explain_signal(good_features(), verdict))
    assert "Rule-based mode" in explanation


def test_secret_masking_never_returns_full_key():
    assert mask_secret("abcd1234wxyz") == "abcd...wxyz"
    assert "1234" not in mask_secret("abcd1234wxyz")


def test_llm_rate_limit_uses_rule_based_after_limit(store, monkeypatch):
    from condor.memescout_ai import llm

    async def fake_gemini(prompt, settings):
        return "gemini explanation"

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("MEMESCOUT_LLM_MAX_CALLS_PER_HOUR", "1")
    monkeypatch.setattr(llm, "_gemini", fake_gemini)
    llm._llm_call_timestamps.clear()
    verdict = score_signal(good_features(), store.weights())
    first = asyncio.run(explain_signal(good_features(), verdict))
    second = asyncio.run(explain_signal(good_features(), verdict))
    assert first == "gemini explanation"
    assert "Rule-based mode" in second
