"""Lightweight stdlib HTTP health server for Koyeb/UptimeRobot."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .koyeb import startup_resource_checks
from .settings import get_settings
from .store import MemeScoutStore

logger = logging.getLogger(__name__)
_START_TIME = time.time()
_server: ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None


def status_payload(store: MemeScoutStore | None = None) -> dict[str, Any]:
    settings = get_settings()
    store = store or MemeScoutStore()
    stats = store.stats()
    last_scan_raw = store.get_state("last_scan_at", "")
    payload: dict[str, Any] = {
        "app": "condor-memescout",
        "paper_only": True,
        "scanner_enabled": not store.bool_state("paused"),
        "monitor_enabled": bool(settings.monitor_enabled),
        "emergency_stop": store.bool_state("emergency_stop"),
        "uptime_seconds": int(time.time() - _START_TIME),
        "open_positions_count": int(stats.get("open_trades", 0)),
        "last_scan_at": float(last_scan_raw) if last_scan_raw else None,
    }
    return payload


class MemeScoutHealthHandler(BaseHTTPRequestHandler):
    server_version = "MemeScoutHealth/1.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send_text(200, "OK\n")
        elif path == "/status":
            self._send_json(200, status_payload())
        elif path == "/":
            self._send_json(200, {"app": "condor-memescout", "health": "/healthz", "status": "/status"})
        else:
            self._send_json(404, {"error": "not_found"})

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("health server: " + fmt, *args)

    def _send_text(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def start_health_server(port: int | None = None) -> ThreadingHTTPServer:
    global _server, _thread
    if _server is not None:
        return _server
    startup_resource_checks()
    port = int(port or os.environ.get("PORT", "8000"))
    _server = ThreadingHTTPServer(("0.0.0.0", port), MemeScoutHealthHandler)
    _thread = threading.Thread(target=_server.serve_forever, name="memescout-health", daemon=True)
    _thread.start()
    logger.info("MemeScout health server listening on port %s", port)
    return _server


def stop_health_server() -> None:
    global _server, _thread
    if _server is not None:
        _server.shutdown()
        _server.server_close()
    if _thread is not None and _thread.is_alive():
        _thread.join(timeout=2)
    _server = None
    _thread = None
