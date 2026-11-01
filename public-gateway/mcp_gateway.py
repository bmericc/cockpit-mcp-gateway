"""MCP sunucusu, araç çağrıları ve taşımalar (Streamable HTTP, HTTP+SSE)."""
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Dict, Any
from urllib.parse import urlencode

import anyio
import mcp.types as types
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

import access
import audit_log
import auth
import cockpit_client
import connections
import fleet_tools
import security
import settings
import storage
from i18n import t

def text_result(value: Any) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=fleet_tools.to_text(value))]

def tool_allowed(spec: fleet_tools.ToolSpec | None) -> bool:
    """spec None: run_remote_command. Salt okunur modda yalnızca okuma araçları çalışır."""
    return not settings.READ_ONLY or (spec is not None and spec.read_only)

# --- MCP Sunucu Tanımları ---
mcp_server = Server("cockpit-mcp-gateway", version=settings.VERSION)

def server_name_prop() -> dict:
    return {"server_name": {"type": "string", "description": t("Registered server name (e.g. prod-db)")}}

@mcp_server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    tools = [
        types.Tool(
            name="list_servers",
            description=t("Lists all registered servers (without passwords) and whether a Cockpit login is configured."),
            inputSchema={"type": "object", "properties": {}},
            annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
        ),
        types.Tool(
            name="fleet_health",
            description=t("Collects a load, failed-service and root-disk-usage summary from all registered servers in parallel."),
            inputSchema={"type": "object", "properties": {}},
            annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=False),
        ),
        *[spec.to_tool() for spec in fleet_tools.TOOLS if tool_allowed(spec)],
    ]
    if not tool_allowed(None):
        return tools
    return [
        *tools,
        types.Tool(
            name="run_remote_command",
            description=t(
                "Runs an arbitrary Linux command on a registered server. Prefer a purpose-built tool when one exists. "
                "Runs through Cockpit if the server has a Cockpit login configured; otherwise, or if Cockpit is unreachable, over SSH."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **server_name_prop(),
                    "command": {"type": "string", "description": t("Linux command to run (e.g. systemctl status nginx)")},
                    "as_root": {"type": "boolean", "description": t("Run with administrator privileges (Cockpit superuser / sudo -n over SSH)")},
                    "confirm": {"type": "boolean", "description": t("Set to true to actually run the command. Otherwise only what would be run is shown.")},
                },
                "required": ["server_name", "command"]
            },
            annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False),
        ),
    ]

def confirmation_required(server_name: str, action: str) -> list[types.TextContent]:
    return text_result(t(
        "Confirmation required: the following will be done on '{server}':\n  {action}\n"
        "Call the same tool again with confirm: true to apply.",
        server=server_name, action=action,
    ))

async def fleet_health(servers: Dict[str, Dict[str, Any]]) -> dict:
    async def one(name, cfg):
        try:
            async with connections.connect_server(name, cfg) as (run, info, _):
                return {**await fleet_tools.fleet_health_for(run), "connection": info}
        except Exception as e:
            return {"error": str(e)}

    names = list(servers)
    results = await asyncio.gather(*(one(n, servers[n]) for n in names))
    return dict(zip(names, results))

@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Aracı çalıştırır ve çağrıyı (kim, hangi sunucu, hangi komutlar, sonuç) işlem kaydına yazar."""
    arguments = arguments or {}
    commands: list = []
    token = connections._command_log.set(commands)
    started = time.monotonic()
    status, content, error = "error", None, None
    try:
        content, status = await _call_tool(name, arguments)
        return content
    except Exception as e:
        error = str(e) or type(e).__name__
        raise
    finally:
        connections._command_log.reset(token)
        server_name = arguments.get("server_name")
        audit_log.record(
            "mcp", name, status, **connections._mcp_actor.get(),
            server=server_name if isinstance(server_name, str) else None,
            args={k: v for k, v in arguments.items() if k != "server_name"},
            commands=commands,
            result=error or (content[0].text if content else None),
            duration_ms=round((time.monotonic() - started) * 1000),
        )

async def _call_tool(name: str, arguments: dict) -> tuple[list[types.TextContent], str]:
    """(çıktı, durum) döner; durum: "ok" | "error" | "preview" (onay bekliyor)."""
    servers = storage.load_servers()

    if name == "list_servers":
        return text_result({n: connections.public_server(cfg) for n, cfg in servers.items()}), "ok"

    if name == "fleet_health":
        return text_result(await fleet_health(servers)), "ok"

    spec = fleet_tools.TOOLS_BY_NAME.get(name)
    if spec is None and name != "run_remote_command":
        raise ValueError(t("Unknown tool: {name}", name=name))

    if not tool_allowed(spec):
        return text_result(t("The gateway is in read-only mode; {name} is disabled.", name=name)), "error"

    server_name = arguments.get("server_name")
    if server_name not in servers:
        return text_result(t("Error: server '{name}' not found.", name=server_name)), "error"
    cfg = servers[server_name]

    try:
        if name == "run_remote_command":
            command = arguments.get("command")
            if not arguments.get("confirm"):
                return confirmation_required(server_name, command), "preview"
            async with connections.connect_server(server_name, cfg) as (run, info, _):
                result = await run(command, privileged=bool(arguments.get("as_root")))
            output = f"User: {result.user}\nVia: {result.via}\nExit Status: {result.exit_status}\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}"
            if info.get("cockpit_error"):
                output = t("Note: Cockpit was unavailable, ran over SSH ({error})", error=info["cockpit_error"]) + "\n" + output
            return text_result(output), "ok"

        if not spec.read_only and not arguments.get("confirm"):
            return confirmation_required(server_name, spec.describe(arguments, cfg.get("distro"))), "preview"

        async with connections.connect_server(server_name, cfg) as (run, info, distro):
            value = await spec.execute(run, arguments, distro)
        if isinstance(value, dict):
            value["connection"] = info
        return text_result(value), "ok"
    except (connections.SSHError, cockpit_client.CockpitError) as e:
        return text_result(str(e)), "error"
    except fleet_tools.ToolInputError as e:
        raise ValueError(str(e)) from e

# --- MCP SSE Transport Entegrasyonu ---
# Sonda "/" olmalı: aksi halde her POST /messages isteği 307 ile /messages/'e yönlenir
sse = SseServerTransport("/messages/")

# Açık SSE bağlantısının kimliği bu aralıkla yeniden doğrulanır
SSE_RECHECK_SECONDS = 30.0

class StreamEnded(Response):
    """SSE yanıtı zaten gönderildi; Starlette'in beklediği yanıt nesnesi hiçbir şey yazmaz."""

    async def __call__(self, scope, receive, send) -> None:
        return None

MCP_RESOURCE_URL = f"{settings.PUBLIC_URL}/sse"
METADATA_PATH = "/.well-known/oauth-protected-resource"
# Taşıma yolu: /mcp (Streamable HTTP, önerilen), /sse (HTTP+SSE, MCP spec'inde deprecated)
MCP_TRANSPORT_PATHS = ("/mcp", "/sse")

def unauthorized_response(request: Request, path: str) -> JSONResponse:
    if request.headers.get("authorization"):
        # Kimlik bilgisi gönderilmiş ama reddedilmiş (başlıksız ilk keşif isteği kaydedilmez)
        audit_log.record("mcp", "connect", "error", **access.request_actor(request), result=t("Authentication rejected."))
    # URL'deki geçerli token metadata adresine taşınır; istemci bunu OAuth isteğindeki
    # "resource" alanında geri gönderir ve giriş sayfası token'ı ayrıca sormaz.
    metadata_url = f"{settings.PUBLIC_URL}{METADATA_PATH}{path}"
    url_token = request.query_params.get("token")
    if settings.MCP_API_KEY and access.login_token_ok(url_token):
        metadata_url += "?" + urlencode({"token": url_token})
    return JSONResponse(
        {"error": "invalid_token", "error_description": t("Login with a Cockpit account is required")},
        status_code=401,
        headers={"WWW-Authenticate": f'Bearer error="invalid_token", resource_metadata="{metadata_url}"'},
    )

async def serve_until_unauthorized(request: Request, actor: Dict[str, Any], streams) -> None:
    """MCP oturumunu çalıştırır; token süresi dolunca ya da kullanıcı listeden çıkınca bağlantıyı keser."""
    async with anyio.create_task_group() as tasks:
        async def watch():
            while True:
                await anyio.sleep(SSE_RECHECK_SECONDS)
                if await access.authenticate_mcp(request) is None:
                    audit_log.record("mcp", "disconnect", "error", **actor, result=t("Authentication rejected."))
                    tasks.cancel_scope.cancel()
                    return

        tasks.start_soon(watch)
        await mcp_server.run(streams[0], streams[1], mcp_server.create_initialization_options())
        tasks.cancel_scope.cancel()

async def handle_sse(request: Request):
    # /messages/ istekleri tahmin edilemez session_id ile korunur; kimlik doğrulama /sse'de yapılır.
    username = await access.authenticate_mcp(request)
    if username is None:
        return unauthorized_response(request, "/sse")

    actor = {"user": username, **access.request_actor(request)}
    audit_log.record("mcp", "connect", **actor, args={"user_agent": request.headers.get("user-agent")})
    # Araç çağrıları bu görevin bağlamını devralır; kayıtlarda kullanıcı ve IP görünür
    connections._mcp_actor.set(actor)
    async with sse.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await serve_until_unauthorized(request, actor, streams)
    # Bağlantı kapandığında Starlette bir Response bekler; None dönerse TypeError fırlar
    return StreamEnded()

# FastAPI route'larına MCP SSE ekleme
# Streamable HTTP. Durumsuz çalışır: her istek ayrı doğrulanır, böylece token süresi dolunca ya da
# kullanıcı listeden çıkınca erişim bir sonraki istekte biter; sunucuda oturum tutulmaz.
_streamable_manager: StreamableHTTPSessionManager | None = None

@asynccontextmanager
async def streamable_http_lifespan():
    global _streamable_manager
    # Yönetici örneği yalnızca bir kez çalıştırılabilir
    _streamable_manager = StreamableHTTPSessionManager(mcp_server, json_response=True, stateless=True)
    async with _streamable_manager.run():
        yield
    _streamable_manager = None

class StreamableHTTPEndpoint:
    async def __call__(self, scope, receive, send):
        request = Request(scope, receive)
        username = await access.authenticate_mcp(request)
        if username is None:
            return await unauthorized_response(request, "/mcp")(scope, receive, send)
        if request.method != "POST":
            # Sunucudan istemciye bildirim gönderilmiyor; durumsuz modda açık GET akışı ve oturum yok
            return await Response(status_code=405, headers={"Allow": "POST"})(scope, receive, send)
        # Araç çağrıları bu görevin bağlamını devralır; kayıtlarda kullanıcı ve IP görünür
        connections._mcp_actor.set({"user": username, **access.request_actor(request)})
        await _streamable_manager.handle_request(scope, receive, send)

class RegisterGuardMiddleware:
    """/register kimlik doğrulamasızdır: IP başına sınır ve gövde boyutu sınırı uygular."""

    def __init__(self, inner):
        self.app = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/register" and scope["method"] == "POST":
            request = Request(scope)
            ip = access.client_ip(request) or ""
            try:
                size = int(request.headers.get("content-length", ""))
            except ValueError:
                size = MAX_REGISTER_BODY + 1
            if size > MAX_REGISTER_BODY:
                return await JSONResponse({"error": "invalid_client_metadata"}, status_code=413)(scope, receive, send)
            if register_limiter.blocked(ip):
                return await JSONResponse({"error": "too_many_requests"}, status_code=429,
                                          headers={"Retry-After": "3600"})(scope, receive, send)
            register_limiter.hit(ip)
        await self.app(scope, receive, send)

MAX_REGISTER_BODY = 8 * 1024
register_limiter = security.RateLimiter(20, 3600)

async def protected_resource_metadata(request: Request):
    """RFC 9728 metadata. Geçerli ?token= varsa resource adresine eklenir (bkz. unauthorized_response)."""
    cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS"}
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=cors)
    resource = settings.PUBLIC_URL + request.url.path.removeprefix(METADATA_PATH)
    url_token = request.query_params.get("token")
    if settings.MCP_API_KEY and access.login_token_ok(url_token):
        resource += "?" + urlencode({"token": url_token})
    return JSONResponse({
        "resource": resource,
        "authorization_servers": [settings.PUBLIC_URL + "/"],
        "scopes_supported": [auth.SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Cockpit MCP Gateway",
    }, headers=cors)
