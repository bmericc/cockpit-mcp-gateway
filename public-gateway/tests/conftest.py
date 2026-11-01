import json
import os
import sys

import pytest

# Gateway bunlar olmadan başlamaz ya da data/ altında gizli değer üretir; testler diske dokunmasın
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("MCP_API_KEY", "test-access-token")
os.environ.setdefault("ALLOWED_USERS", "admin")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit_log  # noqa: E402
import access  # noqa: E402
import connections  # noqa: E402
import mcp_gateway  # noqa: E402
import settings  # noqa: E402
import web_panel  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def servers_file(tmp_path, monkeypatch):
    """settings.SERVERS_FILE'ı geçici bir dosyaya yönlendirir, yazıcı fonksiyon döner."""
    path = tmp_path / "data" / "servers.json"
    monkeypatch.setattr(settings, "SERVERS_FILE", str(path))

    def write(servers):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(servers))

    return write


@pytest.fixture
def sample_server():
    return {
        "prod-db": {
            "name": "prod-db",
            "host": "10.0.0.5",
            "port": 2222,
            "user": "admin",
            "ssh_key_path": "/root/.ssh/id_rsa",
        }
    }


@pytest.fixture(autouse=True)
def ssh_dirs(tmp_path, monkeypatch):
    """SSH_LOGINS'i boş geçici klasörlere yönlendirir; gerçek ~/.ssh'a dokunulmaz.

    Dönen fonksiyon ilgili kullanıcının klasörüne sahte key dosyası ekler.
    """
    dirs = {"root": tmp_path / "root-ssh", "bmericc": tmp_path / "bmericc-ssh"}
    for d in dirs.values():
        d.mkdir()
    monkeypatch.setattr(settings, "SSH_LOGINS", ",".join(f"{u}:{d}" for u, d in dirs.items()))

    def add_key(user, name="id_rsa"):
        path = dirs[user] / name
        path.write_text("fake key")
        return str(path)

    return add_key


@pytest.fixture(autouse=True)
def shared_keys_file(tmp_path, monkeypatch):
    """Ortak SSH anahtarları dosyasını geçici bir yere yönlendirir."""
    path = tmp_path / "data" / "ssh_keys.json"
    monkeypatch.setattr(settings, "SSH_KEYS_FILE", str(path))
    return path


@pytest.fixture(autouse=True)
def no_public_ip_lookup(monkeypatch):
    """Panel testleri dış IP servislerine gerçekten istek atmasın (test_public_ip.py kendi sahtesini kurar)."""
    async def offline(url, proxy=None):
        return None

    monkeypatch.setattr(connections, "_fetch_ip", offline)


@pytest.fixture(autouse=True)
def no_default_proxy(monkeypatch):
    """Testler ortamdaki OUTBOUND_PROXY ayarından etkilenmesin."""
    monkeypatch.setattr(settings, "OUTBOUND_PROXY", "")


@pytest.fixture(autouse=True)
def no_cockpit_down_cache():
    """Doğrudan Cockpit başarısızlık önbelleği testler arasında taşınmasın."""
    connections._cockpit_direct_down.clear()
    yield
    connections._cockpit_direct_down.clear()


@pytest.fixture(autouse=True)
def audit_file(tmp_path, monkeypatch):
    """İşlem kayıtları geçici bir dosyaya yazılsın; dönen fonksiyon kayıtları (en yenisi başta) okur."""
    monkeypatch.setattr(audit_log, "LOG_FILE", str(tmp_path / "data" / "audit.log"))
    return lambda **filters: audit_log.read(1000, **filters)[0]


@pytest.fixture(autouse=True)
def default_security(monkeypatch):
    """Çoğu test ikinci faktörü ve giriş sınırını konu almaz; ilgili testler kendisi açar."""
    monkeypatch.setattr(settings, "MCP_API_KEY", "")
    access.login_limiter.clear()
    mcp_gateway.register_limiter.clear()
    web_panel.panel_sessions.clear()
