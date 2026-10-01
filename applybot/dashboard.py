"""Loopback-only HTTP adapter for the local application tracker."""

from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .application_store import ApplicationStore, StoreError

STATIC_ROOT = Path(__file__).resolve().parent / "web"
MAX_BODY = 65536


class TrackerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        store: ApplicationStore,
        port: int = 8421,
        *,
        token: str | None = None,
        legacy_path: Path | None = None,
    ):
        self.store = store
        self.token = token or secrets.token_urlsafe(32)
        if not 32 <= len(self.token) <= 128 or not self.token.isascii():
            raise ValueError("Local access token must contain 32–128 ASCII characters.")
        self.legacy_path = legacy_path
        super().__init__(("127.0.0.1", port), TrackerHandler)


class TrackerHandler(BaseHTTPRequestHandler):
    server_version = "ApplyBotTracker/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, _format, *_args):
        # Request URLs can contain search text. Do not log their contents.
        pass

    def _host(self):
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_address[1]
        if len(hosts) != 1 or hosts[0] not in {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
        }:
            raise StoreError(
                "Use the local tracker address printed in your terminal.", 403
            )
        return hosts[0]

    def _authorize(self, host):
        values = self.headers.get_all("Authorization", [])
        supplied = values[0] if len(values) == 1 else ""
        if (
            len(supplied) > 140
            or not supplied.startswith("Bearer ")
            or not secrets.compare_digest(
                supplied[7:].encode("utf-8"), self.server.token.encode("ascii")
            )
        ):
            raise StoreError("Enter the local access token to open this tracker.", 401)
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{host}":
            raise StoreError("Cross-origin tracker requests are not allowed.", 403)

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            raise StoreError("Chunked request bodies are not supported.", 400)
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            raise StoreError("A valid Content-Length is required.", 411)
        length = int(lengths[0])
        if length > MAX_BODY:
            raise StoreError("Request exceeds 64 KiB.", 413)
        if self.headers.get_content_type() != "application/json":
            raise StoreError("Use application/json.", 415)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise StoreError("Request body was incomplete.")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise StoreError("Request body must be valid UTF-8 JSON.") from None

    def _send(
        self,
        body,
        status=200,
        *,
        content_type="application/json; charset=utf-8",
        extra=None,
    ):
        payload = (
            body
            if isinstance(body, bytes)
            else json.dumps(body, ensure_ascii=False).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.send_header("X-Request-Id", self.request_id)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _handle(self):
        self.request_id = str(uuid.uuid4())
        try:
            host = self._host()
            parsed = urlsplit(self.path)
            assets = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            }
            if parsed.path in assets and self.command == "GET":
                filename, mime = assets[parsed.path]
                self._send((STATIC_ROOT / filename).read_bytes(), content_type=mime)
                return
            self._authorize(host)
            store = self.server.store
            if self.command == "GET" and parsed.path == "/api/meta":
                self._send(
                    {
                        "legacyHistoryAvailable": bool(
                            self.server.legacy_path and self.server.legacy_path.exists()
                        )
                    }
                )
                return
            if self.command != "GET" and parsed.query:
                raise StoreError("Mutation routes do not accept query parameters.")
            if parsed.path == "/api/applications":
                if self.command == "GET":
                    try:
                        query = parse_qs(
                            parsed.query, keep_blank_values=True, max_num_fields=6
                        )
                    except ValueError:
                        raise StoreError("Too many query parameters.") from None
                    if set(query) - {"q", "status", "page"} or any(
                        len(values) != 1 for values in query.values()
                    ):
                        raise StoreError("Unknown or repeated query parameter.")
                    page = query.get("page", ["1"])[0]
                    if not page.isascii() or not page.isdigit() or len(page) > 6:
                        raise StoreError("Page must be a bounded positive integer.")
                    self._send(
                        store.list(
                            query=query.get("q", [""])[0],
                            status=query.get("status", [""])[0],
                            page=int(page),
                        )
                    )
                    return
                if self.command == "POST":
                    result = store.create(self._body())
                    self._send(
                        result,
                        201,
                        extra={"Location": "/api/applications/" + result["id"]},
                    )
                    return
            parts = parsed.path.strip("/").split("/")
            if len(parts) in (3, 4) and parts[:2] == ["api", "applications"]:
                identity = parts[2]
                try:
                    uuid.UUID(identity)
                except ValueError:
                    raise StoreError("Application not found.", 404) from None
                if parsed.query:
                    raise StoreError("This route does not accept query parameters.")
                if len(parts) == 4:
                    if parts[3] == "audit" and self.command == "GET":
                        self._send({"items": store.audit(identity)})
                        return
                elif self.command == "GET":
                    self._send(store.get(identity))
                    return
                elif self.command in {"PUT", "DELETE"}:
                    value = self._body()
                    expected = (
                        {"version", "application"}
                        if self.command == "PUT"
                        else {"version"}
                    )
                    if not isinstance(value, dict) or set(value) != expected:
                        raise StoreError(
                            "Supply the captured version and the editable application for updates."
                        )
                    result = (
                        store.update(identity, value["version"], value["application"])
                        if self.command == "PUT"
                        else store.delete(identity, value["version"])
                    )
                    self._send(result)
                    return
            raise StoreError("Route or method not found.", 404)
        except StoreError as error:
            self._send(
                {"error": str(error), "requestId": self.request_id}, error.status
            )
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (sqlite3.Error, OSError):
            self._send(
                {
                    "error": "The local tracker could not save or read data. Keep your draft and retry after checking the local database.",
                    "requestId": self.request_id,
                },
                503,
            )
        except Exception:
            self._send(
                {
                    "error": "The tracker could not complete this request. Keep your draft and use the request identifier when investigating.",
                    "requestId": self.request_id,
                },
                500,
            )

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle


def run_dashboard(data_dir: str | Path, port: int = 8421):
    directory = Path(data_dir).resolve()
    store = ApplicationStore(directory / "applications.sqlite3")
    server = TrackerServer(store, port, legacy_path=directory / "applications.jsonl")
    address = f"http://127.0.0.1:{server.server_address[1]}/#token={server.token}"
    print("Local application tracker ready. Open this private local link:")
    print(address)
    print("Keep this terminal open. Press Ctrl+C to stop the tracker.")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nTracker stopped.")
    finally:
        server.server_close()
