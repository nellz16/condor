"""Deterministic MemeScout scoring. LLMs may explain this score, not override it."""

from __future__ import annotations

from typing import Any


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_signal(features: dict[str, Any], weights: dict[str, float]) -> dict[str, Any]:
    liquidity = float(features.get("liquidity_usd") or 0)
    volume_5m = float(features.get("volume_5m") or 0)
    volume_1h = float(features.get("volume_1h") or 0)
    buys = float(features.get("buys_5m") or 0)
    sells = float(features.get("sells_5m") or 0)
    age_minutes = float(features.get("age_minutes") or 0)
    market_cap = float(features.get("market_cap") or 0)
    price_change_5m = float(features.get("price_change_5m") or 0)

    rug_risk = rug_risk_score(features)
    components = {
        "liquidity": clamp((liquidity - 10_000) / 90_000),
        "volume": clamp((volume_5m + volume_1h * 0.25) / 50_000),
        "buy_pressure": clamp((buys - sells) / max(buys + sells, 1) * 0.5 + 0.5),
        "age": clamp(age_minutes / (24 * 60)),
        "price_momentum": clamp((price_change_5m + 20) / 60),
        "market_cap": clamp((market_cap - 50_000) / 950_000),
        "rug_safety": clamp(1 - rug_risk / 100),
    }
    score = sum(components.get(k, 0) * weights.get(k, 0) for k in components) * 100

    rejection_reasons: list[str] = []
    if rug_risk >= 70:
        rejection_reasons.append("rug risk is too high")
    if liquidity < 10_000:
        rejection_reasons.append("liquidity is below $10,000")
    if volume_5m <= 0 and volume_1h <= 0:
        rejection_reasons.append("recent volume is missing")
    if sells > buys * 2 and sells >= 10:
        rejection_reasons.append("sell pressure is too high")
    if price_change_5m < -30:
        rejection_reasons.append("price is falling too fast")

    eligible = score >= 58 and not rejection_reasons
    graduation_probability = clamp(score / 100 * (1 - rug_risk / 140)) * 100
    upside_floor = 1.2 if score < 70 else 1.5
    upside_ceiling = 2.0 if score < 70 else 4.0

    return {
        "score": round(score, 2),
        "eligible": eligible,
        "reject_reason": "; ".join(rejection_reasons) if rejection_reasons else None,
        "rug_risk_score": round(rug_risk, 2),
        "graduation_probability_score": round(graduation_probability, 2),
        "expected_upside_range": f"{upside_floor:.1f}x–{upside_ceiling:.1f}x (paper estimate, not a promise)",
        "components": {k: round(v, 3) for k, v in components.items()},
    }


def rug_risk_score(features: dict[str, Any]) -> float:
    liquidity = float(features.get("liquidity_usd") or 0)
    volume_5m = float(features.get("volume_5m") or 0)
    buys = float(features.get("buys_5m") or 0)
    sells = float(features.get("sells_5m") or 0)
    market_cap = float(features.get("market_cap") or 0)
    price_change_5m = abs(float(features.get("price_change_5m") or 0))

    risk = 20.0
    if liquidity < 5_000:
        risk += 35
    elif liquidity < 10_000:
        risk += 20
    if market_cap and liquidity / market_cap < 0.03:
        risk += 20
    if sells > buys * 2 and sells >= 5:
        risk += 20
    if volume_5m > liquidity * 3 and liquidity > 0:
        risk += 15
    if price_change_5m > 80:
        risk += 10
    return max(0.0, min(100.0, risk))
