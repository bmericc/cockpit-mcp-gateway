"""Gizli değerler, özel dosyalar, oran sınırı ve HTTP güvenlik katmanları."""
import json
import logging
import os
import re
import secrets
import sys
import time
from typing import Any

from starlette.datastructures import MutableHeaders

# Boş ya da örnek değerler güvenli sayılmaz: rastgele değer üretilir
SECRET_PLACEHOLDERS = ("gizli-anahtar", "a_very_secure_and_random_string", "xxx-long-random-token")

# form-action bilerek yok: /oauth/login, MCP istemcisinin adresine yönlendirir ve tarayıcılar
# form yönlendirmesini de form-action ile sınırlar. Panelde satır içi stil ve olay nitelikleri var.
HTML_CSP = ("default-src 'none'; script-src https://cdn.jsdelivr.net 'unsafe-inline'; "
            "style-src https://cdn.jsdelivr.net 'unsafe-inline'; img-src data:; "
            "base-uri 'none'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # URL'deki ?token= değeri CDN isteklerinin Referer başlığıyla dışarı çıkmasın
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": HTML_CSP,
}

_URL_TOKEN = re.compile(r"([?&]token=)[^&\s]*")


def write_private(path: str, text: str) -> None:
    """Dosyayı atomik yazar; yazım yarıda kalırsa eski içerik korunur, izin her zaman 600'dür."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_private_json(path: str, data: Any, indent: int | None = None) -> None:
    write_private(path, json.dumps(data, indent=indent))


def tighten_permissions(*paths: str) -> None:
    """Eski sürümlerden kalan dosyaları 600 yapar."""
    for path in paths:
        try:
            if os.path.exists(path):
                os.chmod(path, 0o600)
        except OSError as e:
            print(f"could not restrict permissions of {path}: {e}", file=sys.stderr)


def load_or_create_secret(name: str, path: str) -> str:
    """Ortam değişkenini döner; boş ya da örnek değerse dosyadaki değeri, o da yoksa yeni rastgele değer.

    Değer kalıcıdır: yeniden başlayınca değişseydi şifreli veriler açılamaz, oturumlar düşerdi.
    Değerin kendisi loglara yazılmaz, yalnızca dosyanın yolu bildirilir.
    """
    value = os.getenv(name, "").strip()
    if value and value not in SECRET_PLACEHOLDERS:
        return value
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            stored = f.read().strip()
        if stored:
            return stored
    value = secrets.token_urlsafe(48)
    write_private(path, value)
    print(f"{name} is not set: generated a random value in {path}", file=sys.stderr)
    return value


class RateLimiter:
    """Anahtar başına (ör. IP) kayan pencerede başarısız deneme sayacı. Bellekte tutulur."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._hits: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = time.monotonic() - self.window
        hits = [at for at in self._hits.get(key, []) if at > cutoff]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.limit

    def hit(self, key: str) -> None:
        self._hits.setdefault(key, []).append(time.monotonic())
        # Çok sayıda farklı IP belleği şişirmesin
        if len(self._hits) > 10000:
            for stale in list(self._hits):
                self._recent(stale)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def clear(self) -> None:
        self._hits.clear()


class SecurityHeadersMiddleware:
    """Saf ASGI: BaseHTTPMiddleware, SSE akışını tamponlayabilir."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RedactTokenFilter(logging.Filter):
    """Erişim loglarında URL'deki ?token= değerini gizler."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_URL_TOKEN.sub(r"\1[redacted]", a) if isinstance(a, str) else a for a in record.args)
        return True
