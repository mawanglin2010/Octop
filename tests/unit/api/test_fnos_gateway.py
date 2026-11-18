"""fork: fnOS unified gateway prefix middleware."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.testclient import TestClient

from octop.api.middleware.fnos_gateway import (
    GATEWAY_PREFIX_ENV,
    GATEWAY_SOCKET_ENV,
    GatewayPrefixMiddleware,
    gateway_listener_from_env,
    normalize_prefix,
    rewrite_html,
)

PREFIX = "/app/octop-native"
INDEX = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    '<link rel="icon" href="/favico.svg">'
    '<script type="module" src="/assets/index.abc.js"></script>'
    '<a href="//cdn.example.com/x">cdn</a><a href="https://example.com/">ext</a>'
    '<img src="logo.png"></head><body></body></html>'
)


def _app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    index = tmp_path / "index.html"
    index.write_text(INDEX, encoding="utf-8")

    @app.get("/api/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "pong"}

    @app.get("/page")
    async def page() -> HTMLResponse:
        return HTMLResponse(INDEX)

    @app.get("/file")
    async def file() -> FileResponse:
        return FileResponse(index, media_type="text/html")

    @app.websocket("/api/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        await socket.send_text(socket.url.path)
        await socket.close()

    return app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(GatewayPrefixMiddleware(_app(tmp_path), PREFIX))  # type: ignore[arg-type]


def test_normalize_prefix() -> None:
    assert normalize_prefix("/app/octop-native/") == PREFIX
    assert normalize_prefix("app/octop-native") == PREFIX
    assert normalize_prefix("") is None
    assert normalize_prefix("/") is None
    assert normalize_prefix("/app/<x>") is None


def test_listener_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(GATEWAY_SOCKET_ENV, raising=False)
    monkeypatch.setenv(GATEWAY_PREFIX_ENV, PREFIX)
    assert gateway_listener_from_env() is None
    sock = str(tmp_path / "gateway.sock")
    monkeypatch.setenv(GATEWAY_SOCKET_ENV, sock)
    expected = (sock, PREFIX) if os.name == "posix" else None
    assert gateway_listener_from_env() == expected
    monkeypatch.setenv(GATEWAY_PREFIX_ENV, "/")
    assert gateway_listener_from_env() is None


def test_rewrite_html_prefixes_root_relative_only() -> None:
    out = rewrite_html(INDEX.encode(), PREFIX).decode()
    assert 'href="/app/octop-native/favico.svg"' in out
    assert 'src="/app/octop-native/assets/index.abc.js"' in out
    assert 'href="//cdn.example.com/x"' in out
    assert 'href="https://example.com/"' in out
    assert 'src="logo.png"' in out
    # Bootstrap runs first thing in <head>.
    head = out.index("<head>") + len("<head>")
    assert out[head:].startswith("<script>(function(){")
    assert 'window.__OCTOP_BASE__=B+"/"' in out
    assert '"/app/octop-native"' in out


def test_rewrite_html_without_head() -> None:
    out = rewrite_html(b'<p><img src="/x.png"></p>', PREFIX)
    assert out.startswith(b"<script>")
    assert b'src="/app/octop-native/x.png"' in out


def test_json_passes_through_with_prefix_stripped(client: TestClient) -> None:
    resp = client.get(f"{PREFIX}/api/ping")
    assert resp.status_code == 200
    assert resp.json() == {"ok": "pong"}


@pytest.mark.parametrize("path", ["/page", "/file"])
def test_html_rewritten(client: TestClient, path: str) -> None:
    resp = client.get(f"{PREFIX}{path}")
    assert resp.status_code == 200
    assert "window.__OCTOP_BASE__" in resp.text
    assert 'src="/app/octop-native/assets/index.abc.js"' in resp.text
    assert resp.headers["content-length"] == str(len(resp.content))
    assert "etag" not in resp.headers


def test_bare_prefix_redirects(client: TestClient) -> None:
    resp = client.get(f"{PREFIX}?a=1", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == f"{PREFIX}/?a=1"


def test_unprefixed_request_untouched(client: TestClient) -> None:
    resp = client.get("/page")
    assert "window.__OCTOP_BASE__" not in resp.text
    assert client.get("/api/ping").json() == {"ok": "pong"}


def test_websocket_prefix_stripped(client: TestClient) -> None:
    with client.websocket_connect(f"{PREFIX}/api/ws") as ws:
        assert ws.receive_text() == "/api/ws"
