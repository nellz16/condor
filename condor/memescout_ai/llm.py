"""Optional LLM explainers for MemeScout AI.

The deterministic scorer decides eligibility. LLM output is only a plain-English
explanation for beginners.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .settings import MemeScoutSettings, get_settings

logger = logging.getLogger(__name__)
_llm_call_timestamps: list[float] = []


async def explain_signal(features: dict[str, Any], verdict: dict[str, Any], settings: MemeScoutSettings | None = None) -> str:
    settings = settings or get_settings()
    if not verdict.get("eligible"):
        return rule_based_explanation(features, verdict)
    if not _llm_allowed(settings):
        return rule_based_explanation(features, verdict)

    prompt = _prompt(features, verdict)
    try:
        if settings.llm_provider == "gemini" and settings.gemini_api_key:
            return await _recorded_call(_gemini(prompt, settings))
        if settings.llm_provider == "openrouter" and settings.openrouter_api_key:
            return await _recorded_call(_openai_compatible(prompt, "https://openrouter.ai/api/v1/chat/completions", settings.openrouter_api_key, "openai/gpt-4o-mini"))
        if settings.llm_provider == "openai" and settings.openai_api_key:
            return await _recorded_call(_openai_compatible(prompt, "https://api.openai.com/v1/chat/completions", settings.openai_api_key, "gpt-4o-mini"))
    except Exception as exc:
        logger.warning("MemeScout LLM explanation failed; using rule-based mode: %s", exc)
    return rule_based_explanation(features, verdict)


def _llm_allowed(settings: MemeScoutSettings) -> bool:
    if settings.llm_max_calls_per_hour <= 0:
        return False
    cutoff = time.time() - 3600
    recent = [ts for ts in _llm_call_timestamps if ts >= cutoff]
    _llm_call_timestamps[:] = recent
    return len(_llm_call_timestamps) < settings.llm_max_calls_per_hour


async def _recorded_call(awaitable):
    result = await awaitable
    _llm_call_timestamps.append(time.time())
    return result


def _prompt(features: dict[str, Any], verdict: dict[str, Any]) -> str:
    return (
        "Explain this Solana memecoin paper-trading signal to a total beginner. "
        "Do not give financial advice. Do not say to buy. The deterministic bot already decided eligibility. "
        f"Features: {features}. Deterministic verdict: {verdict}. Keep it under 120 words."
    )


async def _gemini(prompt: str, settings: MemeScoutSettings) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(url, params={"key": settings.gemini_api_key}, json=payload, timeout=20) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Gemini returned HTTP {resp.status}")
            data = await resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


async def _openai_compatible(prompt: str, url: str, api_key: str, model: str) -> str:
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload, timeout=20) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"LLM returned HTTP {resp.status}")
            data = await resp.json()
    return data["choices"][0]["message"]["content"].strip()


def rule_based_explanation(features: dict[str, Any], verdict: dict[str, Any]) -> str:
    status = "eligible for a paper-only watchlist" if verdict.get("eligible") else "rejected by safety rules"
    reason = verdict.get("reject_reason") or "liquidity, volume, buy pressure, age, and rug-risk checks were acceptable."
    return (
        f"Rule-based mode: {features.get('token_symbol')} is {status}. "
        f"Score {verdict.get('score')}/100, rug risk {verdict.get('rug_risk_score')}/100. "
        f"Reason: {reason} This is simulated research only and cannot guarantee profit."
    )
