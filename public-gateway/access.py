"""Kim girebilir: Cockpit doğrulaması, OAuth sağlayıcısı, erişim token'ı ve giriş sınırı."""
import secrets
from typing import Dict, Any
from urllib.parse import parse_qs, urlsplit

from fastapi import Request

import auth
import security
import settings
from i18n import N, t

authenticator = auth.CockpitAuthenticator(settings.COCKPIT_AUTH_URL, settings.COCKPIT_AUTH_VERIFY_TLS, settings.ALLOWED_USERS)

def login_token_ok(value: Any) -> bool:
    # MCP_API_KEY yalnızca testlerde boş olur; çalışan gateway'de load_or_create_secret doldurur
    if not settings.MCP_API_KEY:
        return True
    return isinstance(value, str) and secrets.compare_digest(value.encode(), settings.MCP_API_KEY.encode())

def token_from_url(url: str | None) -> str | None:
    """URL'deki ?token= değerini döner (RFC 8707 resource veya istek URL'si)."""
    if not url:
        return None
    values = parse_qs(urlsplit(url).query).get("token")
    return values[0] if values else None

oauth_provider = auth.CockpitOAuthProvider(settings.OAUTH_STORE_FILE, f"{settings.PUBLIC_URL}/oauth/login", token_policy=settings.MCP_API_KEY,
                                           is_allowed=authenticator.is_allowed)

def client_ip(request: Request) -> str | None:
    """Bağlanan adres; TRUSTED_PROXIES'ten geliyorsa uvicorn X-Forwarded-For'dan doldurur."""
    return request.client.host if request.client else None

def request_actor(request: Request) -> Dict[str, Any]:
    return {"ip": client_ip(request)}

TOKEN_ERROR = N("Wrong gateway password.")
TOO_MANY_ATTEMPTS = N("Too many failed attempts. Try again in a few minutes.")
# Giriş denemeleri IP başına sınırlıdır; token olmadan Cockpit şifresi hiç sınanmaz
LOGIN_FAILURES_ALLOWED = 10
login_limiter = security.RateLimiter(LOGIN_FAILURES_ALLOWED, 300)

async def verify_login(request: Request, username: str, password: str, token_ok: bool) -> tuple[str | None, int]:
    """(hata, HTTP kodu); başarılıysa hata None'dır."""
    ip = client_ip(request) or ""
    if login_limiter.blocked(ip):
        return t(TOO_MANY_ATTEMPTS), 429
    # Token Cockpit'ten önce kontrol edilir: token'sız denemeler şifreyi hiç sınayamaz
    error = t(TOKEN_ERROR) if not token_ok else await authenticator.login(username, password)
    if error:
        login_limiter.hit(ip)
        return error, 401
    login_limiter.reset(ip)
    return None, 200

async def authenticate_mcp(request: Request) -> str | None:
    """MCP isteğini doğrular; başarılıysa kullanıcı adını döner.

    Kabul edilenler:
    - OAuth erişim token'ı (Bearer): giriş sırasında Cockpit + erişim token'ı zaten doğrulandı.
    - Cockpit kullanıcı adı/şifresi (Basic): MCP_API_KEY tanımlıysa ayrıca X-MCP-Token başlığı
      veya ?token= ile erişim token'ı da gerekir.
    Erişim token'ı tek başına giriş sağlamaz.
    """
    header = request.headers.get("authorization", "")
    if header[:7].lower() == "bearer ":
        access = await oauth_provider.load_access_token(header[7:].strip())
        if access and access.subject and authenticator.is_allowed(access.subject):
            return access.subject
        return None
    if header[:6].lower() == "basic ":
        ip = client_ip(request) or ""
        if login_limiter.blocked(ip):
            return None
        token = request.headers.get("x-mcp-token") or request.query_params.get("token")
        username = await authenticator.check_basic(header) if login_token_ok(token) else None
        if username is None:
            login_limiter.hit(ip)
        return username
    return None
