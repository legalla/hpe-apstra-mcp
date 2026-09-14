"""Shared MCP server core: FastMCP instance, security/auth and the Apstra client accessor."""

import functools
import json
import logging
import os
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import apstra_auth
from apstra_client import ApstraClient

load_dotenv()

logging.basicConfig(level=os.environ.get("APSTRA_LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("hpe-apstra-mcp")

# ── Initialization ────────────────────────────────────────────────────

_host = os.environ.get("MCP_HOST", "0.0.0.0")
_port = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("hpe-apstra-mcp", host=_host, port=_port)


# ── Security: optional Bearer authentication ──────────────────────────
# Disabled by default (backward-compatible). Enable via APSTRA_AUTH_ENABLED=true
# (typically in docker-compose.yml) after creating at least one token with
# `apstra_token_manager.py generate --name <client>`.


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


_AUTH_ENABLED = _env_bool("APSTRA_AUTH_ENABLED", False)
_TOKENS_FILE = os.environ.get("APSTRA_TOKENS_FILE", apstra_auth.DEFAULT_TOKENS_FILE)
_MCP_PATH = os.environ.get("APSTRA_MCP_PATH", "/mcp")
_TRUST_FORWARDED = _env_bool("APSTRA_TRUST_FORWARDED_FOR", False)

# Read-only by default: mutating tools are blocked unless APSTRA_WRITE_ENABLED=true.
_WRITE_ENABLED = _env_bool("APSTRA_WRITE_ENABLED", False)
if _WRITE_ENABLED:
    logger.warning(
        "\u270f\ufe0f Write actions ENABLED (APSTRA_WRITE_ENABLED=true) — mutating tools "
        "can create/update/delete objects and commit/rollback blueprints."
    )
else:
    logger.info(
        "\U0001F512 Write actions DISABLED (read-only mode). Set APSTRA_WRITE_ENABLED=true "
        "to allow mutating tools (create/update/delete, commit, rollback, revert)."
    )


def _require_write(func):
    """Decorator: block a mutating tool unless APSTRA_WRITE_ENABLED=true."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not _WRITE_ENABLED:
            raise PermissionError(
                f"Write action '{func.__name__}' is disabled: this server runs in "
                "read-only mode. Set APSTRA_WRITE_ENABLED=true to allow write actions."
            )
        return func(*args, **kwargs)

    return wrapper

# Built lazily at startup (see __main__).
_token_store: "apstra_auth.TokenStore | None" = None


def _init_security() -> None:
    """Build the token store and apply the startup rules.

    Policy: if Bearer auth is enabled but no token exists yet, the server still
    starts but in LOCKED mode — every MCP request is refused (HTTP 503) until a
    token is created. This makes it possible to generate the first token without
    having to disable authentication; a restart then activates it.
    """
    global _token_store

    if not _AUTH_ENABLED:
        logger.warning(
            "🔓 Bearer authentication is DISABLED (APSTRA_AUTH_ENABLED not set). "
            "The MCP endpoint is open to any client that can reach it."
        )
        return

    _token_store = apstra_auth.TokenStore(_TOKENS_FILE)
    if len(_token_store) == 0:
        logger.warning(
            "🔒 APSTRA_AUTH_ENABLED=true but no token found in '%s'. "
            "Starting in LOCKED mode: all MCP requests are refused "
            "(HTTP 503) until a token exists. Create the first one with: "
            "docker compose exec hpe-apstra-mcp python apstra_token_manager.py "
            "generate --name <client> — then RESTART the container.",
            _TOKENS_FILE,
        )
    else:
        logger.info(
            "🔒 Bearer authentication ENABLED — %d token(s) loaded from %s",
            len(_token_store), _TOKENS_FILE,
        )


_apstra_client: ApstraClient | None = None

def _client() -> ApstraClient:
    global _apstra_client
    if _apstra_client is None:
        host       = os.environ["APSTRA_HOST"]
        username   = os.environ["APSTRA_USERNAME"]
        password   = os.environ["APSTRA_PASSWORD"]
        verify_ssl = os.environ.get("APSTRA_VERIFY_SSL", "false").lower() == "true"
        _apstra_client = ApstraClient(host, username, password, verify_ssl)
    return _apstra_client



class _SecurityMiddleware:
    """Optional Bearer authentication for the MCP endpoint (ASGI).

    When authentication is disabled, this is a zero-cost pass-through. Only the
    MCP path (``/mcp`` by default) is protected; any other path is left
    untouched.
    """

    def __init__(self, app, *, auth_enabled, token_store, mcp_path, trust_forwarded):
        self._app = app
        self._auth_enabled = auth_enabled
        self._token_store = token_store
        self._mcp_path = mcp_path
        self._trust_forwarded = trust_forwarded

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self._auth_enabled:
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith(self._mcp_path):
            await self._app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        src_ip = self._client_ip(scope, headers)

        # LOCKED mode: auth required but no token exists yet.
        if self._token_store is None or len(self._token_store) == 0:
            logger.warning("🔒 MCP request refused — server LOCKED (no "
                           "token configured) from %s %s", src_ip, path)
            await self._send_503_locked(send)
            return

        actor = self._resolve_actor(headers)
        if actor is None:
            logger.warning("🚫 Unauthenticated request rejected from %s %s", src_ip, path)
            await self._send_401(send)
            return

        await self._app(scope, receive, send)

    def _resolve_actor(self, headers: dict) -> "str | None":
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[7:].strip()
        return self._token_store.resolve(token)

    def _client_ip(self, scope, headers: dict) -> str:
        if self._trust_forwarded:
            xff = headers.get("x-forwarded-for")
            if xff:
                return xff.split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    @staticmethod
    async def _send_401(send) -> None:
        payload = json.dumps({"error": "Missing or invalid bearer token"}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": payload})

    @staticmethod
    async def _send_503_locked(send) -> None:
        payload = json.dumps({
            "error": "Service locked: authentication is enabled but no token is "
                     "configured. Create the first token with "
                     "`docker compose exec hpe-apstra-mcp python apstra_token_manager.py "
                     "generate --name <client>`, then restart the container.",
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                (b"retry-after", b"0"),
            ],
        })
        await send({"type": "http.response.body", "body": payload})


def run() -> None:
    """Start the MCP server (stdio or streamable-http, per MCP_TRANSPORT)."""
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http").lower()
    _init_security()

    # Bearer authentication only makes sense on the network HTTP transport.
    if _AUTH_ENABLED and transport == "streamable-http":
        import uvicorn

        app = _SecurityMiddleware(
            mcp.streamable_http_app(),
            auth_enabled=_AUTH_ENABLED,
            token_store=_token_store,
            mcp_path=_MCP_PATH,
            trust_forwarded=_TRUST_FORWARDED,
        )
        uvicorn.run(app, host=_host, port=_port)
    else:
        mcp.run(transport=transport)
