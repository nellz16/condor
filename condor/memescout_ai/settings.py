"""Configuration helpers for MemeScout AI.

MemeScout is intentionally paper-only. Environment variables enable optional
explainers and data enrichments, never real trading.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .koyeb import koyeb_free_mode


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class MemeScoutSettings:
    database_path: Path = field(default_factory=lambda: Path(os.environ.get("MEMESCOUT_DB_PATH", "data/memescout_ai.sqlite")))
    paper_only: bool = field(default_factory=lambda: os.environ.get("MEMESCOUT_PAPER_ONLY", "true").lower() != "false")
    default_balance_usdc: float = field(default_factory=lambda: _env_float("MEMESCOUT_PAPER_BALANCE_USDC", 100.0))
    max_signals_per_hour: int = field(default_factory=lambda: _env_int("MEMESCOUT_MAX_SIGNALS_PER_HOUR", 6))
    duplicate_signal_window_seconds: int = field(default_factory=lambda: _env_int("MEMESCOUT_DUPLICATE_SIGNAL_WINDOW_SECONDS", 3600))
    llm_max_calls_per_hour: int = field(default_factory=lambda: _env_int("MEMESCOUT_LLM_MAX_CALLS_PER_HOUR", 20))
    dex_request_min_interval_seconds: float = field(default_factory=lambda: _env_float("MEMESCOUT_DEX_REQUEST_MIN_INTERVAL_SECONDS", 1.05))
    scan_interval_seconds: int = field(default_factory=lambda: _env_int("MEMESCOUT_SCAN_INTERVAL_SECONDS", 300))
    slippage_bps: int = field(default_factory=lambda: _env_int("MEMESCOUT_SLIPPAGE_BPS", 150))
    trade_size_usdc: float = field(default_factory=lambda: _env_float("MEMESCOUT_TRADE_SIZE_USDC", 10.0))
    stop_loss_pct: float = field(default_factory=lambda: _env_float("MEMESCOUT_STOP_LOSS_PCT", -35.0))
    llm_provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "").strip().lower())
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    openrouter_api_key: str = field(default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", ""))
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite"))
    helius_api_key: str = field(default_factory=lambda: os.environ.get("HELIUS_API_KEY", ""))
    monitor_enabled: bool = field(default_factory=lambda: os.environ.get("MEMESCOUT_MONITOR_ENABLED", "true").lower() != "false")
    monitor_interval_seconds: int = field(default_factory=lambda: _env_int("MEMESCOUT_MONITOR_INTERVAL_SECONDS", 120 if koyeb_free_mode() else 60))
    trailing_stop_pct: float = field(default_factory=lambda: _env_float("MEMESCOUT_TRAILING_STOP_PCT", 30.0))
    allow_risk_reducing_closes_during_emergency: bool = field(default_factory=lambda: os.environ.get("MEMESCOUT_ALLOW_RISK_REDUCING_CLOSES_DURING_EMERGENCY", "true").lower() != "false")
    autostart_scanner: bool = field(default_factory=lambda: os.environ.get("MEMESCOUT_AUTOSTART_SCANNER", "false").lower() == "true")
    autostart_monitor: bool = field(default_factory=lambda: os.environ.get("MEMESCOUT_AUTOSTART_MONITOR", "false").lower() == "true")


def get_settings() -> MemeScoutSettings:
    return MemeScoutSettings()


def mask_secret(value: str | None) -> str:
    if not value:
        return "not set"
    return f"{value[:4]}...{value[-4:]}" if len(value) > 8 else "****"
