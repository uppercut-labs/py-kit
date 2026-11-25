# uppercut-loopback-api

A small JSON API server for a private app that runs on one machine and is reached through a reverse proxy, such as Tailscale Serve, from phones and other computers. It binds 127.0.0.1 only and owns its port alone, refuses Host headers it doesn't know, reads request bodies strictly, answers every error as JSON, and keeps secrets out of its request log. It can also serve a single-page web client with a web manifest and generated icons, so the app installs as a PWA. Standard library only, Python 3.9 or later.

```sh
pip install uppercut-loopback-api
```

```python
from pathlib import Path
from uppercut_loopback_api import ApiError, LoopbackApp, health_route, serve, tailnet_hosts

APP = LoopbackApp(
    name="Notes Remote",
    short_name="NTS",                      # drawn on the icons in a 5x7 pixel font
    mount="/notes",                        # the path the proxy publishes the app under
    port=8890,
    allowed_hosts=tailnet_hosts("tailnet.example", "studio"),
    index_html=Path("web/index.html"),
    log_path=Path("notes-remote.log"),
)

NOTES = {"a": "first note"}


def get_note(handler, _body):
    note_id = handler.query().get("id", "")
    if note_id not in NOTES:
        raise ApiError(404, "no such note")
    return 200, {"id": note_id, "text": NOTES[note_id]}


def put_note(_handler, body):
    if not isinstance(body.get("id"), str) or not isinstance(body.get("text"), str):
        raise ApiError(400, 'body must be {"id": str, "text": str}')
    NOTES[body["id"]] = body["text"]
    return 200, {"saved": True}


ROUTES = {
    ("GET", "/api/health"): health_route(lambda: {"notes": len(NOTES)}, app="notes"),
    ("GET", "/api/note"): get_note,
    ("PUT", "/api/note"): put_note,
}

if __name__ == "__main__":
    serve(APP, ROUTES)
```

Then publish it to your tailnet, for example with `tailscale serve --bg --set-path=/notes http://127.0.0.1:8890/notes`.

## What it does for every request

- **Host check.** Only the names in `allowed_hosts`, plus `127.0.0.1` and `localhost`, are answered; anything else, a missing Host or two Host headers get `403`. This stops a web page from reaching the loopback port through DNS rebinding.
- **Mount.** Routes work under the mount (`/notes/api/note`) and without it (`/api/note`), so a dedicated hostname can proxy to the root. A bare `/notes` redirects to `/notes/`. Use `mount=""` for an app at the root.
- **Strict bodies.** `PUT` and `POST` need exactly one decimal `Content-Length` within `max_body_bytes`, no `Transfer-Encoding`, and a JSON object. Anything else is `400` (or `413` when too long) before your route runs.
- **JSON errors.** Raise `ApiError(status, message, **extra)` for `{"error": message, **extra}`. An `OSError` becomes `500` with its reason; any other exception becomes `500 {"error": "internal error"}` and only its type is logged.
- **Headers.** Every response has `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`; the web client also gets a Content-Security-Policy (`index_csp`).
- **Request log.** One line per request: client address, method, the mount plus a known path (or `<unknown>`), and the status. Query strings, bodies, headers, unknown paths and parser error text are never written.
- **Exclusive port.** On Windows the stdlib server sets `SO_REUSEADDR`, which lets a second copy bind the same port without an error and take some of the first one's requests. `LoopbackServer` sets `SO_EXCLUSIVEADDRUSE` instead, so a second copy fails to start.

## API

| Name | What it is |
| --- | --- |
| `LoopbackApp(name, port, allowed_hosts=(), mount="", short_name="", web_client=True, index_html=None, index_csp=..., background=(17, 19, 24), accent=(110, 231, 183), log_path=None, log=None, max_body_bytes=65536, gzip_min_bytes=None, server_version="loopback-api")` | One app's configuration. `index_html` is a path or bytes; a missing file serves a placeholder. `log` is a callable that gets each log line, for example a `logging` method. `gzip_min_bytes` turns on gzip for JSON responses at least that long when the client accepts it. |
| `tailnet_hosts(tailnet, *names)` | `{name, name.tailnet}` for each name, for `allowed_hosts` |
| `serve(app, routes, port=None)` | Bind 127.0.0.1 and serve until stopped |
| `make_server(app, routes, port=None)` | The bound `LoopbackServer`, not yet serving (port `0` picks a free one; useful in tests) |
| `make_handler(app, routes)` | The request handler class |
| `health_route(check=None, **fields)` | A route answering `{"ok": true, **fields, **check()}` |
| `require_bearer(token, route)` | Wraps a route so it answers `401` unless `Authorization: Bearer <token>` matches (constant-time compare) |
| `ApiError(status, message, **extra)` | Raise from a route for a JSON error |
| `log_line(app, line)` | Write a line to the app's log yourself; never pass secrets |
| `pixel_mark_png(text, size, background, foreground)` | A square PNG with up to four letters or digits in the pixel font |
| `LoopbackServer`, `LoopbackHandler`, `host_name`, `manifest_of`, `GLYPHS`, `LOOPBACK` | The pieces the above are built from |

Routes are a dict keyed by `(method, path)`, where the method is `GET`, `PUT`, `POST` or `DELETE` and the path is below the mount. A route is called as `route(handler, body)`, where `body` is the parsed JSON object for `PUT` and `POST` and `None` otherwise, and returns `(status, payload)` with a JSON-serializable payload. The handler gives routes `handler.query()` (first value of each query parameter) and `handler.headers`.

## License

MIT
