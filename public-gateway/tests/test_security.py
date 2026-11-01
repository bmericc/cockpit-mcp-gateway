"""Güvenlik sertleştirmeleri: gizli değerler, oturum, CSRF, oran sınırı, token sızıntıları, SSH anahtarı."""
import asyncio
import json
import logging
import os
import stat

import asyncssh
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import auth
import access
import connections
import main
import mcp_gateway
import settings
import storage
import web_panel
import security
from creds import PASSWORD, WRONG_PASSWORD
from panel import csrf_token, login_as
from test_auth import basic, cockpit, full_oauth_flow, oauth_flow, provider  # noqa: F401


# --- Gizli değerler ---

@pytest.mark.parametrize("value", ["", "   ", "gizli-anahtar", "a_very_secure_and_random_string", "xxx-long-random-token"])
def test_missing_or_sample_secret_gets_random_persistent_value(tmp_path, monkeypatch, value):
    path = str(tmp_path / "data" / ".secret")
    monkeypatch.setenv("SOME_SECRET", value)
    first = security.load_or_create_secret("SOME_SECRET", path)
    assert len(first) >= 48 and first != value
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert security.load_or_create_secret("SOME_SECRET", path) == first


def test_configured_secret_wins_and_is_not_written(tmp_path, monkeypatch):
    path = tmp_path / ".secret"
    monkeypatch.setenv("SOME_SECRET", "operator-value")
    assert security.load_or_create_secret("SOME_SECRET", str(path)) == "operator-value"
    assert not path.exists()


def test_generated_secret_value_is_not_logged(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SOME_SECRET", raising=False)
    value = security.load_or_create_secret("SOME_SECRET", str(tmp_path / ".secret"))
    assert value not in capsys.readouterr().err


def test_gateway_refuses_to_start_without_allowed_users(monkeypatch):
    monkeypatch.setattr(access.authenticator, "allowed_users", set())
    with pytest.raises(RuntimeError, match="ALLOWED_USERS"):
        with TestClient(main.app):
            pass


def test_values_encrypted_with_legacy_key_are_migrated(servers_file, tmp_path):
    legacy = storage._fernet("gizli-anahtar").encrypt(PASSWORD.encode()).decode()
    current = storage.encrypt_secret("zaten-guncel")
    unknown = storage._fernet("baska-anahtar").encrypt(b"x").decode()
    servers_file({"a": {"cockpit_password": legacy, "proxy": current}, "b": {"cockpit_password": unknown}})
    storage.save_shared_keys({"k": {"private": legacy}})

    storage.migrate_legacy_encryption()

    servers = storage.load_servers()
    assert storage.decrypt_secret(servers["a"]["cockpit_password"]) == PASSWORD
    assert storage.decrypt_secret(servers["a"]["proxy"]) == "zaten-guncel"
    assert servers["b"]["cockpit_password"] == unknown
    assert storage.decrypt_secret(storage.load_shared_keys()["k"]["private"]) == PASSWORD


def test_corrupt_shared_keys_file_is_an_error(shared_keys_file):
    shared_keys_file.parent.mkdir(parents=True, exist_ok=True)
    shared_keys_file.write_text("{bozuk")
    with pytest.raises(storage.StorageError):
        storage.load_shared_keys()


# --- Panel oturumu ve CSRF ---

@pytest.fixture
def admin(servers_file, monkeypatch):
    monkeypatch.setattr(access.authenticator, "allowed_users", {"admin"})
    return login_as(TestClient(main.app), "admin")


def raw_post(client, url, **data):
    """login_as'in eklediği CSRF token'ı olmadan gönderir."""
    return TestClient.post(client, url, data=data, follow_redirects=False)


@pytest.mark.parametrize("url", ["/servers", "/public-ip/refresh", "/ssh-keys", "/ssh-keys/generate", "/logout",
                                 "/servers/x/test", "/servers/x/delete", "/servers/x/forget-host-key",
                                 "/ssh-keys/x/delete"])
def test_panel_posts_need_csrf_token(admin, url):
    assert raw_post(admin, url).status_code == 403
    assert raw_post(admin, url, csrf="yanlis").status_code == 403


def test_csrf_token_in_every_panel_form(admin, servers_file):
    servers_file({"srv": {"name": "srv", "host": "h", "ssh_host_key": "ssh-ed25519 AAA"}})
    page = admin.get("/").text
    assert page.count('name="csrf"') == page.count("<form") - page.count('method="get"')


def test_logout_get_is_gone(admin):
    assert admin.get("/logout", follow_redirects=False).status_code == 405


def test_logout_revokes_the_cookie_on_the_server(admin):
    cookie = admin.cookies["session"]
    assert "Welcome" in admin.get("/").text
    assert admin.post("/logout", follow_redirects=False).status_code == 303

    replay = TestClient(main.app)
    replay.cookies.set("session", cookie)
    assert "Welcome" not in replay.get("/").text


def test_cookie_signed_with_old_key_is_rejected(admin):
    # Eski MCP_API_KEY'le türetilmiş anahtarın ürettiği çerez
    import base64
    import itsdangerous

    data = base64.b64encode(json.dumps({"user": {"username": "admin"}, "sid": "sid-admin"}).encode())
    client = TestClient(main.app)
    client.cookies.set("session", itsdangerous.TimestampSigner("eski-anahtar").sign(data).decode())
    assert "Welcome" not in client.get("/").text


def test_session_key_changes_with_access_token():
    assert settings.SESSION_KEY not in (settings.SECRET_KEY, settings.MCP_API_KEY)
    import hashlib
    other = hashlib.sha256(f"session\0{settings.SECRET_KEY}\0yeni".encode()).hexdigest()
    assert other != settings.SESSION_KEY


def test_expired_panel_session_is_rejected(admin):
    web_panel.panel_sessions["sid-admin"] = 1
    assert "Welcome" not in admin.get("/").text


def test_login_cookie_attributes_and_fresh_session(cockpit, servers_file):  # noqa: F811
    client = TestClient(main.app)
    resp = client.post("/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and f"max-age={settings.SESSION_TTL}" in cookie
    assert len(web_panel.panel_sessions) == 1
    assert client.post("/logout", data={"csrf": csrf_token(client)}, follow_redirects=False).status_code == 303
    assert web_panel.panel_sessions == {}


def test_session_cookie_is_secure_only_on_https():
    options = next(m.kwargs for m in main.app.user_middleware if m.cls is main.SessionMiddleware)
    assert options["https_only"] is settings.PUBLIC_URL.startswith("https://")
    assert options["max_age"] == settings.SESSION_TTL


# --- Giriş koruması ---

def test_login_is_rate_limited_per_ip(cockpit, servers_file):  # noqa: F811
    client = TestClient(main.app)
    for _ in range(access.LOGIN_FAILURES_ALLOWED):
        assert client.post("/login", data={"username": "admin", "password": WRONG_PASSWORD}).status_code == 401
    logins = len(cockpit.fake.logins)
    resp = client.post("/login", data={"username": "admin", "password": PASSWORD})
    assert resp.status_code == 429
    assert "Too many failed attempts" in resp.text
    assert len(cockpit.fake.logins) == logins


def test_oauth_login_shares_the_limit(cockpit, provider):  # noqa: F811
    client = TestClient(main.app)
    for _ in range(access.LOGIN_FAILURES_ALLOWED):
        access.login_limiter.hit("testclient")
    _, _, _, resp = oauth_flow(client)
    assert resp.status_code == 429


def test_successful_login_resets_the_counter(cockpit, servers_file):  # noqa: F811
    client = TestClient(main.app)
    access.login_limiter.hit("testclient")
    client.post("/login", data={"username": "admin", "password": PASSWORD})
    assert not access.login_limiter._hits


@pytest.mark.anyio
async def test_basic_auth_is_rate_limited(cockpit):  # noqa: F811
    def request(password):
        return Request({"type": "http", "query_string": b"", "client": ("203.0.113.9", 1),
                        "headers": [(b"authorization", basic("admin", password).encode())]})

    for _ in range(access.LOGIN_FAILURES_ALLOWED):
        assert await access.authenticate_mcp(request(WRONG_PASSWORD)) is None
    logins = len(cockpit.fake.logins)
    assert await access.authenticate_mcp(request(PASSWORD)) is None
    assert len(cockpit.fake.logins) == logins


def test_non_ascii_and_file_form_values_do_not_crash(cockpit, servers_file, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "MCP_API_KEY", "gercek-token")
    client = TestClient(main.app, raise_server_exceptions=True)
    assert client.post("/login", data={"username": "a", "password": "b", "token": "şifre"}).status_code == 401
    resp = client.post("/login", files={"token": ("t", b"x"), "username": ("u", b"x"), "password": ("p", b"x")})
    assert resp.status_code == 401
    assert client.post("/oauth/login", files={"request": ("r", b"x")}).status_code == 400


# --- /register ---

def register(client, name="c"):
    return client.post("/register", json={"redirect_uris": ["http://localhost/cb"], "client_name": name})


def test_register_is_rate_limited(provider):  # noqa: F811
    client = TestClient(main.app)
    limit = mcp_gateway.register_limiter.limit
    assert all(register(client).status_code == 201 for _ in range(limit))
    assert register(client).status_code == 429
    assert len(provider.clients) == limit


def test_register_rejects_large_body(provider):  # noqa: F811
    client = TestClient(main.app)
    assert register(client, "x" * (mcp_gateway.MAX_REGISTER_BODY + 1)).status_code == 413
    assert provider.clients == {}


def make_client(client_id):
    return auth.OAuthClientInformationFull(client_id=client_id, redirect_uris=["http://localhost/cb"])


@pytest.mark.anyio
async def test_client_count_is_capped_and_idle_clients_go_first(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "MAX_CLIENTS", 2)
    store = auth.CockpitOAuthProvider(str(tmp_path / "oauth.json"), "x")
    await store.register_client(make_client("etkin"))
    await store.register_client(make_client("bosta"))
    store._issue("etkin", ["mcp"], "admin", None)

    await store.register_client(make_client("yeni"))
    assert set(store.clients) == {"etkin", "yeni"}

    store._issue("yeni", ["mcp"], "admin", None)
    with pytest.raises(auth.RegistrationError):
        await store.register_client(make_client("fazla"))


# --- Token sızıntıları ---

def test_url_token_is_not_stored_in_oauth_file(cockpit, provider, monkeypatch):  # noqa: F811
    secret = "gercek-erisim-tokeni"
    monkeypatch.setattr(settings, "MCP_API_KEY", secret)
    tokens, _ = full_oauth_flow(resource=f"{mcp_gateway.MCP_RESOURCE_URL}?token={secret}")
    assert tokens["access_token"]
    with open(provider.store_path) as f:
        stored = f.read()
    assert secret not in stored
    assert mcp_gateway.MCP_RESOURCE_URL in stored


def test_old_oauth_file_is_cleaned_on_load(tmp_path):
    path = str(tmp_path / "oauth.json")
    first = auth.CockpitOAuthProvider(path, "x")
    first._issue("c", ["mcp"], "admin", "https://gw/sse?token=gizli")
    assert "gizli" in open(path).read()

    auth.CockpitOAuthProvider(path, "x")
    assert "gizli" not in open(path).read()


def test_access_log_hides_url_token():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s "%s %s" %d', (
        "1.2.3.4", "GET", "/sse?a=1&token=gizli-deger&b=2", 401), None)
    security.RedactTokenFilter().filter(record)
    assert "gizli-deger" not in record.getMessage()
    assert "a=1" in record.getMessage() and "b=2" in record.getMessage()


# --- OAuth oturum süresi ---

@pytest.mark.anyio
async def test_refresh_does_not_extend_the_session(tmp_path):
    store = auth.CockpitOAuthProvider(str(tmp_path / "oauth.json"), "x")
    client = make_client("c")
    first = store._issue("c", ["mcp"], "admin", None)
    refresh = await store.load_refresh_token(client, first.refresh_token)
    end = refresh.expires_at

    second = await store.exchange_refresh_token(client, refresh, [])
    renewed = await store.load_refresh_token(client, second.refresh_token)
    assert renewed.expires_at == end
    assert await store.load_refresh_token(client, first.refresh_token) is None


@pytest.mark.anyio
async def test_expired_session_cannot_be_refreshed(tmp_path):
    store = auth.CockpitOAuthProvider(str(tmp_path / "oauth.json"), "x")
    client = make_client("c")
    tokens = store._issue("c", ["mcp"], "admin", None, session_expires_at=1)
    assert await store.load_refresh_token(client, tokens.refresh_token) is None


@pytest.mark.anyio
async def test_access_token_never_outlives_the_session(tmp_path):
    store = auth.CockpitOAuthProvider(str(tmp_path / "oauth.json"), "x")
    end = int(connections.time.time()) + 60
    tokens = store._issue("c", ["mcp"], "admin", None, session_expires_at=end)
    assert tokens.expires_in <= 60
    assert (await store.load_access_token(tokens.access_token)).expires_at == end


@pytest.mark.anyio
async def test_removed_user_cannot_refresh(tmp_path, monkeypatch):
    allowed = {"admin"}
    store = auth.CockpitOAuthProvider(str(tmp_path / "oauth.json"), "x", is_allowed=allowed.__contains__)
    client = make_client("c")
    tokens = store._issue("c", ["mcp"], "admin", None)
    assert await store.load_refresh_token(client, tokens.refresh_token) is not None
    allowed.clear()
    assert await store.load_refresh_token(client, tokens.refresh_token) is None


def test_empty_allow_list_denies_everyone():
    assert not auth.CockpitAuthenticator("u", False, set()).is_allowed("admin")


# --- Audit IP'si ---

async def forwarded_client(trusted, peer, header):
    seen = {}

    async def inner(scope, receive, send):
        seen["client"] = scope["client"][0]

    app = ProxyHeadersMiddleware(inner, trusted_hosts=trusted)
    await app({"type": "http", "client": (peer, 1), "headers": [(b"x-forwarded-for", header)], "scheme": "http"}, None, None)
    return seen["client"]


@pytest.mark.anyio
async def test_spoofed_forwarded_for_is_ignored():
    # Doğrudan gelen istemci başlık yazamaz
    assert await forwarded_client("127.0.0.1", "203.0.113.5", b"6.6.6.6") == "203.0.113.5"
    # Güvenilen proxy arkasında istemcinin eklediği sol taraf alınmaz
    assert await forwarded_client("172.16.0.0/12", "172.18.0.1", b"6.6.6.6, 203.0.113.5") == "203.0.113.5"


def test_default_trusts_only_loopback():
    assert settings.TRUSTED_PROXIES == "127.0.0.1"


# --- Başlıklar ---

def test_security_headers_and_subresource_integrity():
    resp = TestClient(main.app).get("/login")
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    # OAuth girişi istemci adresine yönlendirdiğinden form-action kısıtı olamaz
    assert "form-action" not in resp.headers["content-security-policy"]
    assert resp.text.count('integrity="sha384-') == 2


# --- Salt okunur mod ---

@pytest.mark.anyio
async def test_read_only_mode_hides_and_blocks_write_tools(monkeypatch, servers_file, sample_server):
    servers_file(sample_server)
    monkeypatch.setattr(settings, "READ_ONLY", True)
    names = {tool.name for tool in await mcp_gateway.handle_list_tools()}
    assert "server_info" in names and "list_servers" in names
    assert not names & {"run_remote_command", "reboot_server", "package_install", "firewall_rule"}
    for name, args in (("run_remote_command", {"command": "id"}), ("reboot_server", {})):
        content, status = await mcp_gateway._call_tool(name, {"server_name": "prod-db", "confirm": True, **args})
        assert status == "error" and "read-only" in content[0].text


@pytest.mark.anyio
async def test_write_tools_are_listed_by_default():
    assert "run_remote_command" in {tool.name for tool in await mcp_gateway.handle_list_tools()}


# --- SSH sunucu anahtarı ---

class NoAuth(asyncssh.SSHServer):
    def begin_auth(self, username):
        return False


@pytest.fixture
async def ssh_server(ssh_dirs):
    first, second = (asyncssh.generate_private_key("ssh-ed25519") for _ in range(2))
    state = {"servers": []}

    async def start(host_key, port=0):
        server = await asyncssh.create_server(NoAuth, "127.0.0.1", port, server_host_keys=[host_key])
        state["servers"].append(server)
        return server.sockets[0].getsockname()[1]

    state.update(first=first, second=second, start=start, key_path=ssh_dirs("root"))
    private = asyncssh.generate_private_key("ssh-ed25519")
    private.write_private_key(state["key_path"])
    yield state
    for server in state["servers"]:
        server.close()
        await server.wait_closed()


def ssh_cfg(port, key_path):
    return {"name": "srv", "host": "127.0.0.1", "port": port, "user": "root", "ssh_key_path": key_path}


async def stop(state):
    server = state["servers"].pop()
    server.close()
    await server.wait_closed()
    await asyncio.sleep(0)


@pytest.mark.anyio
async def test_host_key_is_pinned_and_a_changed_key_is_refused(ssh_server, servers_file):
    port = await ssh_server["start"](ssh_server["first"])
    cfg = ssh_cfg(port, ssh_server["key_path"])
    servers_file({"srv": dict(cfg)})

    async with connections.ssh_session(cfg):
        pass
    stored = storage.load_servers()["srv"]["ssh_host_key"]
    assert stored == ssh_server["first"].export_public_key().decode().strip()

    async with connections.ssh_session(storage.load_servers()["srv"]):
        pass

    await stop(ssh_server)
    await ssh_server["start"](ssh_server["second"], port)
    with pytest.raises(connections.SSHError, match="host key"):
        async with connections.ssh_session(storage.load_servers()["srv"]):
            pass
    assert storage.load_servers()["srv"]["ssh_host_key"] == stored


def test_forget_host_key_from_panel(admin, servers_file):
    servers_file({"srv": {"name": "srv", "host": "h", "ssh_host_key": "ssh-ed25519 AAA"}})
    assert "Forget host key" in admin.get("/").text
    resp = admin.post("/servers/srv/forget-host-key", follow_redirects=False)
    assert resp.status_code == 303
    assert "ssh_host_key" not in storage.load_servers()["srv"]
    assert "Forget host key" not in admin.get("/").text


def test_unsaved_attempt_does_not_pin_on_the_stored_server(servers_file):
    servers_file({"srv": {"name": "srv", "host": "eski", "port": 22}})

    class Key:
        def export_public_key(self):
            return b"ssh-ed25519 YENI\n"

    class Conn:
        def get_server_host_key(self):
            return Key()

    cfg = {"name": "srv", "host": "yeni", "port": 22}
    connections.pin_host_key(cfg, Conn())
    assert cfg["ssh_host_key"] == "ssh-ed25519 YENI"
    assert "ssh_host_key" not in storage.load_servers()["srv"]


def test_saving_keeps_pin_only_for_the_same_address(admin, servers_file):
    servers_file({"srv": {"name": "srv", "host": "h1", "port": 22, "ssh_host_key": "ssh-ed25519 AAA"}})
    form = {"name": "srv", "host": "h1", "port": "22", "skip_check": "on"}
    admin.post("/servers", data=form)
    assert storage.load_servers()["srv"]["ssh_host_key"] == "ssh-ed25519 AAA"
    admin.post("/servers", data={**form, "host": "h2"})
    assert "ssh_host_key" not in storage.load_servers()["srv"]
