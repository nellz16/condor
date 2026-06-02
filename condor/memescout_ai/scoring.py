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


SUPPORTED_STRATEGIES = (
    "fresh_launch",
    "momentum_continuation",
    "pullback_reentry",
    "liquidity_expansion",
    "boost_anomaly",
    "rug_defense_only",
)


def _buy_pressure(features: dict[str, Any]) -> float:
    buys = float(features.get("buys_5m") or 0)
    sells = float(features.get("sells_5m") or 0)
    return clamp((buys - sells) / max(buys + sells, 1) * 0.5 + 0.5)


def _base_rejections(features: dict[str, Any], rug_risk: float) -> list[str]:
    liquidity = float(features.get("liquidity_usd") or 0)
    volume_5m = float(features.get("volume_5m") or 0)
    volume_1h = float(features.get("volume_1h") or 0)
    buys = float(features.get("buys_5m") or 0)
    sells = float(features.get("sells_5m") or 0)
    reasons: list[str] = []
    if rug_risk >= 70:
        reasons.append("rug risk is too high")
    if liquidity < 10_000:
        reasons.append("liquidity is below $10,000")
    if volume_5m <= 0 and volume_1h <= 0:
        reasons.append("recent volume is missing")
    if sells > buys * 2 and sells >= 10:
        reasons.append("sell pressure is too high")
    return reasons


def score_strategy_signal(features: dict[str, Any], strategy_id: str, weights: dict[str, float] | None = None) -> dict[str, Any]:
    """Deterministic strategy scorer. LLMs may explain this output, never override it."""
    if strategy_id not in SUPPORTED_STRATEGIES:
        strategy_id = "rug_defense_only"
    liquidity = float(features.get("liquidity_usd") or 0)
    volume_5m = float(features.get("volume_5m") or 0)
    volume_1h = float(features.get("volume_1h") or 0)
    buys = float(features.get("buys_5m") or 0)
    sells = float(features.get("sells_5m") or 0)
    age = float(features.get("age_minutes") or 0)
    market_cap = float(features.get("market_cap") or 0)
    pc5 = float(features.get("price_change_5m") or 0)
    pc1h = float(features.get("price_change_1h") or 0)
    boosted = bool(features.get("boosted") or features.get("boost_amount") or features.get("boost_total_amount"))
    organic_volume = volume_5m + volume_1h * 0.2
    buy_pressure = _buy_pressure(features)
    rug = rug_risk_score(features)
    volume_liquidity = organic_volume / max(liquidity, 1)
    rejections = _base_rejections(features, rug)
    why = ""
    fail = "Memecoins can reverse quickly even when this deterministic setup looks healthy."
    risk = "Liquidity/rug risk can change quickly."

    if strategy_id == "fresh_launch":
        components = {
            "young_age": clamp((180 - age) / 180),
            "early_liquidity": clamp(liquidity / 80_000),
            "buy_pressure": buy_pressure,
            "rug_safety": clamp(1 - rug / 100),
            "early_volume": clamp(organic_volume / 40_000),
        }
        score = (components["young_age"] * 0.25 + components["early_liquidity"] * 0.22 + components["buy_pressure"] * 0.23 + components["rug_safety"] * 0.20 + components["early_volume"] * 0.10) * 100
        if age > 240:
            rejections.append("too old for fresh_launch")
        why = "Very new pair with early liquidity, buy pressure, and acceptable rug-risk checks."
        risk = "Fresh launches often fail from dev sells, thin liquidity, or early hype fading."
    elif strategy_id == "momentum_continuation":
        weakening = pc1h > 180 and sells >= buys * 0.75
        components = {
            "volume_acceleration": clamp(volume_5m / max(volume_1h / 12, 1) / 3),
            "liquidity": clamp(liquidity / 150_000),
            "buy_pressure": buy_pressure,
            "momentum": clamp((pc5 + 10) / 60),
            "rug_safety": clamp(1 - rug / 100),
        }
        score = (components["volume_acceleration"] * 0.26 + components["liquidity"] * 0.20 + components["buy_pressure"] * 0.24 + components["momentum"] * 0.15 + components["rug_safety"] * 0.15) * 100
        if weakening:
            score -= 28
            rejections.append("extreme 1h pump with weakening buy/sell ratio")
        why = "Momentum continuation matched because volume acceleration and buy pressure remain active."
        risk = "Momentum entries can fail when late buyers exhaust or sellers take profit."
    elif strategy_id == "pullback_reentry":
        renewed = buys > sells * 1.5 and buys >= 8
        pulled_back = pc1h >= 25 and -35 <= pc5 <= 8
        components = {
            "prior_pump": clamp(pc1h / 120),
            "pullback": 1.0 if pulled_back else 0.0,
            "renewed_buy_pressure": buy_pressure,
            "liquidity_present": clamp(liquidity / 100_000),
            "volume_alive": clamp(volume_5m / 10_000),
            "rug_safety": clamp(1 - rug / 100),
        }
        score = (components["prior_pump"] * 0.15 + components["pullback"] * 0.20 + components["renewed_buy_pressure"] * 0.25 + components["liquidity_present"] * 0.15 + components["volume_alive"] * 0.15 + components["rug_safety"] * 0.10) * 100
        if not renewed:
            rejections.append("pullback lacks renewed buy pressure")
        if not pulled_back:
            rejections.append("not a healthy pullback after pump")
        if volume_5m < 2_000:
            rejections.append("volume is too dead for pullback_reentry")
        why = "Pullback re-entry matched because a prior pump cooled off while buy pressure returned."
        risk = "Pullbacks can become full reversals if buyers do not keep returning."
    elif strategy_id == "liquidity_expansion":
        components = {
            "liquidity": clamp(liquidity / 200_000),
            "slippage": clamp((500 - float(features.get("slippage_estimate_bps") or 500)) / 350),
            "volume_liquidity_ratio": clamp(volume_liquidity / 1.2),
            "market_cap_room": clamp((2_000_000 - market_cap) / 2_000_000) if market_cap else 0.5,
            "buy_pressure": buy_pressure,
            "rug_safety": clamp(1 - rug / 100),
        }
        score = (components["liquidity"] * 0.20 + components["slippage"] * 0.20 + components["volume_liquidity_ratio"] * 0.20 + components["market_cap_room"] * 0.15 + components["buy_pressure"] * 0.15 + components["rug_safety"] * 0.10) * 100
        if market_cap > 2_000_000:
            rejections.append("market cap is too high for liquidity_expansion")
        if not 0.05 <= volume_liquidity <= 2.5:
            rejections.append("volume/liquidity ratio is unhealthy")
        why = "Liquidity expansion matched because liquidity/slippage and volume/liquidity look healthier."
        risk = "Liquidity can disappear quickly, invalidating slippage assumptions."
    elif strategy_id == "boost_anomaly":
        components = {
            "boosted": 1.0 if boosted else 0.0,
            "organic_volume": clamp(organic_volume / 50_000),
            "buy_pressure": buy_pressure,
            "liquidity": clamp(liquidity / 100_000),
            "rug_safety": clamp(1 - rug / 100),
        }
        score = (components["boosted"] * 0.12 + components["organic_volume"] * 0.30 + components["buy_pressure"] * 0.25 + components["liquidity"] * 0.18 + components["rug_safety"] * 0.15) * 100
        if not boosted:
            rejections.append("no boost/ad anomaly metadata")
        if boosted and organic_volume < 5_000:
            score -= 25
            rejections.append("paid boost has weak organic volume")
        why = "Boost anomaly matched because boost metadata was present and organic activity was checked separately."
        risk = "Paid boosts can attract attention without real demand."
    else:
        components = {"rug_risk": rug / 100, "sell_pressure": 1 - buy_pressure, "thin_liquidity": clamp((10_000 - liquidity) / 10_000)}
        score = min(100, rug + components["sell_pressure"] * 25 + components["thin_liquidity"] * 25)
        rejections.append("rug_defense_only never sends approval signals")
        why = "Risk-only classifier stored this for future rug-defense learning."
        risk = "This token is classified for risk tracking, not approval."
        fail = "Risk-only records are intentionally ineligible."

    eligible = score >= 62 and not rejections and strategy_id != "rug_defense_only"
    return {
        "strategy_id": strategy_id,
        "score": round(max(0, min(100, score)), 2),
        "eligible": eligible,
        "reject_reason": "; ".join(rejections) if rejections else None,
        "rug_risk_score": round(rug, 2),
        "graduation_probability_score": round(clamp(score / 100 * (1 - rug / 140)) * 100, 2),
        "expected_upside_range": "1.2x–4.0x (paper estimate, not a promise)",
        "components": {k: round(v, 3) for k, v in components.items()},
        "strategy_match_reason": why,
        "main_risk": risk,
        "main_failure_reason": fail,
    }


def candidate_strategies(features: dict[str, Any]) -> list[str]:
    strategies: list[str] = []
    age = float(features.get("age_minutes") or 0)
    pc5 = float(features.get("price_change_5m") or 0)
    pc1h = float(features.get("price_change_1h") or 0)
    liquidity = float(features.get("liquidity_usd") or 0)
    volume_5m = float(features.get("volume_5m") or 0)
    boosted = bool(features.get("boosted") or features.get("boost_amount") or features.get("boost_total_amount"))
    if age <= 240:
        strategies.append("fresh_launch")
    if pc5 >= 8 or volume_5m >= 10_000:
        strategies.append("momentum_continuation")
    if pc1h >= 25 and -35 <= pc5 <= 8:
        strategies.append("pullback_reentry")
    if liquidity >= 25_000 and (volume_5m / max(liquidity, 1)) >= 0.03:
        strategies.append("liquidity_expansion")
    if boosted:
        strategies.append("boost_anomaly")
    strategies.append("rug_defense_only")
    return list(dict.fromkeys(strategies))
