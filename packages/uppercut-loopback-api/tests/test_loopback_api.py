import gzip
import http.client
import json
import re
import socket
import threading
from pathlib import Path

import pytest

import uppercut_loopback_api as api
from uppercut_loopback_api import ApiError, LoopbackApp

TAILNET = "tailnet.example"
SECRET = "TEST-ONLY-SECRET-9f3a"


class Running:
    def __init__(self, app, routes):
        self.app = app
        self.server = api.make_server(app, routes, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None, host="127.0.0.1"):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            if host is not None:
                conn.putheader("Host", host)
            headers = headers or {}
            for key, value in headers.items():
                for one in value if isinstance(value, list) else [value]:
                    conn.putheader(key, one)
            if body is not None and "Content-Length" not in headers and "Transfer-Encoding" not in headers:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def json(self, method, path, payload=None, **kw):
        body = json.dumps(payload).encode() if payload is not None else None
        status, headers, raw = self.request(method, path, body, **kw)
        return status, (json.loads(raw) if raw else None)


def echo(_handler, body):
    return 200, {"got": body}


def boom_os(_handler, _body):
    raise PermissionError(13, "Access is denied")


def boom_bug(_handler, _body):
    raise KeyError(SECRET)


def conflict(_handler, _body):
    raise ApiError(409, "changed", revision="abc")


def query_route(handler, _body):
    return 200, handler.query()


@pytest.fixture
def tmp_log(tmp_path):
    return tmp_path / "requests.log"


@pytest.fixture
def app(tmp_path, tmp_log):
    index = tmp_path / "index.html"
    index.write_text("<!doctype html><html><title>Notes</title></html>", encoding="utf-8")
    return LoopbackApp(
        name="Notes Remote", short_name="NTS", mount="/notes", port=0,
        allowed_hosts=api.tailnet_hosts(TAILNET, "studio"), index_html=index, log_path=tmp_log,
        max_body_bytes=1024,
    )


@pytest.fixture
def routes(app):
    return {
        ("GET", "/api/health"): api.health_route(lambda: {"exists": True}, app="notes"),
        ("PUT", "/api/echo"): echo,
        ("POST", "/api/echo"): echo,
        ("GET", "/api/os"): boom_os,
        ("GET", "/api/bug"): boom_bug,
        ("GET", "/api/conflict"): conflict,
        ("GET", "/api/query"): query_route,
        ("GET", "/api/secret"): api.require_bearer(SECRET, api.health_route()),
        ("GET", "/api/big"): lambda h, b: (200, {"items": ["x" * 50] * 100}),
    }


@pytest.fixture
def run(app, routes):
    running = Running(app, routes)
    yield running
    running.close()


# ---------------------------------------------------------------- Host check


def test_unknown_host_is_refused(run):
    assert run.json("GET", "/notes/api/health", host="evil.example") == (403, {"error": "host not allowed"})


@pytest.mark.parametrize("host", [
    "studio", "studio.tailnet.example", "STUDIO.tailnet.example:443", "studio.tailnet.example.",
    "127.0.0.1", "127.0.0.1:8890", "localhost", "localhost:8890",
])
def test_tailnet_and_loopback_hosts_pass(run, host):
    assert run.json("GET", "/notes/api/health", host=host)[0] == 200


def test_missing_or_repeated_host_is_refused(run):
    assert run.json("GET", "/notes/api/health", host=None)[0] == 403
    status, _ = run.json("GET", "/notes/api/health", host="127.0.0.1", headers={"Host": "evil.example"})
    assert status == 403


def test_host_name_parsing():
    assert api.host_name("[::1]:8080") == "::1"
    assert api.host_name("[::1]") == "::1"
    assert api.host_name("Studio.Tailnet.example.:443") == "studio.tailnet.example"
    assert api.host_name("::1") == "::1"


def test_only_loopback_names_without_configuration():
    assert LoopbackApp(name="X", port=1).accepted_hosts == {"127.0.0.1", "localhost"}


def test_tailnet_hosts_builds_short_and_full_names():
    assert api.tailnet_hosts("Tailnet.Example.", "studio", "notes") == {
        "studio", "studio.tailnet.example", "notes", "notes.tailnet.example"}


def test_package_names_no_tailnet():
    source = Path(api.__file__).parent
    for path in source.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\.ts\.net", text, re.IGNORECASE), path.name
        assert "0.0.0.0" not in text, path.name


# ---------------------------------------------------------------- binding


def test_binds_loopback_only(run):
    assert run.server.server_address[0] == "127.0.0.1"


def test_a_second_server_cannot_share_the_port(run, app, routes):
    with pytest.raises(OSError):
        second = api.make_server(app, routes, port=run.server.server_address[1])
        second.server_close()


def test_bad_routes_are_refused(app):
    with pytest.raises(ValueError):
        api.make_handler(app, {("PATCH", "/x"): echo})
    with pytest.raises(ValueError):
        api.make_handler(app, {("GET", "x"): echo})


# ---------------------------------------------------------------- mount and static client


def test_mount_redirects_and_routes_work_with_or_without_it(run):
    status, headers, _ = run.request("GET", "/notes")
    assert (status, headers["Location"]) == (301, "/notes/")
    assert run.json("GET", "/notes/api/health") == (200, {"ok": True, "app": "notes", "exists": True})
    assert run.json("GET", "/api/health")[0] == 200


@pytest.mark.parametrize("given, expected", [("", ""), ("/", ""), ("notes", "/notes"), ("/notes/", "/notes")])
def test_mount_is_normalized(given, expected):
    assert LoopbackApp(name="X", port=1, mount=given).mount == expected


def test_root_mount_serves_routes_at_the_root(tmp_log):
    app = LoopbackApp(name="Feed", port=0, web_client=False, log_path=tmp_log)
    running = Running(app, {("GET", "/v1/health"): api.health_route(version="1.0.0")})
    try:
        assert running.json("GET", "/v1/health") == (200, {"ok": True, "version": "1.0.0"})
        assert running.json("GET", "/")[0] == 404
        assert running.json("GET", "/manifest.webmanifest")[0] == 404
    finally:
        running.close()
    assert "GET /v1/health 200" in tmp_log.read_text(encoding="utf-8")


def test_index_has_csp_and_no_store(run):
    status, headers, body = run.request("GET", "/notes/")
    assert status == 200
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert b"<html" in body


def test_missing_index_gets_a_placeholder(tmp_path):
    app = LoopbackApp(name="Notes Remote", port=0, index_html=tmp_path / "absent.html")
    running = Running(app, {})
    try:
        status, _, body = running.request("GET", "/")
        assert status == 200 and b"no web client yet" in body
    finally:
        running.close()


def test_manifest_and_icons(run):
    status, _, raw = run.request("GET", "/notes/manifest.webmanifest")
    manifest = json.loads(raw)
    assert (status, manifest["name"], manifest["short_name"], manifest["display"]) == (
        200, "Notes Remote", "NTS", "standalone")
    for size in (180, 512):
        status, headers, raw = run.request("GET", f"/notes/icon-{size}.png")
        assert (status, headers["Content-Type"], raw[:8]) == (200, "image/png", b"\x89PNG\r\n\x1a\n")
        assert int.from_bytes(raw[16:20], "big") == size


def test_unknown_path_and_wrong_method(run):
    assert run.json("GET", "/notes/api/nope") == (404, {"error": "not found"})
    assert run.json("DELETE", "/notes/api/echo") == (405, {"error": "method not allowed"})


# ---------------------------------------------------------------- bodies


def test_json_object_body_reaches_the_route(run):
    assert run.json("PUT", "/notes/api/echo", {"a": 1}) == (200, {"got": {"a": 1}})


@pytest.mark.parametrize("headers, body, expected", [
    ({"Content-Length": "abc"}, b"{}", 400),
    ({"Content-Length": "-1"}, b"{}", 400),
    ({"Content-Length": "0"}, b"", 400),
    ({"Content-Length": "+2"}, b"{}", 400),
    ({"Content-Length": ["2", "2"]}, b"{}", 400),
    ({"Transfer-Encoding": "chunked"}, b"2\r\n{}\r\n0\r\n\r\n", 400),
    ({"Content-Length": "1025"}, b"", 413),
])
def test_body_framing_is_strict(run, headers, body, expected):
    assert run.request("PUT", "/notes/api/echo", body, headers)[0] == expected


@pytest.mark.parametrize("body", [b"[1]", b"{not json", b'"text"', b"[" * 1000], ids=["list", "bad", "string", "deep"])
def test_body_must_be_a_json_object(run, body):
    assert run.request("POST", "/notes/api/echo", body)[0] == 400


def test_short_body_is_refused(run):
    with socket.create_connection(run.server.server_address, timeout=3) as sock:
        sock.sendall(b"PUT /notes/api/echo HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 10\r\n\r\n{}")
        sock.shutdown(socket.SHUT_WR)
        reply = sock.recv(4096)
    assert reply.startswith(b"HTTP/1.0 400")


# ---------------------------------------------------------------- errors


def test_api_error_carries_extra_fields(run):
    assert run.json("GET", "/notes/api/conflict") == (409, {"error": "changed", "revision": "abc"})


def test_os_error_is_a_500_with_the_reason(run):
    assert run.json("GET", "/notes/api/os") == (500, {"error": "server could not do that: Access is denied"})


def test_a_bug_in_a_route_is_a_plain_500_and_its_message_is_not_logged(run, tmp_log):
    assert run.json("GET", "/notes/api/bug") == (500, {"error": "internal error"})
    text = tmp_log.read_text(encoding="utf-8")
    assert "failed: KeyError" in text and SECRET not in text


# ---------------------------------------------------------------- logging


def test_log_never_holds_bodies_queries_headers_or_unknown_paths(run, tmp_log):
    run.json("PUT", "/notes/api/echo", {"content": SECRET})
    run.request("GET", f"/notes/api/query?token={SECRET}")
    run.request("GET", f"/notes/{SECRET}")
    run.request("GET", "/notes/api/secret", headers={"Authorization": f"Bearer {SECRET}"})
    run.request("BREW", f"/notes/{SECRET}")
    text = tmp_log.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "PUT /notes/api/echo 200" in text
    assert "GET /notes/api/query 200" in text
    assert "<unknown>" in text
    assert "OTHER" in text


def test_log_callable_gets_the_same_lines(tmp_path):
    lines = []
    app = LoopbackApp(name="X", port=0, web_client=False, log=lines.append)
    running = Running(app, {("GET", "/ping"): api.health_route()})
    try:
        running.request("GET", "/ping?x=1")
    finally:
        running.close()
    assert any(line.endswith("GET /ping 200") for line in lines)


def test_query_parameters(run):
    assert run.json("GET", "/notes/api/query?id=a&id=b&empty=") == (200, {"id": "a", "empty": ""})


# ---------------------------------------------------------------- bearer token and gzip


def test_bearer_token(run):
    assert run.json("GET", "/notes/api/secret") == (401, {"error": "unauthorized"})
    assert run.json("GET", "/notes/api/secret", headers={"Authorization": "Bearer wrong"})[0] == 401
    assert run.json("GET", "/notes/api/secret", headers={"Authorization": f"Bearer {SECRET}"}) == (200, {"ok": True})
    with pytest.raises(ValueError):
        api.require_bearer("", echo)


def test_gzip_only_when_enabled_accepted_and_large_enough(app, routes):
    app.gzip_min_bytes = 1000
    running = Running(app, routes)
    try:
        status, headers, raw = running.request("GET", "/notes/api/big", headers={"Accept-Encoding": "br, gzip"})
        assert (status, headers["Content-Encoding"], headers["Vary"]) == (200, "gzip", "Accept-Encoding")
        assert len(json.loads(gzip.decompress(raw))["items"]) == 100
        assert "Content-Encoding" not in running.request(
            "GET", "/notes/api/big", headers={"Accept-Encoding": "gzip;q=0"})[1]
        assert "Content-Encoding" not in running.request(
            "GET", "/notes/api/health", headers={"Accept-Encoding": "gzip"})[1]
    finally:
        running.close()


def test_gzip_off_by_default(run):
    assert "Content-Encoding" not in run.request("GET", "/notes/api/big", headers={"Accept-Encoding": "gzip"})[1]


# ---------------------------------------------------------------- pixel mark


def test_every_glyph_is_five_by_seven():
    for ch, rows in api.GLYPHS.items():
        parts = rows.split()
        assert len(parts) == 7, ch
        assert all(len(r) == 5 and set(r) <= {"0", "1"} for r in parts), ch


def test_pixel_mark_png_size_and_plain_tile():
    png = api.pixel_mark_png("ram", 64, (0, 0, 0), (255, 255, 255))
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(png[16:20], "big") == 64
    assert api.pixel_mark_png("???", 32, (0, 0, 0), (9, 9, 9)) == api.pixel_mark_png("", 32, (0, 0, 0), (1, 1, 1))
