"""Web paneli: oturum, CSRF, sayfalar ve giriş formları."""
import html
import json
import re
import secrets
import time
from contextvars import ContextVar
from typing import Dict, Any
from urllib.parse import urlencode

import asyncssh
from fastapi import APIRouter
from fastapi import Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse

import access
import audit_log
import connections
import i18n
import outbound_proxy
import settings
import storage
from i18n import N, t

router = APIRouter()

# Sunucu tarafı oturum listesi (kimlik -> bitiş): çıkış çerezi de iptal eder, yeniden başlatma herkesi çıkarır
panel_sessions: Dict[str, float] = {}

# FastAPI Web Uç Noktaları
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.:_-]{1,253}$")

def audit_web(request: Request, action: str, status: str = "ok", **fields: Any) -> None:
    """Panelde yapılan bir işlemi kaydeder (kullanıcı oturumdan alınır; fields ile ezilebilir)."""
    fields.setdefault("user", (request.session.get("user") or {}).get("username"))
    audit_log.record("web", action, status, **access.request_actor(request), **fields)

# Geçerli panel oturumunun CSRF token'ı; formlar csrf_field() ile bunu taşır
_csrf_token: ContextVar[str] = ContextVar("csrf_token", default="")

def csrf_field() -> str:
    return f'<input type="hidden" name="csrf" value="{html.escape(_csrf_token.get())}">'

def current_user(request: Request) -> str | None:
    """Cockpit ile giriş yapmış, oturumu sunucuda geçerli ve hâlâ yetkili olan kullanıcının adı."""
    session = request.session
    username = (session.get('user') or {}).get('username')
    expires = panel_sessions.get(session.get('sid'), 0)
    if not username or expires < time.time() or not access.authenticator.is_allowed(username):
        return None
    _csrf_token.set(session.get('csrf', ''))
    return username

async def panel_user(request: Request) -> str | None:
    """Değişiklik yapan panel isteği: oturuma ek olarak formdaki CSRF token'ı da doğrulanır."""
    username = current_user(request)
    if not username:
        return None
    value = (await request.form()).get("csrf")
    expected = request.session.get("csrf", "")
    if not expected or not isinstance(value, str) or not secrets.compare_digest(value.encode(), expected.encode()):
        return None
    return username

def start_panel_session(request: Request, username: str) -> None:
    now = time.time()
    for sid in [sid for sid, expires in panel_sessions.items() if expires < now]:
        del panel_sessions[sid]
    request.session.clear()
    sid = secrets.token_urlsafe(32)
    panel_sessions[sid] = now + settings.SESSION_TTL
    request.session.update(user={"username": username}, sid=sid, csrf=secrets.token_urlsafe(32))

BOOTSTRAP_CDN = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist"
# Dosyalardan hesaplandı: CDN içeriği değişirse tarayıcı yüklemez
BOOTSTRAP_CSS_SRI = "sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH"
BOOTSTRAP_JS_SRI = "sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz"

PAGE_STYLE = f"""
<link rel="stylesheet" href="{BOOTSTRAP_CDN}/css/bootstrap.min.css" integrity="{BOOTSTRAP_CSS_SRI}" crossorigin="anonymous">
<style>
  form.add, div.add {{ display: grid; grid-template-columns: 220px 1fr; gap: 8px; align-items: center; max-width: 720px; }}
  form.add .form-check-input, form.add .btn {{ justify-self: start; margin: 0; }}
  @media (max-width: 575.98px) {{ form.add, div.add {{ grid-template-columns: 1fr; }} }}
  .note {{ color: var(--bs-secondary-color); font-size: .8125rem; }}
  .err {{ color: var(--bs-danger); }}
  .ok {{ color: var(--bs-success); }}
  .warn {{ color: #a15c00; }}
  td form {{ display: inline; }}
  textarea.key {{ font-family: var(--bs-font-monospace); font-size: 12px; }}
  table.logs td {{ vertical-align: top; }}
  table.logs pre {{ white-space: pre-wrap; word-break: break-word; margin: 4px 0; font-size: 12px; max-height: 320px; overflow: auto; }}
  code {{ word-break: break-word; }}
</style>
"""

# Üst menü: (anahtar, adres, etiket)
NAV_ITEMS = [
    ("servers", "/", N("Servers")),
    ("ssh-keys", "/#ssh-keys", N("SSH Keys")),
    ("logs", "/logs", N("Audit Log")),
]

def navbar(user: str | None, active: str = "") -> str:
    """Üst menü; giriş yapılmamışsa yalnızca başlık gösterilir."""
    menu = ""
    if user:
        links = "".join(
            f'<li class="nav-item"><a class="nav-link{" active" if key == active else ""}" href="{href}">{t(label)}</a></li>'
            for key, href, label in NAV_ITEMS
        )
        menu = f"""
    <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#topnav"
            aria-controls="topnav" aria-expanded="false" aria-label="{t('Menu')}">
      <span class="navbar-toggler-icon"></span>
    </button>
    <div class="collapse navbar-collapse" id="topnav">
      <ul class="navbar-nav me-auto">{links}</ul>
      <span class="navbar-text me-3">{html.escape(user)}</span>
      <form method="post" action="/logout" class="d-inline">{csrf_field()}<button class="btn btn-sm btn-outline-light">{t('Log out')}</button></form>
    </div>"""
    return f"""<nav class="navbar navbar-expand-md navbar-dark bg-dark mb-4">
  <div class="container">
    <a class="navbar-brand" href="/">Cockpit MCP Gateway</a>{menu}
  </div>
</nav>"""

def render_page(title: str, body: str, status_code: int = 200, user: str | None = None,
                active: str = "") -> HTMLResponse:
    """Tüm panel sayfaları için ortak HTML iskeleti (başlık, karakter seti, stil, üst menü)."""
    return HTMLResponse(f"""<!doctype html>
<html lang="{i18n.get_language()}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} · Cockpit MCP Gateway</title>
  {PAGE_STYLE}
</head>
<body class="bg-body-tertiary">
{navbar(user, active)}
<main class="container pb-5">
{body}
</main>
<script src="{BOOTSTRAP_CDN}/js/bootstrap.bundle.min.js" integrity="{BOOTSTRAP_JS_SRI}" crossorigin="anonymous"></script>
</body>
</html>""", status_code=status_code)

def forbidden() -> HTMLResponse:
    title = t("Access denied")
    return render_page(title, f"<h2 class='h4'>{title}</h2><p><a class='btn btn-primary' href='/login'>{t('Log in')}</a></p>", 403)

def invalid_login_request() -> HTMLResponse:
    title = t("Invalid login request")
    detail = t("The login request is invalid or has expired. Connect again from the MCP client.")
    return render_page(title, f"<h2 class='h4'>{title}</h2><p>{detail}</p>", 400)

def public_ip_html(ips: Dict[str, str | None], proxy_ips: Dict[str, str | None] | None = None) -> str:
    """proxy_ips: varsayılan proxy (OUTBOUND_PROXY) tanımlıysa onun üzerinden görünen adres."""
    proxy_html = ""
    if settings.OUTBOUND_PROXY:
        via = [ip for ip in ((proxy_ips or {}).get("ipv4"), (proxy_ips or {}).get("ipv6")) if ip]
        shown = " ".join(f"<code>{html.escape(ip)}</code>" for ip in via) or f"<span class='err'>{t('could not be determined')}</span>"
        label = t("Default proxy ({proxy}) egress IP address:", proxy=html.escape(outbound_proxy.redact(settings.OUTBOUND_PROXY)))
        proxy_html = (f"<p>{label} {shown}<br><span class='note'>"
                      f"{t('Servers that use the proxy see this address; allow this address on them.')}</span></p>")
    found = [ip for ip in (ips.get("ipv4"), ips.get("ipv6")) if ip]
    if not found:
        return proxy_html + "<p class='note'>" + t(
            "The gateway's public IP address could not be determined (outbound access may be blocked).") + "</p>"
    codes = " ".join(f"<code>{html.escape(ip)}</code>" for ip in found)
    v4 = ips.get("ipv4")
    example = ""
    if v4:
        ufw_rule = f"ufw allow from {v4} to any port 9090 proto tcp"
        firewalld_rule = (f"firewall-cmd --permanent --add-rich-rule='rule family=\"ipv4\" source address=\"{v4}\" "
                          f"port port=\"9090\" protocol=\"tcp\" accept' && firewall-cmd --reload")
        example = (f"<br>{t('Example (ufw):')} <code>{html.escape(ufw_rule)}</code>"
                   f"<br>{t('Example (firewalld):')} <code>{html.escape(firewalld_rule)}</code>")
    note = t("On remote servers, allow this address for Cockpit (9090/tcp) and 22/tcp for the SSH fallback. "
             "Servers on the same local network see the local IP address of the machine running the gateway instead.")
    return proxy_html + (
        f"<p>{t('Gateway public IP address:')} {codes} "
        f"<form method='post' action='/public-ip/refresh' style='display:inline'>{csrf_field()}<button class='btn btn-sm btn-outline-secondary'>{t('Refresh')}</button></form><br>"
        f"<span class='note'>{note}{example}</span></p>"
    )

def server_row(cfg: Dict[str, Any]) -> str:
    e = lambda v: html.escape(str(v)) if v not in (None, "") else "—"
    name = cfg["name"]
    cockpit = f"{e(cfg['cockpit_user'])} @ {e(connections.cockpit_url(cfg))}" if cfg.get("cockpit_user") else "—"
    ssh = f"{e(cfg.get('user') or t('automatic'))} @ {e(cfg['host'])}:{e(cfg.get('port', 22))}"
    check = cfg.get("last_check")
    if check:
        if not check.get("ok"):
            mark, css = "✗", "err"
        elif check.get("warning"):
            mark, css = "⚠", "warn"
        else:
            mark, css = "✓", "ok"
        status = (f"<span class='{css}' title='{e(check.get('message'))}'>{mark} {e(check.get('message'))}</span>"
                  f"<br><span class='note'>{e(check.get('at'))}</span>")
    else:
        status = f"<span class='note'>{t('Not tested')}</span>"
    quoted = html.escape(name)
    distro = cfg.get("distro") or {}
    distro_name = distro.get("name") or t("distribution not detected yet")
    forget_key = (f"<form method='post' action='/servers/{quoted}/forget-host-key'>{csrf_field()}"
                  f"<button class='btn btn-sm btn-outline-secondary'>{t('Forget host key')}</button></form> "
                  if cfg.get("ssh_host_key") else "")
    return (
        f"<tr><td><b>{e(name)}</b><br><span class='note'>{e(connections.proxy_label(cfg))}</span></td>"
        f"<td>{e(distro_name)}</td><td>{e(distro.get('version'))}</td><td>{cockpit}</td><td>{ssh}</td><td>{status}</td>"
        f"<td><form method='post' action='/servers/{quoted}/test'>{csrf_field()}<button class='btn btn-sm btn-outline-primary'>{t('Test')}</button></form> "
        f"{forget_key}<form method='post' action='/servers/{quoted}/delete' "
        f"onsubmit=\"return confirm('{t('Delete {name}?', name=quoted)}')\">{csrf_field()}<button class='btn btn-sm btn-outline-danger'>{t('Delete')}</button></form></td></tr>"
    )

@router.get("/", response_class=HTMLResponse)
async def index(request: Request, error: str = "", info: str = ""):
    username = current_user(request)
    if not username:
        return RedirectResponse(url="/login", status_code=303)

    servers = storage.load_servers()
    rows = "".join(server_row(cfg) for cfg in servers.values()) or f"<tr><td colspan='7'>{t('No servers defined yet.')}</td></tr>"
    error_html = f"<div class='alert alert-danger'>{html.escape(error)}</div>" if error else ""
    info_html = f"<div class='alert alert-success'>{html.escape(info)}</div>" if info else ""
    key_rows = "".join(shared_key_row(k) for k in storage.load_shared_keys().values()) or \
        f"<tr><td colspan='4'>{t('No shared keys yet.')}</td></tr>"
    add_note = t(
        "Saving with an existing name updates that server. If the password field is left empty, the current password is kept. "
        "Before saving, the gateway connects to the server to verify the details; if no connection can be made, nothing is saved.")
    default_proxy = "" if not settings.OUTBOUND_PROXY else " = " + html.escape(outbound_proxy.redact(settings.OUTBOUND_PROXY))
    proxy_note = (
        t("Cockpit and SSH connections go through this proxy; the server sees the proxy's IP address. "
          "If left empty, the current setting is kept (a new server uses the default).")
        + " <code>default</code>: " + t("the default (OUTBOUND_PROXY in .env{value})", value=default_proxy)
        + ", <code>direct</code>: " + t("no proxy.") + " "
        + t("A username/password can be given in the address (socks5://user:password@host:1080) and is stored encrypted.")
    )
    keys_note = t(
        "The keys here are tried on all servers, for every user, in the SSH fallback. "
        "To use one, add the public part of the key to <code>~/.ssh/authorized_keys</code> on the servers. "
        "Private keys are stored encrypted in <code>data/ssh_keys.json</code> and are not shown in the panel.")

    return render_page(t("Servers"), f"""
        {info_html}{error_html}
        <div class="card mb-4"><div class="card-body">
          <h2 class="h5">{t("Welcome, {user}!", user=html.escape(username))}</h2>
          <p>{t("MCP Gateway is active. MCP endpoint:")} <code>{html.escape(settings.PUBLIC_URL)}/mcp</code><br>
          <span class="note">{t("For clients that only support the deprecated HTTP+SSE transport:")} <code>{html.escape(settings.PUBLIC_URL)}/sse</code></span></p>
          {public_ip_html(await connections.public_ips(), await connections.public_ips(proxy=settings.OUTBOUND_PROXY) if settings.OUTBOUND_PROXY else None)}
        </div></div>

        <div class="card mb-4"><div class="card-header">{t("Registered Servers")}</div><div class="card-body">
        <div class="table-responsive"><table class="table table-sm table-bordered table-hover align-middle bg-body mb-0">
          <tr><th>{t("Name")}</th><th>{t("Distribution")}</th><th>{t("Version")}</th><th>Cockpit</th><th>{t("SSH (fallback)")}</th><th>{t("Connection status")}</th><th></th></tr>
          {rows}
        </table></div>
        </div></div>

        <div class="card mb-4"><div class="card-header">{t("Add / Update Server")}</div><div class="card-body">
        <p class="note">{add_note}</p>
        <form class="add" method="post" action="/servers">
          {csrf_field()}
          <label>{t("Server name *")}</label><input class="form-control form-control-sm" name="name" required placeholder="prod-db">
          <label>{t("Host (IP / domain name) *")}</label><input class="form-control form-control-sm" name="host" required placeholder="192.168.0.98">
          <fieldset class="border rounded p-3" style="grid-column: 1 / -1">
            <legend class="float-none w-auto px-2 fs-6 mb-0">Cockpit</legend>
            <div class="add">
              <label>{t("Cockpit user")}</label><input class="form-control form-control-sm" name="cockpit_user" placeholder="bmericc">
              <label>{t("Cockpit password")}</label><input class="form-control form-control-sm" name="cockpit_password" type="password" autocomplete="new-password">
              <label>{t("Cockpit address")}</label><input class="form-control form-control-sm" name="cockpit_url" placeholder="{t("https://HOST:9090 (if empty)")}">
              <label>{t("Verify TLS certificate")}</label><input name="cockpit_verify_tls" type="checkbox" class="form-check-input">
            </div>
          </fieldset>
          <fieldset class="border rounded p-3" style="grid-column: 1 / -1">
            <legend class="float-none w-auto px-2 fs-6 mb-0">{t("SSH (fallback)")}</legend>
            <div class="add">
              <label>{t("SSH user")}</label><input class="form-control form-control-sm" name="user" placeholder="{t("SSH_LOGINS order if empty")}">
              <label>{t("SSH port")}</label><input class="form-control form-control-sm" name="port" type="number" value="22">
              <label>{t("SSH key path")}</label><input class="form-control form-control-sm" name="ssh_key_path" placeholder="{t("the user's .ssh folder if empty")}">
            </div>
          </fieldset>
          <fieldset class="border rounded p-3" style="grid-column: 1 / -1">
            <legend class="float-none w-auto px-2 fs-6 mb-0">{t("Connection proxy")}</legend>
            <div class="add">
              <label>Proxy</label><input class="form-control form-control-sm" name="proxy" autocomplete="off"
                placeholder="socks5://host:1080 · http://host:3128 · direct · default">
            </div>
            <p class="note mt-2 mb-0">{proxy_note}</p>
          </fieldset>
          <label>{t("Save without testing the connection")}</label><input name="skip_check" type="checkbox" class="form-check-input">
          <span></span><button type="submit" class="btn btn-primary">{t("Save")}</button>
        </form>
        </div></div>

        <div class="card mb-4" id="ssh-keys"><div class="card-header">{t("Shared SSH Keys")}</div><div class="card-body">
        <p class="note">{keys_note}</p>
        <div class="table-responsive"><table class="table table-sm table-bordered table-hover align-middle bg-body">
          <tr><th>{t("Name")}</th><th>{t("Type / fingerprint")}</th><th>{t("Public key (authorized_keys line)")}</th><th></th></tr>
          {key_rows}
        </table></div>
        <form class="add" method="post" action="/ssh-keys">
          {csrf_field()}
          <label>{t("Key name *")}</label><input class="form-control form-control-sm" name="name" required placeholder="{t("shared-key")}">
          <label>{t("Private key *")}</label><textarea class="key form-control form-control-sm" name="private_key" rows="6" required
            placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea>
          <label>{t("Key passphrase")}</label><input class="form-control form-control-sm" name="passphrase" type="password" autocomplete="off" placeholder="{t("empty if none")}">
          <span></span><button type="submit" class="btn btn-primary">{t("Add key")}</button>
        </form>
        <form class="add" method="post" action="/ssh-keys/generate" style="margin-top:12px">
          {csrf_field()}
          <label>{t("Generate a new key")}</label><input class="form-control form-control-sm" name="name" required placeholder="{t("key name")}">
          <span></span><button type="submit" class="btn btn-outline-primary">{t("Generate Ed25519 key")}</button>
        </form>
        </div></div>
    """, user=username, active="servers")

def _redirect_error(message: str) -> RedirectResponse:
    return RedirectResponse(url="/?" + urlencode({"error": message}), status_code=303)

def _redirect_info(message: str) -> RedirectResponse:
    return RedirectResponse(url="/?" + urlencode({"info": message}), status_code=303)

@router.post("/servers")
async def save_server(request: Request):
    if not await panel_user(request):
        return forbidden()
    form = await request.form()
    field = lambda k: (form.get(k) or "").strip()

    name, host = field("name"), field("host")
    if not SERVER_NAME_RE.match(name):
        return _redirect_error(t("The server name may only contain letters, digits, dots, underscores and hyphens."))
    if not HOST_RE.match(host):
        return _redirect_error(t("Invalid host."))
    try:
        port = int(field("port") or 22)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        return _redirect_error(t("Invalid SSH port."))
    cockpit_url_value = field("cockpit_url")
    if cockpit_url_value and not re.match(r"^https?://[^\s/]+(:\d+)?/?$", cockpit_url_value):
        return _redirect_error(t("The Cockpit address must look like https://host:9090."))

    servers = storage.load_servers()
    existing = servers.get(name, {})
    cfg: Dict[str, Any] = {"name": name, "host": host, "port": port}
    if (existing.get("host"), existing.get("port", 22)) == (host, port) and existing.get("ssh_host_key"):
        cfg["ssh_host_key"] = existing["ssh_host_key"]
    for key in ("user", "ssh_key_path", "cockpit_user"):
        if field(key):
            cfg[key] = field(key)
    if cockpit_url_value:
        cfg["cockpit_url"] = cockpit_url_value.rstrip("/")
    if form.get("cockpit_verify_tls"):
        cfg["cockpit_verify_tls"] = True

    proxy_value = field("proxy")
    if proxy_value == "":
        if existing.get("proxy"):
            cfg["proxy"] = existing["proxy"]
    elif proxy_value == "direct":
        cfg["proxy"] = storage.encrypt_secret("direct")
    elif proxy_value != "default":
        try:
            outbound_proxy.parse_proxy(proxy_value)
        except ValueError as e:
            return _redirect_error(t("Invalid proxy: {error}", error=e))
        cfg["proxy"] = storage.encrypt_secret(proxy_value)

    if cfg.get("cockpit_user"):
        password = form.get("cockpit_password") or ""
        if password:
            cfg["cockpit_password"] = storage.encrypt_secret(password)
        elif existing.get("cockpit_password"):
            cfg["cockpit_password"] = existing["cockpit_password"]
        else:
            return _redirect_error(t("A password is required for the Cockpit user."))

    if form.get("skip_check"):
        # Bilgiler test edilmedi: eski (başka ayarlara ait olabilecek) durum gösterilmesin
        servers[name] = cfg
        storage.save_servers(servers)
        audit_web(request, "server_save", server=name, args=connections.public_server(cfg), result=t("Saved without a connection test."))
        return _redirect_info(t("'{name}' saved without a connection test.", name=name))

    check = await connections.check_server(cfg)
    audit_web(request, "server_save", "ok" if check["ok"] else "error", server=name, args=connections.public_server(cfg),
              via=check.get("via"), result=check["message"])
    if not check["ok"]:
        return _redirect_error(t("'{name}' was not saved, could not connect: {message}", name=name, message=check["message"]))
    cfg["last_check"] = check
    if distro := await connections.detect_server_distro(cfg):
        cfg["distro"] = distro
    # Kontrol sürerken dosya değişmiş olabilir; en güncel hâli üzerine yaz
    servers = storage.load_servers()
    servers[name] = cfg
    storage.save_servers(servers)
    if check.get("warning"):
        return _redirect_error(t("'{name}' saved.", name=name) + " " + check["message"])
    return _redirect_info(t("'{name}' saved.", name=name) + " " + check["message"])

@router.post("/public-ip/refresh")
async def refresh_public_ip(request: Request):
    if not await panel_user(request):
        return forbidden()
    ips = await connections.public_ips(force=True)
    if settings.OUTBOUND_PROXY:
        await connections.public_ips(force=True, proxy=settings.OUTBOUND_PROXY)
    found = ", ".join(ip for ip in ips.values() if ip)
    audit_web(request, "public_ip_refresh", "ok" if found else "error", result=found)
    if found:
        return _redirect_info(t("Public IP address: {ips}", ips=found))
    return _redirect_error(t("The public IP address could not be determined."))

@router.post("/servers/{name}/test")
async def test_server(name: str, request: Request):
    if not await panel_user(request):
        return forbidden()
    cfg = storage.load_servers().get(name)
    if cfg is None:
        return _redirect_error(t("'{name}' not found.", name=name))
    check = await connections.check_server(cfg)
    audit_web(request, "server_test", "ok" if check["ok"] else "error", server=name,
              via=check.get("via"), result=check["message"])
    distro = await connections.detect_server_distro(cfg) if check["ok"] else None
    servers = storage.load_servers()
    if name in servers:
        servers[name]["last_check"] = check
        if distro:
            servers[name]["distro"] = distro
        storage.save_servers(servers)
    if check["ok"] and not check.get("warning"):
        return _redirect_info(f"'{name}': {check['message']}")
    return _redirect_error(f"'{name}': {check['message']}")

@router.post("/servers/{name}/forget-host-key")
async def forget_host_key(name: str, request: Request):
    if not await panel_user(request):
        return forbidden()
    servers = storage.load_servers()
    if name not in servers:
        return _redirect_error(t("'{name}' not found.", name=name))
    servers[name].pop("ssh_host_key", None)
    storage.save_servers(servers)
    audit_web(request, "ssh_host_key_forget", server=name)
    return _redirect_info(t("Host key of '{name}' forgotten; the next SSH connection stores the new key.", name=name))

@router.post("/servers/{name}/delete")
async def delete_server(name: str, request: Request):
    if not await panel_user(request):
        return forbidden()
    servers = storage.load_servers()
    removed = servers.pop(name, None)
    storage.save_servers(servers)
    audit_web(request, "server_delete", "ok" if removed else "error", server=name,
              result=None if removed else t("Server not found."))
    return RedirectResponse(url="/", status_code=303)

def shared_key_row(entry: Dict[str, Any]) -> str:
    name = html.escape(entry["name"])
    return (
        f"<tr><td><b>{name}</b><br><span class='note'>{html.escape(entry.get('created', ''))}</span></td>"
        f"<td>{html.escape(entry.get('type', ''))}<br><span class='note'>{html.escape(entry.get('fingerprint', ''))}</span></td>"
        f"<td><textarea class='key form-control' rows='3' readonly onclick='this.select()'>{html.escape(entry.get('public', ''))}</textarea></td>"
        f"<td><form method='post' action='/ssh-keys/{name}/delete' "
        f"onsubmit=\"return confirm('{t('Delete key {name}?', name=name)}')\">{csrf_field()}<button class='btn btn-sm btn-outline-danger'>{t('Delete')}</button></form></td></tr>"
    )

def _key_name(form) -> str | None:
    name = (form.get("name") or "").strip()
    return name if SERVER_NAME_RE.match(name) else None

@router.post("/ssh-keys")
async def add_shared_key(request: Request):
    if not await panel_user(request):
        return forbidden()
    form = await request.form()
    name = _key_name(form)
    if not name:
        return _redirect_error(t("The key name may only contain letters, digits, dots, underscores and hyphens."))
    keys = storage.load_shared_keys()
    if name in keys:
        # Eski açık anahtar sunuculara dağıtılmış olabilir; yanlışlıkla üzerine yazılmasın
        return _redirect_error(t("A key named '{name}' already exists. Delete it first to replace it.", name=name))
    try:
        entry = storage.import_shared_key(name, form.get("private_key") or "", form.get("passphrase") or "")
    except ValueError as e:
        audit_web(request, "ssh_key_add", "error", args={"name": name}, result=str(e))
        return _redirect_error(t("Key not added: {error}", error=e))
    keys[name] = entry
    storage.save_shared_keys(keys)
    audit_web(request, "ssh_key_add", args={"name": name, "fingerprint": entry["fingerprint"]})
    return _redirect_info(t("Key '{name}' added ({fingerprint}).", name=name, fingerprint=entry["fingerprint"]))

@router.post("/ssh-keys/generate")
async def generate_shared_key(request: Request):
    if not await panel_user(request):
        return forbidden()
    form = await request.form()
    name = _key_name(form)
    if not name:
        return _redirect_error(t("The key name may only contain letters, digits, dots, underscores and hyphens."))
    keys = storage.load_shared_keys()
    if name in keys:
        return _redirect_error(t("A key named '{name}' already exists.", name=name))
    key = asyncssh.generate_private_key("ssh-ed25519", comment=f"cockpit-mcp-gateway:{name}")
    keys[name] = storage.shared_key_entry(name, key)
    storage.save_shared_keys(keys)
    audit_web(request, "ssh_key_generate", args={"name": name, "fingerprint": keys[name]["fingerprint"]})
    return _redirect_info(t("Key '{name}' generated. Add the public key to the authorized_keys file on the servers.", name=name))

@router.post("/ssh-keys/{name}/delete")
async def delete_shared_key(name: str, request: Request):
    if not await panel_user(request):
        return forbidden()
    keys = storage.load_shared_keys()
    removed = keys.pop(name, None)
    storage.save_shared_keys(keys)
    audit_web(request, "ssh_key_delete", "ok" if removed else "error",
              args={"name": name, "fingerprint": (removed or {}).get("fingerprint")})
    return _redirect_info(t("Key '{name}' deleted.", name=name))

# --- İşlem kayıtları ---
LOGS_PER_PAGE = 100
LOG_STATUS = {"ok": ("✓", "ok"), "error": (N("✗ error"), "err"), "preview": (N("awaiting confirmation"), "warn")}

def log_row(entry: Dict[str, Any]) -> str:
    e = lambda v: html.escape(str(v)) if v not in (None, "") else "—"
    mark, css = LOG_STATUS.get(entry.get("status"), (entry.get("status"), "note"))
    details = []
    if entry.get("args"):
        details.append(f"<b>{t('Parameters')}</b><pre>" + html.escape(json.dumps(entry["args"], ensure_ascii=False, indent=2)) + "</pre>")
    if entry.get("commands"):
        lines = "\n".join(
            f"[{c.get('via')} · {c.get('user')}{' · root' if c.get('as_root') else ''} · {t('exit')} {c.get('exit_status')}] {c.get('command')}"
            for c in entry["commands"]
        )
        details.append(f"<b>{t('Commands run')}</b><pre>" + html.escape(lines) + "</pre>")
    if entry.get("result"):
        details.append(f"<b>{t('Result')}</b><pre>" + html.escape(str(entry["result"])) + "</pre>")
    if entry.get("commands"):
        summary = t("{count} command(s)", count=len(entry["commands"]))
    else:
        summary = e(entry["via"]) if entry.get("via") else t("Details")
    if entry.get("duration_ms") is not None:
        summary += f" · {entry['duration_ms']} ms"
    detail_html = f"<details><summary>{summary}</summary>{''.join(details)}</details>" if details else "—"
    return (
        f"<tr><td>{e(entry.get('at'))}</td>"
        f"<td>{e(entry.get('user'))}<br><span class='note'>{e(entry.get('ip'))}</span></td>"
        f"<td>{e(entry.get('source'))}</td><td><b>{e(entry.get('action'))}</b></td>"
        f"<td>{e(entry.get('server'))}</td><td><span class='{css}'>{e(mark and t(mark))}</span></td><td>{detail_html}</td></tr>"
    )

@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, q: str = "", source: str = "", status: str = "", user: str = "",
                    server: str = "", page: int = 1):
    username = current_user(request)
    if not username:
        return forbidden()
    page = max(page, 1)
    entries, has_more = audit_log.read(LOGS_PER_PAGE, (page - 1) * LOGS_PER_PAGE, q=q, source=source,
                                       status=status, user=user.strip(), server=server.strip())
    rows = "".join(log_row(entry) for entry in entries) or f"<tr><td colspan='7'>{t('No entries.')}</td></tr>"
    filters = {"q": q, "source": source, "status": status, "user": user, "server": server}

    def options(selected: str, choices: Dict[str, str]) -> str:
        return "".join(f"<option value='{v}'{' selected' if v == selected else ''}>{html.escape(label)}</option>"
                       for v, label in choices.items())

    def page_link(number: int, label: str) -> str:
        query = urlencode({**{k: v for k, v in filters.items() if v}, "page": number})
        return f"<a class='btn btn-sm btn-outline-secondary' href='/logs?{query}'>{label}</a>"

    nav = " ".join(filter(None, [
        page_link(page - 1, t("← Newer")) if page > 1 else "",
        f"<span class='mx-2'>{t('Page {page}', page=page)}</span>",
        page_link(page + 1, t("Older →")) if has_more else "",
    ]))
    logs_note = t(
        "MCP tool calls, panel actions and logins. The newest entry is on top. "
        "Entries are kept in <code>{file}</code>; passwords and keys are not recorded.",
        file=html.escape(audit_log.LOG_FILE))
    return render_page(t("Audit log"), f"""
        <h2 class="h4">{t("Audit log")}</h2>
        <p class="note">{logs_note}</p>
        <form class="row g-2 align-items-center mb-3" method="get" action="/logs">
          <div class="col-md"><input class="form-control form-control-sm" name="q" value="{html.escape(q)}" placeholder="{t("Search (command, tool, output…)")}"></div>
          <div class="col-6 col-md-2"><input class="form-control form-control-sm" name="user" value="{html.escape(user)}" placeholder="{t("User")}"></div>
          <div class="col-6 col-md-2"><input class="form-control form-control-sm" name="server" value="{html.escape(server)}" placeholder="{t("Server")}"></div>
          <div class="col-6 col-md-auto"><select class="form-select form-select-sm" name="source">{options(source, {"": t("All sources"), "mcp": "MCP", "web": "Panel"})}</select></div>
          <div class="col-6 col-md-auto"><select class="form-select form-select-sm" name="status">{options(status, {"": t("All statuses"), "ok": t("Success"), "error": t("Error"), "preview": t("Awaiting confirmation")})}</select></div>
          <div class="col-auto"><button type="submit" class="btn btn-sm btn-primary">{t("Filter")}</button>
            <a class="btn btn-sm btn-link" href="/logs">{t("Clear")}</a></div>
        </form>
        <div class="table-responsive"><table class="logs table table-sm table-bordered table-hover align-middle bg-body">
          <tr><th>{t("Time")}</th><th>{t("User")}</th><th>{t("Source")}</th><th>{t("Action")}</th><th>{t("Server")}</th><th>{t("Status")}</th><th>{t("Details")}</th></tr>
          {rows}
        </table></div>
        <p>{nav}</p>
    """, user=username, active="logs")

def login_page(action: str, title: str, error: str = "", note: str = "", hidden: Dict[str, str] | None = None,
               status_code: int = 200, ask_token: bool = False) -> HTMLResponse:
    hidden_inputs = "".join(
        f"<input type='hidden' name='{html.escape(k)}' value='{html.escape(v)}'>" for k, v in (hidden or {}).items()
    )
    token_field = (
        f'<div class="mb-3"><label class="form-label">{t("Gateway password")}</label>'
        '<input class="form-control" name="token" type="password" autocomplete="off" required></div>'
    ) if ask_token else ""
    return render_page(title, f"""
        <div class="card mx-auto shadow-sm" style="max-width: 420px"><div class="card-body">
        <h2 class="h5 mb-3">{html.escape(title)}</h2>
        <p class="note">{note}{t("Log in with your Cockpit username and password ({url}).", url=html.escape(settings.COCKPIT_AUTH_URL))}</p>
        {f"<div class='alert alert-danger py-2'>{html.escape(error)}</div>" if error else ""}
        <form method="post" action="{html.escape(action)}">
          {hidden_inputs}
          <div class="mb-3"><label class="form-label">{t("Username")}</label>
            <input class="form-control" name="username" autocomplete="username" required autofocus></div>
          <div class="mb-3"><label class="form-label">{t("Cockpit password")}</label>
            <input class="form-control" name="password" type="password" autocomplete="current-password" required></div>
          {token_field}
          <button type="submit" class="btn btn-primary w-100">{t("Log in")}</button>
        </form>
        </div></div>
    """, status_code)

def form_text(form, key: str) -> str:
    """Formdaki metin alanı; dosya olarak gönderilen ya da eksik alan boş sayılır."""
    value = form.get(key)
    return value if isinstance(value, str) else ""

def _token_fields(url_token: str | None) -> tuple[bool, Dict[str, str]]:
    """URL'de geçerli token varsa formda sorma, gizli alanla taşı."""
    if access.login_token_ok(url_token):
        return False, ({"token": url_token} if url_token else {})
    return True, {}

@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, token: str | None = None):
    ask_token, hidden = _token_fields(token)
    return login_page("/login", t("Cockpit MCP Gateway - Login"), hidden=hidden, ask_token=ask_token)

@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = form_text(form, "username").strip()
    error, status = await access.verify_login(request, username, form_text(form, "password"), access.login_token_ok(form.get("token")))
    if error:
        audit_web(request, "login", "error", user=username, result=error)
        return login_page("/login", t("Cockpit MCP Gateway - Login"), error=error, status_code=status, ask_token=bool(settings.MCP_API_KEY))
    start_panel_session(request, username)
    audit_web(request, "login")
    return RedirectResponse(url='/', status_code=303)

# --- MCP istemcileri için OAuth girişi (/authorize buraya yönlendirir) ---
@router.get("/oauth/login", response_class=HTMLResponse)
async def oauth_login_form(request_id: str = Query(alias="request")):
    client_name = access.oauth_provider.pending_client_name(request_id)
    if client_name is None:
        return invalid_login_request()
    return login_page(
        "/oauth/login", t("Grant Access to MCP Client"),
        note=t("<b>{client}</b> wants to access the tools on this gateway.", client=html.escape(client_name)) + " ",
        hidden={"request": request_id},
        ask_token=not access.login_token_ok(access.token_from_url(access.oauth_provider.pending_resource(request_id))),
    )

@router.post("/oauth/login")
async def oauth_login_submit(request: Request):
    form = await request.form()
    request_id = form_text(form, "request")
    client_name = access.oauth_provider.pending_client_name(request_id)
    if client_name is None:
        return invalid_login_request()
    username = form_text(form, "username").strip()
    # Token: istemcinin bağlandığı URL'den (?token=) ya da formdan
    url_token = access.token_from_url(access.oauth_provider.pending_resource(request_id))
    token_ok = access.login_token_ok(url_token) or access.login_token_ok(form.get("token"))
    error, status = await access.verify_login(request, username, form_text(form, "password"), token_ok)
    if error:
        audit_web(request, "oauth_login", "error", user=username, args={"client": client_name}, result=error)
        return login_page(
            "/oauth/login", t("Grant Access to MCP Client"), error=error,
            note=t("<b>{client}</b> wants to access the tools on this gateway.", client=html.escape(client_name)) + " ",
            hidden={"request": request_id}, status_code=status, ask_token=not access.login_token_ok(url_token),
        )
    redirect = access.oauth_provider.complete_authorization(request_id, username)
    audit_web(request, "oauth_login", user=username, args={"client": client_name})
    return RedirectResponse(url=redirect, status_code=302)

@router.post("/logout")
async def logout(request: Request):
    if current_user(request):
        if not await panel_user(request):
            return forbidden()
        audit_web(request, "logout")
    panel_sessions.pop(request.session.get("sid"), None)
    request.session.clear()
    return RedirectResponse(url='/', status_code=303)
