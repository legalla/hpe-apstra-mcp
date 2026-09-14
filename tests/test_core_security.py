"""Unit tests for core.py: the write-guard decorator and the optional Bearer
authentication ASGI middleware. No real Apstra client/network involved."""

import pytest

import core


# ── _require_write ──────────────────────────────────────────────────────

def test_require_write_blocks_by_default(monkeypatch):
    monkeypatch.setattr(core, "_WRITE_ENABLED", False)

    @core._require_write
    def mutating_tool(x):
        return x * 2

    with pytest.raises(PermissionError, match="mutating_tool"):
        mutating_tool(3)


def test_require_write_allows_when_enabled(monkeypatch):
    monkeypatch.setattr(core, "_WRITE_ENABLED", True)

    @core._require_write
    def mutating_tool(x):
        return x * 2

    assert mutating_tool(3) == 6


def test_require_write_preserves_function_metadata():
    @core._require_write
    def some_tool():
        """Docstring."""

    assert some_tool.__name__ == "some_tool"
    assert some_tool.__doc__ == "Docstring."


# ── _env_bool ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("True", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False), ("garbage", False),
])
def test_env_bool_parses_common_truthy_values(monkeypatch, value, expected):
    monkeypatch.setenv("SOME_FLAG", value)
    assert core._env_bool("SOME_FLAG") is expected


def test_env_bool_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert core._env_bool("SOME_FLAG", default=True) is True
    assert core._env_bool("SOME_FLAG", default=False) is False


# ── _SecurityMiddleware ───────────────────────────────────────────────────

class _RecordingApp:
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class _FakeTokenStore:
    def __init__(self, tokens=None):
        self._tokens = tokens or {}

    def resolve(self, token):
        return self._tokens.get(token)

    def __len__(self):
        return len(self._tokens)


def _make_scope(path="/mcp", headers=None):
    raw_headers = [
        (k.encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    return {"type": "http", "path": path, "headers": raw_headers, "client": ("1.2.3.4", 5555)}


async def _run(mw, scope):
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(msg):
        sent.append(msg)

    await mw(scope, receive, send)
    return sent


def _status_of(sent):
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.asyncio
async def test_auth_disabled_is_pass_through():
    app = _RecordingApp()
    mw = core._SecurityMiddleware(
        app, auth_enabled=False, token_store=None, mcp_path="/mcp", trust_forwarded=False)

    await _run(mw, _make_scope("/mcp"))

    assert app.called is True


@pytest.mark.asyncio
async def test_non_mcp_path_is_pass_through_even_when_enabled():
    app = _RecordingApp()
    mw = core._SecurityMiddleware(
        app, auth_enabled=True, token_store=_FakeTokenStore(), mcp_path="/mcp", trust_forwarded=False)

    await _run(mw, _make_scope("/healthz"))

    assert app.called is True


@pytest.mark.asyncio
async def test_locked_when_no_token_store():
    app = _RecordingApp()
    mw = core._SecurityMiddleware(
        app, auth_enabled=True, token_store=None, mcp_path="/mcp", trust_forwarded=False)

    sent = await _run(mw, _make_scope("/mcp"))

    assert app.called is False
    assert _status_of(sent) == 503


@pytest.mark.asyncio
async def test_locked_when_token_store_empty():
    app = _RecordingApp()
    mw = core._SecurityMiddleware(
        app, auth_enabled=True, token_store=_FakeTokenStore({}), mcp_path="/mcp", trust_forwarded=False)

    sent = await _run(mw, _make_scope("/mcp"))

    assert app.called is False
    assert _status_of(sent) == 503


@pytest.mark.asyncio
async def test_missing_bearer_header_is_rejected():
    app = _RecordingApp()
    store = _FakeTokenStore({"apstra_valid": "client-a"})
    mw = core._SecurityMiddleware(
        app, auth_enabled=True, token_store=store, mcp_path="/mcp", trust_forwarded=False)

    sent = await _run(mw, _make_scope("/mcp"))

    assert app.called is False
    assert _status_of(sent) == 401


@pytest.mark.asyncio
async def test_invalid_bearer_token_is_rejected():
    app = _RecordingApp()
    store = _FakeTokenStore({"apstra_valid": "client-a"})
    mw = core._SecurityMiddleware(
        app, auth_enabled=True, token_store=store, mcp_path="/mcp", trust_forwarded=False)

    sent = await _run(mw, _make_scope("/mcp", {"authorization": "Bearer apstra_wrong"}))

    assert app.called is False
    assert _status_of(sent) == 401


@pytest.mark.asyncio
async def test_valid_bearer_token_is_accepted():
    app = _RecordingApp()
    store = _FakeTokenStore({"apstra_valid": "client-a"})
    mw = core._SecurityMiddleware(
        app, auth_enabled=True, token_store=store, mcp_path="/mcp", trust_forwarded=False)

    sent = await _run(mw, _make_scope("/mcp", {"authorization": "Bearer apstra_valid"}))

    assert app.called is True
    assert _status_of(sent) == 200


@pytest.mark.asyncio
async def test_non_http_scope_is_passed_through_untouched():
    app = _RecordingApp()
    mw = core._SecurityMiddleware(
        app, auth_enabled=True, token_store=_FakeTokenStore(), mcp_path="/mcp", trust_forwarded=False)

    await _run(mw, {"type": "lifespan"})

    assert app.called is True
