"""Koyeb Free and low-memory helpers for MemeScout AI."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)
_warned_local_sqlite = False


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def koyeb_free_mode() -> bool:
    return env_bool("KOYEB_FREE_MODE", False)


def low_memory_mode() -> bool:
    return env_bool("MEMESCOUT_LOW_MEMORY_MODE", koyeb_free_mode())


def disable_full_hummingbot_api() -> bool:
    return env_bool("MEMESCOUT_DISABLE_FULL_HUMMINGBOT_API", koyeb_free_mode())


def ensure_data_dir(path: str | Path | None = None) -> Path:
    db_path = Path(path or os.environ.get("MEMESCOUT_DB_PATH", "data/memescout_ai.sqlite"))
    data_dir = db_path.parent
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def warn_if_local_sqlite_on_koyeb(path: str | Path | None = None) -> None:
    global _warned_local_sqlite
    if not koyeb_free_mode() or _warned_local_sqlite:
        return
    db_path = Path(path or os.environ.get("MEMESCOUT_DB_PATH", "data/memescout_ai.sqlite"))
    logger.warning(
        "KOYEB_FREE_MODE=true with local SQLite path %s. Koyeb Free has no persistent volume; "
        "MemeScout paper data may be lost on redeploy, restart, or scale-to-zero. Use /memescout_backup often.",
        db_path,
    )
    _warned_local_sqlite = True


def log_memory_usage() -> None:
    try:
        import psutil

        process = psutil.Process(os.getpid())
        rss_mb = process.memory_info().rss / (1024 * 1024)
        logger.info("MemeScout memory usage: %.1f MB RSS", rss_mb)
    except Exception as exc:
        logger.debug("Memory usage logging skipped: %s", exc)


def startup_resource_checks() -> None:
    data_dir = ensure_data_dir()
    warn_if_local_sqlite_on_koyeb()
    log_memory_usage()
    if koyeb_free_mode():
        logger.info("Koyeb Free mode active; data directory ready at %s", data_dir)
