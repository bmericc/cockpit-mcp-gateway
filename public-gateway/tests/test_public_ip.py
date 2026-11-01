"""Panelde gateway'in dış IP adresinin gösterilmesi."""

import pytest
from fastapi.testclient import TestClient

import access
import connections
import main
from panel import login_as


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    monkeypatch.setattr(connections, "_public_ip_cache", {})


@pytest.fixture
def fake_lookup(monkeypatch):
    calls = []
    answers = {"https://api.ipify.org": "203.0.113.7", "https://api6.ipify.org": None,
               "https://ipv4.icanhazip.com": None, "https://ipv6.icanhazip.com": None}

    async def fetch(url, proxy=None):
        calls.append((url, proxy))
        return answers.get(url) if proxy is None else answers.get(("proxy", url))

    monkeypatch.setattr(connections, "_fetch_ip", fetch)
    return calls, answers


@pytest.fixture
def client(monkeypatch, servers_file):
    monkeypatch.setattr(access.authenticator, "allowed_users", {"admin"})
    c = TestClient(main.app)
    login_as(c, "admin")
    return c


def test_panel_shows_public_ip_and_firewall_example(client, fake_lookup):
    page = client.get("/").text
    assert "Gateway public IP address: <code>203.0.113.7</code>" in page
    assert "ufw allow from 203.0.113.7 to any port 9090 proto tcp" in page
    assert "source address=&quot;203.0.113.7&quot;" in page
    assert "9090/tcp" in page


@pytest.mark.anyio
async def test_falls_back_to_second_service(fake_lookup):
    calls, answers = fake_lookup
    answers["https://api.ipify.org"] = None
    answers["https://ipv4.icanhazip.com"] = "198.51.100.1"
    answers["https://api6.ipify.org"] = "2001:db8::1"
    assert await connections.public_ips() == {"ipv4": "198.51.100.1", "ipv6": "2001:db8::1"}


@pytest.mark.anyio
async def test_result_is_cached(fake_lookup):
    calls, _ = fake_lookup
    await connections.public_ips()
    count = len(calls)
    await connections.public_ips()
    assert len(calls) == count
    await connections.public_ips(force=True)
    assert len(calls) > count


def test_unknown_ip_shows_note_and_is_not_cached(client, fake_lookup):
    calls, answers = fake_lookup
    answers["https://api.ipify.org"] = None
    assert "could not be determined" in client.get("/").text
    count = len(calls)
    client.get("/")
    assert len(calls) > count


def test_refresh_button(client, fake_lookup):
    resp = client.post("/public-ip/refresh", follow_redirects=False)
    assert "203.0.113.7" in client.get(resp.headers["location"]).text
    assert TestClient(main.app).post("/public-ip/refresh").status_code == 403


@pytest.mark.anyio
async def test_fetch_ip_rejects_non_ip(monkeypatch):
    class Resp:
        text = "<html>hata</html>"

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return Resp()

    monkeypatch.setattr(connections.httpx, "AsyncClient", Client)
    assert await connections._fetch_ip("https://example.invalid") is None
