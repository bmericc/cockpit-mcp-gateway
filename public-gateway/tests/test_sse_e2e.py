"""Gerçek uvicorn sunucusu + resmi MCP SSE client ile uçtan uca test."""
import asyncio
import logging
import socket
import threading
import time

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

import mcp_gateway
import access
import main
import settings

pytestmark = pytest.mark.anyio


class _Collector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class LiveServer:
    def __init__(self, url, server, thread, errors, access):
        self.url = url
        self._server = server
        self._thread = thread
        self.errors = errors
        self.access = access

    def stop(self):
        self._server.should_exit = True
        self._thread.join(timeout=10)

    def error_messages(self):
        return [r.getMessage() for r in self.errors.records if r.levelno >= logging.ERROR]

    def status_codes(self):
        # uvicorn.access args: (client_addr, method, path, http_version, status_code)
        return [r.args[4] for r in self.access.records]


@pytest.fixture
def access_token(monkeypatch, tmp_path):
    """Cockpit girişi + erişim token'ı ile alınmış gibi bir OAuth erişim token'ı üretir."""
    monkeypatch.setattr(access.oauth_provider, "store_path", str(tmp_path / "oauth.json"))
    monkeypatch.setattr(access.authenticator, "allowed_users", {"admin", "plain"})
    return access.oauth_provider._issue("test-client", ["mcp"], "admin", None).access_token


@pytest.fixture
def live_server(monkeypatch, servers_file):
    monkeypatch.setattr(settings, "MCP_API_KEY", "xxx")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    # Config oluşturulurken uvicorn logging'i yeniden yapılandırır; handler'lar ondan sonra eklenmeli
    config = uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="info")
    errors, access = _Collector(), _Collector()
    logging.getLogger("uvicorn.error").addHandler(errors)
    logging.getLogger("uvicorn.access").addHandler(access)

    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn başlamadı")
        time.sleep(0.05)

    live = LiveServer(f"http://127.0.0.1:{port}", server, thread, errors, access)
    yield live

    live.stop()
    logging.getLogger("uvicorn.error").removeHandler(errors)
    logging.getLogger("uvicorn.access").removeHandler(access)


async def test_sse_roundtrip(live_server, access_token):
    async with sse_client(f"{live_server.url}/sse", headers={"Authorization": f"Bearer {access_token}"}) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {"list_servers", "run_remote_command", "server_info"} <= {t.name for t in tools.tools}
            result = await session.call_tool("list_servers", {})
            assert result.content[0].text == "{}"

    # Sunucu durdurulunca kapanan SSE bağlantısı hata (örn. NoneType response) üretmemeli
    live_server.stop()
    assert live_server.error_messages() == []
    # /messages POST'ları yönlendirilmeden doğrudan 202 almalı
    assert 307 not in live_server.status_codes()
    assert 202 in live_server.status_codes()


async def test_tool_calls_are_logged_with_connected_user(live_server, access_token, audit_file):
    async with sse_client(f"{live_server.url}/sse", headers={"Authorization": f"Bearer {access_token}"}) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("list_servers", {})

    entries = audit_file(source="mcp")
    assert [(e["action"], e["status"], e["user"], e["ip"]) for e in entries] == [
        ("list_servers", "ok", "admin", "127.0.0.1"), ("connect", "ok", "admin", "127.0.0.1")]


async def test_rejected_credentials_are_logged(live_server, audit_file):
    import httpx

    async with httpx.AsyncClient(trust_env=False) as client:
        await client.get(f"{live_server.url}/sse")  # başlıksız keşif isteği kaydedilmez
        await client.get(f"{live_server.url}/sse", headers={"Authorization": "Bearer yanlis"})
    assert [(e["action"], e["status"]) for e in audit_file()] == [("connect", "error")]


async def test_access_token_alone_is_not_enough(live_server):
    # Erişim token'ı (MCP_API_KEY) tek başına giriş sağlamaz; Cockpit girişi de gerekir
    import httpx

    async with httpx.AsyncClient(trust_env=False) as client:
        for kwargs in ({"params": {"token": "xxx"}}, {"headers": {"Authorization": "Bearer xxx"}}):
            resp = await client.get(f"{live_server.url}/sse", **kwargs)
            assert resp.status_code == 401


async def open_stream(url, token):
    import httpx

    client = httpx.AsyncClient(trust_env=False, timeout=5)
    stream = client.stream("GET", f"{url}/sse", headers={"Authorization": f"Bearer {token}"})
    response = await stream.__aenter__()
    assert response.status_code == 200
    return client, stream, response


async def read_until_closed(response):
    async for _ in response.aiter_bytes():
        pass


async def test_open_stream_closes_when_user_is_removed(live_server, access_token, audit_file, monkeypatch):
    monkeypatch.setattr(mcp_gateway, "SSE_RECHECK_SECONDS", 0.05)
    client, stream, response = await open_stream(live_server.url, access_token)
    try:
        monkeypatch.setattr(access.authenticator, "allowed_users", {"baska"})
        await asyncio.wait_for(read_until_closed(response), 5)
    finally:
        await stream.__aexit__(None, None, None)
        await client.aclose()
    assert audit_file(action="disconnect")[0]["user"] == "admin"
    live_server.stop()
    assert live_server.error_messages() == []


async def test_open_stream_closes_when_token_expires(live_server, access_token, monkeypatch):
    monkeypatch.setattr(mcp_gateway, "SSE_RECHECK_SECONDS", 0.05)
    client, stream, response = await open_stream(live_server.url, access_token)
    try:
        for stored in access.oauth_provider.access_tokens.values():
            stored.expires_at = 1
        await asyncio.wait_for(read_until_closed(response), 5)
    finally:
        await stream.__aexit__(None, None, None)
        await client.aclose()


# --- Streamable HTTP ---

async def test_streamable_http_roundtrip_is_logged_with_user(live_server, access_token, audit_file):
    headers = {"Authorization": f"Bearer {access_token}"}
    async with streamablehttp_client(f"{live_server.url}/mcp", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "run_remote_command" in {t.name for t in tools.tools}
            result = await session.call_tool("list_servers", {})
            assert result.content[0].text == "{}"
    entry = audit_file(action="list_servers")[0]
    assert (entry["status"], entry["user"], entry["ip"]) == ("ok", "admin", "127.0.0.1")
    live_server.stop()
    assert live_server.error_messages() == []


async def test_streamable_http_requires_login_on_every_request(live_server, access_token, monkeypatch):
    import httpx

    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient(trust_env=False) as client:
        resp = await client.post(f"{live_server.url}/mcp", json=body)
        assert resp.status_code == 401
        assert 'oauth-protected-resource/mcp"' in resp.headers["www-authenticate"]
        assert (await client.get(f"{live_server.url}/mcp", headers=headers)).status_code == 405
        monkeypatch.setattr(access.authenticator, "allowed_users", {"baska"})
        assert (await client.post(f"{live_server.url}/mcp", json=body, headers=headers)).status_code == 401


async def test_metadata_names_the_transport(live_server):
    import httpx

    async with httpx.AsyncClient(trust_env=False) as client:
        data = (await client.get(f"{live_server.url}/.well-known/oauth-protected-resource/mcp")).json()
    assert data["resource"] == f"{settings.PUBLIC_URL}/mcp"
