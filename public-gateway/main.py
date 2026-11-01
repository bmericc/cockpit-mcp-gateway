"""Uygulamayı birleştirir: ara katmanlar, yönlendirmeler ve başlangıç işleri."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Mount, Route

import access
import audit_log
import auth
import connections
import i18n
import mcp_gateway
import security
import settings
import storage
import web_panel

@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not access.authenticator.allowed_users:
        raise RuntimeError("ALLOWED_USERS is empty: set the Cockpit users allowed into the gateway.")
    logging.getLogger("uvicorn.access").addFilter(security.RedactTokenFilter())
    security.tighten_permissions(settings.SERVERS_FILE, settings.SSH_KEYS_FILE, settings.OAUTH_STORE_FILE, audit_log.LOG_FILE)
    storage.migrate_legacy_encryption()
    # Arka planda çalışır: ulaşılamayan sunucular gateway'in açılmasını bekletmesin
    migration = asyncio.create_task(connections.migrate_servers())
    async with mcp_gateway.streamable_http_lifespan():
        yield
    migration.cancel()
    await asyncio.gather(migration, return_exceptions=True)

app = FastAPI(lifespan=lifespan, version=settings.VERSION)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_KEY,
    max_age=settings.SESSION_TTL,
    https_only=settings.PUBLIC_URL.startswith("https://"),
    same_site="lax",
)
# Interface language from the browser's Accept-Language header (English by default)
app.add_middleware(i18n.LanguageMiddleware)
app.add_middleware(security.SecurityHeadersMiddleware)
app.add_middleware(mcp_gateway.RegisterGuardMiddleware)

app.include_router(web_panel.router)

app.routes.append(Route("/mcp", endpoint=mcp_gateway.StreamableHTTPEndpoint()))
app.routes.append(Route("/sse", endpoint=mcp_gateway.handle_sse))
app.routes.append(Mount("/messages/", app=mcp_gateway.sse.handle_post_message))

# OAuth 2.1 yetkilendirme sunucusu (/authorize, /token, /register, /revoke) ve metadata
app.routes.extend(create_auth_routes(
    access.oauth_provider,
    issuer_url=AnyHttpUrl(settings.PUBLIC_URL),
    client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[auth.SCOPE], default_scopes=[auth.SCOPE]),
    revocation_options=RevocationOptions(enabled=True),
))

for transport_path in mcp_gateway.MCP_TRANSPORT_PATHS:
    app.routes.append(Route(mcp_gateway.METADATA_PATH + transport_path, endpoint=mcp_gateway.protected_resource_metadata,
                            methods=["GET", "OPTIONS"]))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 7435))
    # X-Forwarded-* yalnızca TRUSTED_PROXIES'ten kabul edilir; uvicorn sağdan ilk güvenilmeyen adresi alır.
    # "*" verilirse en soldaki (istemcinin yazdığı) değer alındığından audit IP'si sahtelenebilir.
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False, proxy_headers=True,
                forwarded_allow_ips=settings.TRUSTED_PROXIES)
