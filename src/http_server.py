#!/usr/bin/env python3
"""Local, read-only status dashboard for an AI-DLC v2 project.

    python3 server.py --project /path/to/aidlc-project   (launcher at the repo root)
    python3 src/http_server.py --project /path/to/aidlc-project

Binds to 127.0.0.1 only. It never writes to the project.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import sys
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import reader
from version import PULSE_VERSION

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"
APP_JS_FILE = STATIC_DIR / "app.js"
CHEATSHEET_FILE = STATIC_DIR / "cheatsheet.html"
CHEATSHEET_JS_FILE = STATIC_DIR / "cheatsheet.js"
APP_CSS_FILE = STATIC_DIR / "app.css"
CHEATSHEET_CSS_FILE = STATIC_DIR / "cheatsheet.css"
HTML_TYPE = "text/html; charset=utf-8"
JS_TYPE = "text/javascript; charset=utf-8"
CSS_TYPE = "text/css; charset=utf-8"
# Fixed static files; there is no generic file server.
STATIC_ROUTES = {
    "/": (INDEX_FILE, HTML_TYPE), "/index.html": (INDEX_FILE, HTML_TYPE),
    "/app.js": (APP_JS_FILE, JS_TYPE), "/app.css": (APP_CSS_FILE, CSS_TYPE),
    "/cheatsheet": (CHEATSHEET_FILE, HTML_TYPE), "/cheatsheet.html": (CHEATSHEET_FILE, HTML_TYPE),
    "/cheatsheet.js": (CHEATSHEET_JS_FILE, JS_TYPE), "/cheatsheet.css": (CHEATSHEET_CSS_FILE, CSS_TYPE),
}
MAX_QUERY_LEN = 2048  # percent-encoded non-ASCII artifact paths are ~3x their character length
MAX_STALL_MINUTES = 1440
# Scripts and styles load only from same-origin files. The UI sets element styles through the CSSOM
# (el.style), which style-src 'self' allows; style attributes and inline <style> blocks are refused.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

log = logging.getLogger("aidlc-pulse")


def _log(level: int, msg: str, **fields: Any) -> None:
    log.log(level, json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "msg": msg, **fields}))


class StatusHandler(BaseHTTPRequestHandler):
    project: Path
    allowed_hosts: frozenset[str]
    stall_minutes: int = reader.insights.DEFAULT_STALL_MINUTES
    harness: str | None = None
    server_version = f"aidlc-pulse/{PULSE_VERSION}"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 (stdlib name)
        cid = uuid.uuid4().hex[:12]
        if not self._host_ok():
            self._send_json({"error": "Host not allowed"}, HTTPStatus.FORBIDDEN, cid)
            return
        url = urlparse(self.path)
        if len(url.query) > MAX_QUERY_LEN:
            self._send_json({"error": "Query too long"}, HTTPStatus.REQUEST_URI_TOO_LONG, cid)
            return
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            self._route(url.path, query, cid)
        except (reader.RecordNotFound, reader.ArtifactRefused) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND, cid)
        except Exception as exc:  # never leak internals to the browser; full detail stays in the server log
            _log(logging.ERROR, "request_failed", cid=cid, path=url.path, error=repr(exc))
            log.debug("traceback cid=%s", cid, exc_info=True)
            self._send_json({"error": "Internal error", "correlationId": cid}, HTTPStatus.INTERNAL_SERVER_ERROR, cid)

    def _route(self, path: str, query: dict[str, str], cid: str) -> None:
        if path in STATIC_ROUTES:
            file, ctype = STATIC_ROUTES[path]
            self._send(file.read_bytes(), ctype, HTTPStatus.OK, cid)
        elif path == "/api/status":
            self._send_json(reader.build_status(self.project, query.get("record"), self.stall_minutes, self.harness),
                            HTTPStatus.OK, cid)
        elif path == "/api/audit":
            self._send_json(reader.build_audit(self.project, query.get("record"), self.harness), HTTPStatus.OK, cid)
        elif path == "/api/artifact":
            self._send_json(reader.read_artifact(self.project, query.get("record"), query.get("path", "")), HTTPStatus.OK, cid)
        elif path == "/healthz":
            self._send_json({"ok": True, "project": self.project.is_dir()}, HTTPStatus.OK, cid)
        else:
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND, cid)

    def _host_ok(self) -> bool:
        """Block DNS-rebinding: only loopback Host headers are served."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in self.allowed_hosts

    def _send_json(self, body: Any, status: HTTPStatus, cid: str) -> None:
        self._send(json.dumps(body, default=str).encode(), "application/json", status, cid)

    def _send(self, data: bytes, ctype: str, status: HTTPStatus, cid: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Correlation-Id", cid)
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        _log(logging.INFO, "http", client=self.client_address[0], line=fmt % args)


def _loopback(host: str) -> str:
    try:
        # is_loopback is a property of IPv4Address/IPv6Address, not a method, so no call is needed.
        if not ipaddress.ip_address(host).is_loopback:  # nosemgrep: is-function-without-parentheses
            raise ValueError
    except ValueError:
        raise SystemExit(f"--host must be a loopback address (got {host!r}); this dashboard has no authentication.")
    return host


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, type=Path, help="AI-DLC project root (contains .kiro/ and aidlc/)")
    parser.add_argument("--host", default=DEFAULT_HOST, type=_loopback, help="loopback address to bind (default 127.0.0.1)")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help=f"port (default {DEFAULT_PORT})")
    parser.add_argument("--stall-minutes", default=reader.insights.DEFAULT_STALL_MINUTES, type=int,
                        help="flag a working stage as possibly stalled after this many minutes with no audit activity (default 15)")
    parser.add_argument("--harness", choices=reader.HARNESS_DIRS,
                        help="harness dir to read when several are installed (default: the first found)")
    args = parser.parse_args(argv)
    args.project = args.project.expanduser().resolve()
    if not args.project.is_dir():
        parser.error(f"project not found: {args.project}")
    if not (1 <= args.port <= 65535):
        parser.error("port must be 1-65535")
    if not (1 <= args.stall_minutes <= MAX_STALL_MINUTES):
        parser.error(f"--stall-minutes must be 1-{MAX_STALL_MINUTES}")
    return args


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    args = parse_args(sys.argv[1:] if argv is None else argv)
    StatusHandler.project = args.project
    StatusHandler.stall_minutes = args.stall_minutes
    StatusHandler.harness = args.harness
    StatusHandler.allowed_hosts = frozenset({"127.0.0.1", "localhost", "::1", args.host})
    httpd = ThreadingHTTPServer((args.host, args.port), StatusHandler)
    _log(logging.INFO, "listening", url=f"http://{args.host}:{args.port}/", project=str(args.project), mode="read-only")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        _log(logging.INFO, "stopped")


if __name__ == "__main__":
    main()
