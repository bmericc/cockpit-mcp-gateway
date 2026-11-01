"""Sunuculara bağlantı: Cockpit, SSH tüneli, SSH yedeği, dağıtım tespiti ve dış IP."""
import asyncio
import ipaddress
import os
import shlex
import time
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from typing import Dict, Any
from urllib.parse import urlsplit

import asyncssh
import httpx

import audit_log
import cockpit_client
import fleet_tools
import outbound_proxy
import settings
import storage
from i18n import N, t

def public_server(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Şifreler olmadan, dışarıya gösterilebilir sunucu bilgisi."""
    out = {k: v for k, v in cfg.items() if k not in storage.SECRET_FIELDS}
    out["cockpit"] = bool(cfg.get("cockpit_user"))
    if out["cockpit"]:
        out["cockpit_url"] = cockpit_url(cfg)
    out["connection"] = proxy_label(cfg)
    return out

PROXY_UNREADABLE = N("The server's proxy setting could not be decrypted (SECRET_KEY may have changed); re-enter it in the panel.")

def server_proxy(cfg: Dict[str, Any]) -> str | None:
    """Sunucuya bağlanırken kullanılacak proxy adresi (None = doğrudan).

    Sunucuya özel ayar var ama çözülemiyorsa bağlantı kurulmaz (ProxyError): sessizce varsayılan
    proxy'ye ya da doğrudan bağlantıya düşmek, beklenmeyen bir IP adresinden bağlanmak demek olur.
    """
    if cfg.get("proxy"):
        stored = storage.decrypt_secret(cfg["proxy"])
        if stored is None:
            raise outbound_proxy.ProxyError(t(PROXY_UNREADABLE))
        return None if stored == "direct" else stored
    return settings.OUTBOUND_PROXY or None

def proxy_label(cfg: Dict[str, Any]) -> str:
    stored = storage.decrypt_secret(cfg["proxy"]) if cfg.get("proxy") else None
    if cfg.get("proxy") and stored is None:
        return t("proxy setting unreadable")
    if stored == "direct":
        return t("direct")
    if stored:
        return f"proxy {outbound_proxy.redact(stored)}"
    if settings.OUTBOUND_PROXY:
        return t("proxy {proxy} (default)", proxy=outbound_proxy.redact(settings.OUTBOUND_PROXY))
    return t("direct")

def cockpit_url(cfg: Dict[str, Any]) -> str:
    return cfg.get("cockpit_url") or f"https://{cfg['host']}:9090"
SSH_KEY_NAMES = ("id_ed25519", "id_ecdsa", "id_rsa")

def parse_ssh_logins(value: str) -> list[tuple[str, str]]:
    logins = []
    for item in value.split(","):
        user, _, ssh_dir = item.strip().partition(":")
        if user and ssh_dir:
            logins.append((user.strip(), ssh_dir.strip()))
    return logins

def find_ssh_keys(ssh_dir: str) -> list[str]:
    return [p for p in (os.path.join(ssh_dir, n) for n in SSH_KEY_NAMES) if os.path.isfile(p)]

def shared_client_keys() -> list:
    """Çözülebilen tüm ortak anahtarlar (asyncssh SSHKey nesneleri)."""
    keys = []
    for entry in storage.load_shared_keys().values():
        private = storage.decrypt_secret(entry.get("private", ""))
        if private is None:
            continue
        try:
            keys.append(asyncssh.import_private_key(private))
        except Exception:
            continue
    return keys

def login_candidates(cfg: Dict[str, Any]) -> list[tuple[str, list]]:
    """Önce sunucuya tanımlı kullanıcı, ardından SSH_LOGINS'teki diğer kullanıcılar denenir.

    Her kullanıcı için kendi .ssh klasöründeki anahtarlara ek olarak ortak anahtarlar da denenir.
    """
    logins = parse_ssh_logins(settings.SSH_LOGINS)
    shared = shared_client_keys()
    candidates = []
    cfg_user = cfg.get("user")
    if cfg_user:
        if cfg.get("ssh_key_path"):
            keys = [cfg["ssh_key_path"]]
        else:
            keys = find_ssh_keys(dict(logins).get(cfg_user, ""))
        keys = [*keys, *shared]
        if keys:
            candidates.append((cfg_user, keys))
    for user, ssh_dir in logins:
        if user == cfg_user:
            continue
        keys = [*find_ssh_keys(ssh_dir), *shared]
        if keys:
            candidates.append((user, keys))
    return candidates

class SSHError(Exception):
    """Bağlantı kurulamadı veya hiçbir kullanıcı ile giriş yapılamadı."""

HOST_KEY_CHANGED = N("The SSH host key of {host} has changed, so the connection was refused. "
                     "If the server was reinstalled, use Forget host key in the panel; otherwise investigate first.")

SSH_CONNECT_TIMEOUT = 15

def pin_host_key(cfg: Dict[str, Any], conn) -> None:
    """İlk bağlantıda görülen sunucu anahtarını saklar; sonraki bağlantılar yalnızca bunu kabul eder."""
    key = conn.get_server_host_key()
    if key is None:
        return
    public = key.export_public_key().decode().strip()
    cfg["ssh_host_key"] = public
    servers = storage.load_servers()
    stored = servers.get(cfg.get("name"))
    # Kaydedilmemiş bir denemede eski kayda başka adresin anahtarı yazılmasın
    if stored and (stored.get("host"), stored.get("port", 22)) == (cfg.get("host"), cfg.get("port", 22)):
        stored["ssh_host_key"] = public
        storage.save_servers(servers)

@asynccontextmanager
async def ssh_session(cfg: Dict[str, Any], connect_timeout: float = SSH_CONNECT_TIMEOUT):
    """Sunucuya bağlanır; (kullanıcı, bağlantı) döner.

    Kimlik doğrulama reddedilirse sıradaki kullanıcı denenir; ağ hatasında hemen vazgeçilir.
    """
    candidates = login_candidates(cfg)
    if not candidates:
        raise SSHError(t("Error: no usable SSH key found for '{name}'.", name=cfg.get("name", cfg.get("host"))))

    try:
        proxy = server_proxy(cfg)
    except outbound_proxy.ProxyError as e:
        raise SSHError(t("SSH connection error: {reason}", reason=e)) from e
    pinned = cfg.get("ssh_host_key")
    denied = []
    for user, keys in candidates:
        try:
            extra = {}
            if proxy:
                # Proxy tüneli üzerinden açılmış soket asyncssh'e verilir
                extra["sock"] = await outbound_proxy.open_tunnel(proxy, cfg["host"], cfg.get("port", 22), connect_timeout)
            cm = asyncssh.connect(
                cfg["host"],
                port=cfg.get("port", 22),
                username=user,
                client_keys=keys,
                # (güvenilen anahtarlar, CA'lar, iptal edilenler); None: ilk bağlantı, bkz. pin_host_key
                known_hosts=([asyncssh.import_public_key(pinned)], [], []) if pinned else None,
                # asyncssh'de varsayılan olarak bağlantı için zaman aşımı yok; takılan sunucu beklenmesin
                connect_timeout=connect_timeout,
                **extra,
            )
            conn = await cm.__aenter__()
        except asyncssh.PermissionDenied as e:
            # Kimlik doğrulama reddedildi: sıradaki kullanıcıyı dene
            denied.append(f"{user}: {e.reason}")
            continue
        except asyncssh.HostKeyNotVerifiable as e:
            raise SSHError(t(HOST_KEY_CHANGED, host=cfg["host"])) from e
        except Exception as e:
            # Ağ/bağlantı hatasında diğer kullanıcıları denemenin anlamı yok
            reason = t("connection timed out") if isinstance(e, (asyncio.TimeoutError, TimeoutError)) else (str(e) or type(e).__name__)
            raise SSHError(t("SSH connection error: {reason}", reason=reason)) from e
        try:
            if not pinned:
                pin_host_key(cfg, conn)
            yield user, conn
        finally:
            await cm.__aexit__(None, None, None)
        return

    raise SSHError(t("SSH authentication failed, users tried:") + "\n" + "\n".join(denied))

# --- İşlem kaydı (audit log) bağlamı ---
# MCP bağlantısını açan kullanıcı; /sse isteğinde atanır, araç çağrıları aynı bağlamı devralır.
_mcp_actor: ContextVar[Dict[str, Any]] = ContextVar("mcp_actor", default={})
# Bir araç çağrısı sırasında sunucularda çalıştırılan komutlar (araç çağrısı dışında None)
_command_log: ContextVar[list | None] = ContextVar("command_log", default=None)

def _recorded(run: fleet_tools.Runner) -> fleet_tools.Runner:
    """Runner'ı, çalıştırdığı komutları geçerli araç çağrısının kaydına ekleyecek şekilde sarar."""
    async def wrapper(argv, privileged: bool = False, timeout: int = fleet_tools.DEFAULT_TIMEOUT):
        result = await run(argv, privileged=privileged, timeout=timeout)
        commands = _command_log.get()
        if commands is not None:
            commands.append({
                "command": argv if isinstance(argv, str) else shlex.join(argv),
                "as_root": privileged, "user": result.user, "via": result.via,
                "exit_status": result.exit_status,
            })
        return result
    return wrapper

DISTRO_FIELDS = ("version", "firewall", "selinux", "apparmor")

def needs_distro(cfg: Dict[str, Any]) -> bool:
    """Dağıtım bilgisi hiç yoksa ya da alanları eksikse (eski biçim) True."""
    distro = cfg.get("distro") or {}
    return any(field not in distro for field in DISTRO_FIELDS)

def remember_distro(server_name: str, distro: Dict[str, Any]) -> None:
    servers = storage.load_servers()
    if server_name in servers:
        servers[server_name]["distro"] = distro
        storage.save_servers(servers)

async def refresh_distro(server_name: str, cfg: Dict[str, Any], run: fleet_tools.Runner) -> Dict[str, Any] | None:
    """Dağıtımı sunucudan okur, kayıttan farklıysa günceller.

    Okunamazsa kayıtlı bilgiyle devam edilir: asıl işlem bu yüzden engellenmesin.
    """
    try:
        distro = await fleet_tools.detect_distro(run)
    except Exception:
        distro = None
    if distro and distro != cfg.get("distro"):
        remember_distro(server_name, distro)
    return distro or cfg.get("distro")

@asynccontextmanager
async def connect_server(server_name: str, cfg: Dict[str, Any]):
    """open_runner + her bağlantıda dağıtım kontrolü. (runner, bağlantı bilgisi, dağıtım) döner."""
    async with open_runner(cfg) as (run, info):
        yield run, info, await refresh_distro(server_name, cfg, run)

def make_runner(user: str, conn) -> fleet_tools.Runner:
    async def run(argv, privileged: bool = False, timeout: int = fleet_tools.DEFAULT_TIMEOUT):
        if isinstance(argv, str):
            command = argv
            if privileged and user != "root":
                command = "sudo -n sh -c " + shlex.quote(argv)
        else:
            # Çıktıların ayrıştırılabilmesi için dil ayarını sabitle
            command = shlex.join(["env", "LC_ALL=C", *argv])
            if privileged and user != "root":
                command = "sudo -n " + command
        try:
            result = await asyncio.wait_for(conn.run(command, check=False), timeout)
        except asyncio.TimeoutError:
            return fleet_tools.CommandResult(user, None, "", t("Command timed out after {timeout} seconds.", timeout=timeout), "ssh")
        return fleet_tools.CommandResult(user, result.exit_status, result.stdout or "", result.stderr or "", "ssh")
    return _recorded(run)

def make_cockpit_runner(session: cockpit_client.CockpitSession) -> fleet_tools.Runner:
    async def run(argv, privileged: bool = False, timeout: int = fleet_tools.DEFAULT_TIMEOUT):
        if isinstance(argv, str):
            argv = ["sh", "-c", argv]
        return await session.spawn(argv, superuser=privileged, timeout=timeout)
    return _recorded(run)

# Doğrudan Cockpit'e ulaşılamayan sunucularda her çağrıda zaman aşımı beklenmesin:
# başarısızlık bir süre hatırlanır ve bu sürede doğrudan SSH tüneli kullanılır.
COCKPIT_DOWN_TTL = 600
_cockpit_direct_down: Dict[str, tuple[float, str]] = {}

def _cockpit_down_key(cfg: Dict[str, Any]) -> str:
    return f"{cockpit_url(cfg)}|{cfg.get('proxy', '')}"

def cockpit_password(cfg: Dict[str, Any]) -> str | None:
    return storage.decrypt_secret(cfg.get("cockpit_password", ""))

COCKPIT_PASSWORD_UNREADABLE = N("The Cockpit password could not be decrypted (no password was entered, or SECRET_KEY may have changed).")

async def connect_cockpit_direct(cfg: Dict[str, Any], password: str,
                                 connect_timeout: float = 10) -> cockpit_client.CockpitSession:
    """Cockpit'e doğrudan (gerekirse proxy ile) bağlanır. Sonuç tünel kararı için hatırlanır."""
    key = _cockpit_down_key(cfg)
    try:
        session = cockpit_client.CockpitSession(
            cockpit_url(cfg), cfg["cockpit_user"], password,
            verify_tls=bool(cfg.get("cockpit_verify_tls")), connect_timeout=connect_timeout,
            proxy=server_proxy(cfg),
        )
        await session.connect()
    except cockpit_client.CockpitAuthError:
        raise
    except outbound_proxy.ProxyError as e:
        raise cockpit_client.CockpitError(str(e)) from e
    except cockpit_client.CockpitError as e:
        _cockpit_direct_down[key] = (time.time() + COCKPIT_DOWN_TTL, str(e))
        raise
    _cockpit_direct_down.pop(key, None)
    return session

def cockpit_recently_down(cfg: Dict[str, Any]) -> str | None:
    entry = _cockpit_direct_down.get(_cockpit_down_key(cfg))
    if entry and entry[0] > time.time():
        return entry[1]
    return None

@asynccontextmanager
async def cockpit_over_ssh(cfg: Dict[str, Any], conn, password: str, connect_timeout: float = 10):
    """SSH bağlantısının içinden sunucunun kendi Cockpit'ine (localhost) bağlanır.

    Böylece Cockpit portunu (9090) dışarıya açmak gerekmez. Bağlantı SSH ile korunduğu için
    tünel ucunda sertifika doğrulanmaz.
    """
    parts = urlsplit(cockpit_url(cfg))
    port = parts.port or 9090
    listener = await conn.forward_local_port("127.0.0.1", 0, "localhost", port)
    try:
        session = cockpit_client.CockpitSession(
            f"{parts.scheme}://127.0.0.1:{listener.get_port()}", cfg["cockpit_user"], password,
            verify_tls=False, connect_timeout=connect_timeout,
        )
        await session.connect()
        try:
            yield session
        finally:
            await session.close()
    finally:
        listener.close()

@asynccontextmanager
async def open_runner(cfg: Dict[str, Any]):
    """Sıra: Cockpit (doğrudan) -> SSH tüneli içinden Cockpit -> düz SSH. (runner, bağlantı bilgisi) döner."""
    cockpit_error = None
    password = None
    if cfg.get("cockpit_user"):
        password = cockpit_password(cfg)
        if password is None:
            cockpit_error = t(COCKPIT_PASSWORD_UNREADABLE)
        elif (down := cockpit_recently_down(cfg)) is not None:
            cockpit_error = down
        else:
            session = None
            try:
                session = await connect_cockpit_direct(cfg, password)
            except cockpit_client.CockpitAuthError as e:
                # Şifre yanlış: tünelden de aynı sonuç alınır
                cockpit_error, password = str(e), None
            except cockpit_client.CockpitError as e:
                cockpit_error = str(e)
            if session is not None:
                try:
                    yield make_cockpit_runner(session), {"via": "cockpit", "user": cfg["cockpit_user"]}
                finally:
                    await session.close()
                return

    try:
        async with ssh_session(cfg) as (user, conn), AsyncExitStack() as stack:
            session = None
            if password is not None:
                try:
                    session = await stack.enter_async_context(cockpit_over_ssh(cfg, conn, password))
                except Exception as e:
                    cockpit_error = t("{error}; also failed over the SSH tunnel: {reason}",
                                      error=cockpit_error, reason=str(e) or type(e).__name__)
            if session is not None:
                yield make_cockpit_runner(session), {"via": "cockpit-ssh", "user": cfg["cockpit_user"], "ssh_user": user}
                return
            info = {"via": "ssh", "user": user}
            if cockpit_error:
                info["cockpit_error"] = cockpit_error
            yield make_runner(user, conn), info
    except SSHError as e:
        if cockpit_error:
            raise SSHError(t("{error}\nSSH fallback also failed: {reason}", error=cockpit_error, reason=e)) from e
        raise

CHECK_TIMEOUT = 10

# --- Gateway'in dış (public) IP adresi ---
# Uzaktaki sunucuların güvenlik duvarında izin verilmesi gereken adres; panelde gösterilir.
PUBLIC_IP_SERVICES = {
    "ipv4": ["https://api.ipify.org", "https://ipv4.icanhazip.com"],
    "ipv6": ["https://api6.ipify.org", "https://ipv6.icanhazip.com"],
}
PUBLIC_IP_CACHE_TTL = 600
# Anahtar: proxy adresi ("" = doğrudan); değer: {"at": zaman, "value": {...}}
_public_ip_cache: Dict[str, Dict[str, Any]] = {}

async def _fetch_ip(url: str, proxy: str | None = None) -> str | None:
    try:
        if proxy:
            status, _, body = await outbound_proxy.http_get(proxy, url, timeout=5)
            if status != 200:
                return None
            value = body.decode(errors="replace").strip()
        else:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(url)
            value = resp.text.strip()
        ipaddress.ip_address(value)
        return value
    except Exception:
        return None

async def public_ips(force: bool = False, proxy: str | None = None) -> Dict[str, str | None]:
    """{"ipv4": ..., "ipv6": ...}; belirlenemeyenler None. Sonuç 10 dakika önbelleklenir.

    proxy verilirse adres o proxy üzerinden sorgulanır (sunucuların göreceği adres).
    """
    cached = _public_ip_cache.get(proxy or "")
    if not force and cached and time.time() - cached["at"] < PUBLIC_IP_CACHE_TTL:
        return cached["value"]

    async def first(urls):
        for url in urls:
            ip = await _fetch_ip(url, proxy)
            if ip:
                return ip
        return None

    v4, v6 = await asyncio.gather(first(PUBLIC_IP_SERVICES["ipv4"]), first(PUBLIC_IP_SERVICES["ipv6"]))
    value = {"ipv4": v4, "ipv6": v6}
    # Hiçbiri bulunamadıysa önbelleğe alma; sonraki sayfa açılışında tekrar denensin
    if v4 or v6:
        _public_ip_cache[proxy or ""] = {"at": time.time(), "value": value}
    return value

async def check_server(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Sunucuya gerçekten bağlanıp basit bir komut çalıştırarak bilgileri doğrular.

    MCP araçlarıyla aynı sıra izlenir: Cockpit (doğrudan), SSH tüneli içinden Cockpit, düz SSH.
    Yalnızca düz SSH ile bağlanılabiliyorsa sonuç başarılı ama "warning" işaretlidir.
    """
    checked_at = time.strftime("%Y-%m-%d %H:%M:%S")

    async def spawn_true(session) -> str | None:
        result = await session.spawn(["true"], timeout=CHECK_TIMEOUT)
        if result.exit_status != 0:
            return t("Could not run a command through Cockpit: {reason}", reason=result.stderr.strip() or result.exit_status)
        return None

    async def try_ssh(password: str | None) -> tuple[str | None, str | None]:
        """(SSH hatası, tünel hatası). password verilirse SSH içinden Cockpit de denenir."""
        tunnel_error = None
        try:
            async with ssh_session(cfg, connect_timeout=CHECK_TIMEOUT) as (user, conn):
                if password is not None:
                    try:
                        async with cockpit_over_ssh(cfg, conn, password, CHECK_TIMEOUT) as session:
                            tunnel_error = await spawn_true(session)
                    except Exception as e:
                        tunnel_error = str(e) or type(e).__name__
                result = await make_runner(user, conn)("true", timeout=CHECK_TIMEOUT)
            if result.exit_status != 0:
                return t("SSH command failed: {reason}", reason=result.stderr.strip() or result.exit_status), tunnel_error
            return None, tunnel_error
        except Exception as e:
            return str(e), tunnel_error

    if cfg.get("cockpit_user"):
        password = cockpit_password(cfg)
        error = None
        if password is None:
            error = t("The Cockpit password could not be decrypted; re-enter the password.")
        else:
            session = None
            try:
                session = await connect_cockpit_direct(cfg, password, CHECK_TIMEOUT)
                error = await spawn_true(session)
            except cockpit_client.CockpitAuthError as e:
                # Şifre yanlış: tünel denemenin anlamı yok
                error, password = str(e), None
            except cockpit_client.CockpitError as e:
                error = str(e)
            finally:
                if session is not None:
                    await session.close()
        if error is None:
            return {"ok": True, "via": "cockpit", "message": t("Cockpit connection successful."), "at": checked_at}
        ssh_error, tunnel_error = await try_ssh(password)
        if ssh_error is None and password is not None and tunnel_error is None:
            return {"ok": True, "via": "cockpit-ssh", "at": checked_at,
                    "message": t("Connected to Cockpit over the SSH tunnel (the Cockpit port is not reachable from outside).")}
        if ssh_error is None:
            # MCP araçları da bu durumda SSH yedeğiyle çalışır: sunucu kullanılabilir, ama uyarı gösterilir
            detail = " " + t("Cockpit over the SSH tunnel: {error}", error=tunnel_error) if tunnel_error else ""
            return {"ok": True, "warning": True, "via": "ssh",
                    "message": t("Connected with the SSH fallback; Cockpit is not working: {error}.", error=error) + detail,
                    "at": checked_at}
        return {"ok": False, "via": None, "message": t("{error} (the SSH fallback is not working either: {ssh_error})", error=error, ssh_error=ssh_error),
                "at": checked_at}

    ssh_error, _ = await try_ssh(None)
    if ssh_error is None:
        return {"ok": True, "via": "ssh", "message": t("SSH connection successful."), "at": checked_at}
    return {"ok": False, "via": None, "message": ssh_error, "at": checked_at}

async def detect_server_distro(cfg: Dict[str, Any]) -> Dict[str, Any] | None:
    """Bağlantısı doğrulanmış sunucunun dağıtımı; öğrenilemezse ilk kullanımda yeniden denenir."""
    try:
        async with open_runner(cfg) as (run, _):
            return await fleet_tools.detect_distro(run)
    except Exception:
        return None

MIGRATION_CONCURRENCY = 10

async def migrate_servers() -> None:
    """Eski sürümlerden kalan kayıtlara dağıtım ve sürüm bilgisini ekler.

    Yalnızca eksik kayıtlara dokunur, her açılışta güvenle çalışır. Ulaşılamayan sunucular
    ilk paket aracı çağrısında, Test düğmesiyle ya da sonraki açılışta tamamlanır.
    """
    pending = {name: cfg for name, cfg in storage.load_servers().items() if needs_distro(cfg)}
    if not pending:
        return
    limit = asyncio.Semaphore(MIGRATION_CONCURRENCY)

    async def one(name: str, cfg: Dict[str, Any]) -> str | None:
        async with limit:
            distro = await detect_server_distro(cfg)
        if distro is None:
            return name
        remember_distro(name, distro)
        return None

    results = await asyncio.gather(*(one(name, cfg) for name, cfg in pending.items()))
    failed = [name for name in results if name]
    audit_log.record("system", "distro_migration", "error" if failed else "ok",
                     args={"servers": list(pending), "failed": failed})
