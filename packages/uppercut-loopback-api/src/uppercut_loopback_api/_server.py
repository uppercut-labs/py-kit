"""A small JSON API on 127.0.0.1 for a private app that a reverse proxy publishes."""

import gzip
import hmac
import json
import os
import socket
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Iterable, Optional, Tuple, Union
from urllib.parse import parse_qs

from ._icon import pixel_mark_png

LOOPBACK = "127.0.0.1"
LOOPBACK_NAMES = frozenset({LOOPBACK, "localhost"})
METHODS = ("GET", "PUT", "POST", "DELETE")
STATIC_PATHS = ("/", "/manifest.webmanifest", "/icon-180.png", "/icon-512.png")
DEFAULT_INDEX_CSP = (
    "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; manifest-src 'self'; frame-ancestors 'none'"
)


class ApiError(Exception):
    """Raise from a route to answer with `{"error": message, **extra}` and `status`."""

    def __init__(self, status: int, message: str, **extra: object) -> None:
        super().__init__(message)
        self.status = status
        self.body = {"error": message, **extra}


def tailnet_hosts(tailnet: str, *names: str) -> FrozenSet[str]:
    """Each name and `name.tailnet`, lower case: the Host headers a tailnet proxy sends.

    `tailnet_hosts("tailnet.example", "studio", "notes")` accepts `studio`,
    `studio.tailnet.example`, `notes` and `notes.tailnet.example`.
    """
    tailnet = tailnet.strip(".").lower()
    hosts = set()
    for name in names:
        name = name.strip(".").lower()
        hosts |= {name, f"{name}.{tailnet}"}
    return frozenset(hosts)


@dataclass
class LoopbackApp:
    """One app's configuration.

    allowed_hosts: Host header names (without port) the app answers. 127.0.0.1 and
        localhost are always accepted; every other Host gets 403, which stops DNS
        rebinding from a web page reaching the loopback port.
    mount: path prefix the proxy publishes the app under, such as "/notes", or "" for
        the root. Requests work with or without it, and a bare "/notes" redirects to "/notes/".
    web_client: serve index.html at "/", a web manifest and two pixel-mark icons.
    log_path / log: where the request log goes (a file, a callable, both or neither).
        Lines hold the method, a known path or "<unknown>", and the status; never query
        strings, bodies, headers or unknown paths.
    gzip_min_bytes: gzip JSON responses at least this long when the client accepts gzip.
    """

    name: str
    port: int
    allowed_hosts: Iterable[str] = ()
    mount: str = ""
    short_name: str = ""
    web_client: bool = True
    index_html: Union[Path, bytes, None] = None
    index_csp: str = DEFAULT_INDEX_CSP
    background: Tuple[int, int, int] = (17, 19, 24)
    accent: Tuple[int, int, int] = (110, 231, 183)
    log_path: Optional[Path] = None
    log: Optional[Callable[[str], None]] = None
    max_body_bytes: int = 64 * 1024
    gzip_min_bytes: Optional[int] = None
    server_version: str = "loopback-api"

    def __post_init__(self) -> None:
        mount = self.mount.strip()
        if mount in ("", "/"):
            mount = ""
        else:
            mount = "/" + mount.strip("/")
        if "?" in mount or "#" in mount:
            raise ValueError("mount must be a plain path such as /notes")
        self.mount = mount
        self.allowed_hosts = frozenset(h.strip(".").lower() for h in self.allowed_hosts)

    @property
    def accepted_hosts(self) -> FrozenSet[str]:
        return frozenset(self.allowed_hosts) | LOOPBACK_NAMES


_log_lock = threading.Lock()


def log_line(app: LoopbackApp, line: str) -> None:
    """Write one line to the app's log. Never pass request bodies, query strings or secrets."""
    if app.log is not None:
        try:
            app.log(line)
        except Exception:  # noqa: BLE001 - logging must never break a request
            pass
    if app.log_path is not None:
        stamp = datetime.now().isoformat(timespec="seconds")
        try:
            with _log_lock, open(app.log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{stamp} {line}\n")
        except OSError:
            pass


def _hex(rgb: Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def manifest_of(app: LoopbackApp) -> bytes:
    """The web app manifest: standalone display, the app's colors and its two icons."""
    return json.dumps({
        "name": app.name, "short_name": app.short_name or app.name,
        "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": _hex(app.background), "theme_color": _hex(app.background),
        "icons": [
            {"src": "icon-180.png", "sizes": "180x180", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }).encode()


def host_name(header: str) -> str:
    """The host part of a Host header, lower case, without port, brackets or a final dot."""
    value = header.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else ""
    if value.count(":") == 1:
        value = value.split(":", 1)[0]
    return value.rstrip(".")


Payload = object
Route = Callable[["LoopbackHandler", Optional[dict]], Tuple[int, Payload]]
Routes = Dict[Tuple[str, str], Route]


class LoopbackHandler(BaseHTTPRequestHandler):
    """Request handler for one app. Build it with make_handler(); routes do the app's work."""

    app: LoopbackApp
    routes: Routes = {}
    index_bytes: bytes = b""
    icons: Dict[str, bytes] = {}
    manifest: bytes = b""
    sys_version = ""

    # The base class would log raw request lines and parser errors, which can hold secrets.
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        pass

    def log_request(self, code: object = "-", size: object = "-") -> None:
        command = getattr(self, "command", None)
        method = command if command in METHODS + ("HEAD", "OPTIONS") else "OTHER"
        shown = "<unknown>"
        if hasattr(self, "path"):
            sub = self.sub_path()
            if sub == "redirect":
                shown = self.app.mount
            elif sub in self.known_paths():
                shown = self.app.mount + sub
        log_line(self.app, f"{self.address_string()} {method} {shown} {code}")

    def known_paths(self) -> set:
        paths = {path for _, path in self.routes}
        return paths | set(STATIC_PATHS) if self.app.web_client else paths

    def host_ok(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        return len(hosts) == 1 and host_name(hosts[0]) in self.app.accepted_hosts

    def sub_path(self) -> str:
        """The request path below the mount, or "redirect" for the bare mount."""
        path = self.path.split("?", 1)[0]
        mount = self.app.mount
        if not mount:
            return path
        if path == mount:
            return "redirect"
        return path[len(mount):] if path.startswith(mount + "/") else path

    def query(self) -> Dict[str, str]:
        """Query parameters, first value of each. Query strings are never logged."""
        raw = self.path.split("?", 1)[1] if "?" in self.path else ""
        return {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}

    def accepts_gzip(self) -> bool:
        for part in (self.headers.get("Accept-Encoding") or "").split(","):
            coding, *params = part.split(";")
            if coding.strip().lower() != "gzip":
                continue
            quality = 1.0
            for param in params:
                key, _, value = param.partition("=")
                if key.strip().lower() == "q":
                    try:
                        quality = float(value)
                    except ValueError:
                        quality = 0.0
            return quality > 0
        return False

    def send(self, status: int, body: object, ctype: str = "application/json",
             extra: Optional[Dict[str, str]] = None) -> None:
        """Send a response with no-store caching and the security headers."""
        headers = dict(extra or {})
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode()
            limit = self.app.gzip_min_bytes
            if limit is not None and len(body) >= limit and self.accepts_gzip():
                body = gzip.compress(body, compresslevel=6)
                headers["Content-Encoding"] = "gzip"
                headers["Vary"] = "Accept-Encoding"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        """One Content-Length, no Transfer-Encoding, a JSON object within app.max_body_bytes."""
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1:
            raise ApiError(400, "one Content-Length is required; Transfer-Encoding is unsupported")
        raw = lengths[0].strip()
        if not (raw.isascii() and raw.isdecimal()):
            raise ApiError(400, "bad body size")
        length = int(raw)
        if length > self.app.max_body_bytes:
            raise ApiError(413, "body too large")
        if length <= 0:
            raise ApiError(400, "bad body size")
        payload = self.rfile.read(length)
        if len(payload) != length:
            raise ApiError(400, "body ended early")
        try:
            body = json.loads(payload)
        except (ValueError, RecursionError):
            raise ApiError(400, "body must be JSON") from None
        if not isinstance(body, dict):
            raise ApiError(400, "body must be a JSON object")
        return body

    def dispatch(self) -> None:
        if not self.host_ok():
            return self.send(403, {"error": "host not allowed"})
        sub = self.sub_path()
        if sub == "redirect":
            return self.send(301, b"", "text/plain", {"Location": self.app.mount + "/"})
        if self.app.web_client and self.command == "GET" and sub in STATIC_PATHS:
            if sub == "/":
                return self.send(200, self.index_bytes, "text/html; charset=utf-8",
                                 {"Content-Security-Policy": self.app.index_csp})
            if sub == "/manifest.webmanifest":
                return self.send(200, self.manifest, "application/manifest+json")
            return self.send(200, self.icons[sub], "image/png")
        route = self.routes.get((self.command, sub))
        if route is None:
            known = any(path == sub for _, path in self.routes)
            return self.send(405 if known else 404,
                             {"error": "method not allowed" if known else "not found"})
        try:
            body = self.read_json() if self.command in ("PUT", "POST") else None
            status, result = route(self, body)
        except ApiError as error:
            return self.send(error.status, error.body)
        except OSError as error:
            log_line(self.app, f"{self.command} {self.app.mount}{sub} failed: {type(error).__name__}")
            return self.send(500, {"error": f"server could not do that: {error.strerror or type(error).__name__}"})
        except Exception as error:  # noqa: BLE001 - a bug in a route still gets a JSON answer
            log_line(self.app, f"{self.command} {self.app.mount}{sub} failed: {type(error).__name__}")
            return self.send(500, {"error": "internal error"})
        return self.send(status, result)

    do_GET = do_PUT = do_POST = do_DELETE = dispatch


def make_handler(app: LoopbackApp, routes: Routes) -> type:
    """Build the handler class for one app. Routes are keyed by (method, path below the mount)."""
    for method, path in routes:
        if method not in METHODS or not path.startswith("/"):
            raise ValueError(f"bad route {(method, path)}: method must be one of {METHODS}, path must start with /")
    index = app.index_html
    if isinstance(index, Path):
        index = index.read_bytes() if index.exists() else None
    if index is None:
        index = f"<!doctype html><title>{app.name}</title><p>{app.name} has no web client yet.</p>".encode()
    mark = app.short_name or app.name
    icons = {f"/icon-{s}.png": pixel_mark_png(mark, s, app.background, app.accent) for s in (180, 512)} \
        if app.web_client else {}
    return type("Handler", (LoopbackHandler,), {
        "app": app, "routes": dict(routes), "index_bytes": bytes(index),
        "icons": icons, "manifest": manifest_of(app) if app.web_client else b"",
        "server_version": app.server_version,
    })


def health_route(check: Optional[Callable[[], dict]] = None, **fields: object) -> Route:
    """GET route answering `{"ok": true, **fields, **check()}`. Clients and installers poll it."""
    def route(_handler: LoopbackHandler, _body: Optional[dict]) -> Tuple[int, dict]:
        return 200, {"ok": True, **fields, **(check() if check else {})}
    return route


def require_bearer(token: str, route: Route) -> Route:
    """Wrap a route so it answers 401 unless `Authorization: Bearer <token>` matches."""
    if not token:
        raise ValueError("token must not be empty")
    expected = token.encode()

    def guarded(handler: LoopbackHandler, body: Optional[dict]) -> Tuple[int, Payload]:
        supplied = handler.headers.get("Authorization") or ""
        if supplied.startswith("Bearer "):
            supplied = supplied[len("Bearer "):]
        if not hmac.compare_digest(supplied.strip().encode(), expected):
            raise ApiError(401, "unauthorized")
        return route(handler, body)
    return guarded


class LoopbackServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that owns its port alone.

    The stdlib server sets SO_REUSEADDR, and on Windows that lets a second server bind
    the same port with no error and take some of the first one's requests. On Windows
    this sets SO_EXCLUSIVEADDRUSE instead, so a second bind fails; elsewhere SO_REUSEADDR
    already refuses a second listener.
    """

    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(app: LoopbackApp, routes: Routes, port: Optional[int] = None) -> LoopbackServer:
    """Bind 127.0.0.1 only (port 0 picks a free one) and return the server, not yet serving."""
    return LoopbackServer((LOOPBACK, app.port if port is None else port), make_handler(app, routes))


def serve(app: LoopbackApp, routes: Routes, port: Optional[int] = None) -> None:
    """Bind 127.0.0.1 only and serve until stopped. A taken port raises OSError."""
    server = make_server(app, routes, port)
    address = f"http://{LOOPBACK}:{server.server_address[1]}{app.mount}/"
    log_line(app, f"listening on {address}")
    if sys.stdout:
        print(f"{app.name} on {address}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
