"""In-process MemeScout loop manager for Koyeb Free mode.

This intentionally manages only MemeScout paper-only scanner/monitor loops and
never calls Condor routines, trading handlers, Gateway, or Hummingbot order APIs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from .koyeb import env_bool
from .monitor import monitor_once
from .settings import get_settings
from .store import MemeScoutStore

logger = logging.getLogger(__name__)
_scanner_task: asyncio.Task | None = None
_monitor_task: asyncio.Task | None = None


@dataclass
class LoopContext:
    bot: Any = None
    _chat_id: int | None = None


async def _scanner_loop(context: Any, store: MemeScoutStore | None = None) -> None:
    from routines.memescout_ai import Config, scan_once_summary

    store = store or MemeScoutStore()
    while True:
        settings = get_settings()
        try:
            summary = await scan_once_summary(
                Config(interval_seconds=settings.scan_interval_seconds, max_pairs_per_scan=10),
                context,
                store,
                getattr(context, "_chat_id", None),
            )
            store.set_state("last_scan_summary", summary.to_json())
            store.set_state("last_scan_error", summary.scanner_error or "")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("MemeScout scanner loop survived error: %s", exc)
            store.set_state("last_scan_error", str(exc)[:250])
        await asyncio.sleep(max(60, settings.scan_interval_seconds))


async def _monitor_loop(store: MemeScoutStore | None = None) -> None:
    store = store or MemeScoutStore()
    while True:
        settings = get_settings()
        try:
            result = await monitor_once(store)
            store.set_state("last_monitor_at", str(time.time()))
            store.set_state("last_monitor_error", "")
            store.set_state("last_monitor_summary", str(result))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("MemeScout monitor loop survived error: %s", exc)
            store.set_state("last_monitor_error", str(exc)[:250])
        await asyncio.sleep(max(10, settings.monitor_interval_seconds))


def scanner_loop_running() -> bool:
    return _scanner_task is not None and not _scanner_task.done()


def monitor_loop_running() -> bool:
    return _monitor_task is not None and not _monitor_task.done()


def start_scanner_loop(context: Any, store: MemeScoutStore | None = None) -> bool:
    global _scanner_task
    if scanner_loop_running():
        return False
    _scanner_task = asyncio.create_task(_scanner_loop(context, store), name="memescout-scanner-loop")
    return True


def start_monitor_loop(store: MemeScoutStore | None = None) -> bool:
    global _monitor_task
    if monitor_loop_running():
        return False
    _monitor_task = asyncio.create_task(_monitor_loop(store), name="memescout-monitor-loop")
    return True


def stop_scanner_loop() -> bool:
    global _scanner_task
    if not scanner_loop_running():
        _scanner_task = None
        return False
    _scanner_task.cancel()
    _scanner_task = None
    return True


def stop_monitor_loop() -> bool:
    global _monitor_task
    if not monitor_loop_running():
        _monitor_task = None
        return False
    _monitor_task.cancel()
    _monitor_task = None
    return True


def loop_status(store: MemeScoutStore | None = None) -> dict[str, Any]:
    store = store or MemeScoutStore()
    settings = get_settings()

    def _float_state(key: str) -> float | None:
        raw = store.get_state(key, "")
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    now = time.time()
    last_scan = _float_state("last_scan_at")
    last_monitor = _float_state("last_monitor_at")
    next_scan = max(0, int(last_scan + settings.scan_interval_seconds - now)) if last_scan and scanner_loop_running() else None
    next_monitor = max(0, int(last_monitor + settings.monitor_interval_seconds - now)) if last_monitor and monitor_loop_running() else None
    return {
        "scanner_loop_running": scanner_loop_running(),
        "monitor_loop_running": monitor_loop_running(),
        "last_scan_at": last_scan,
        "last_monitor_at": last_monitor,
        "last_scan_error": store.get_state("last_scan_error", ""),
        "last_monitor_error": store.get_state("last_monitor_error", ""),
        "next_scan_eta_seconds": next_scan,
        "next_monitor_eta_seconds": next_monitor,
    }


def autostart_enabled_scanner() -> bool:
    return env_bool("MEMESCOUT_AUTOSTART_SCANNER", False)


def autostart_enabled_monitor() -> bool:
    return env_bool("MEMESCOUT_AUTOSTART_MONITOR", False)


def autostart_loops(bot: Any = None, chat_id: int | None = None, store: MemeScoutStore | None = None) -> dict[str, bool]:
    context = LoopContext(bot=bot, _chat_id=chat_id)
    started_scanner = start_scanner_loop(context, store) if autostart_enabled_scanner() else False
    started_monitor = start_monitor_loop(store) if autostart_enabled_monitor() else False
    return {"scanner_started": started_scanner, "monitor_started": started_monitor}
