"""A small JSON API on 127.0.0.1 for a private app that a reverse proxy such as Tailscale Serve publishes."""

from ._icon import GLYPHS, pixel_mark_png
from ._server import (
    LOOPBACK,
    ApiError,
    LoopbackApp,
    LoopbackHandler,
    LoopbackServer,
    Route,
    Routes,
    health_route,
    host_name,
    log_line,
    make_handler,
    make_server,
    manifest_of,
    require_bearer,
    serve,
    tailnet_hosts,
)

__all__ = [
    "GLYPHS",
    "LOOPBACK",
    "ApiError",
    "LoopbackApp",
    "LoopbackHandler",
    "LoopbackServer",
    "Route",
    "Routes",
    "health_route",
    "host_name",
    "log_line",
    "make_handler",
    "make_server",
    "manifest_of",
    "pixel_mark_png",
    "require_bearer",
    "serve",
    "tailnet_hosts",
]
__version__ = "0.1.0"
