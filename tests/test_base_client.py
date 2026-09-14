"""Unit tests for apstra_client.base.BaseMixin (HTTP helpers), with a fake
`requests.Session` — no real network calls."""

import pytest
import requests

from apstra_client.base import BaseMixin


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self.reason = "OK" if status_code < 400 else "Error"
        self.ok = status_code < 400
        self.text = text
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} {self.reason}")


class _FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []
        self._next = {}

    def _record(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._next.get(method, _FakeResponse())

    def get(self, url, **kwargs):
        return self._record("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self._record("post", url, **kwargs)

    def patch(self, url, **kwargs):
        return self._record("patch", url, **kwargs)

    def put(self, url, **kwargs):
        return self._record("put", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._record("delete", url, **kwargs)


def _client(session=None):
    c = BaseMixin("apstra.example.test", "user", "pass")
    c.session = session or _FakeSession()
    return c


# ── _slim / _extract_items ──────────────────────────────────────────────

def test_slim_keeps_only_requested_keys_when_present():
    items = [{"id": "1", "label": "a", "extra": "x"}, {"id": "2"}]
    assert BaseMixin._slim(items, "id", "label") == [
        {"id": "1", "label": "a"}, {"id": "2"},
    ]


@pytest.mark.parametrize("data,expected", [
    ({"items": [{"id": 1}]}, [{"id": 1}]),
    ({"data": [{"id": 2}]}, [{"id": 2}]),
    ({"result": [{"id": 3}]}, [{"id": 3}]),
    ({"other": "x"}, []),
    ([{"id": 4}], [{"id": 4}]),
    ("not-a-collection", []),
])
def test_extract_items_normalizes_response_shapes(data, expected):
    assert BaseMixin._extract_items(data) == expected


# ── login / _ensure_logged_in ───────────────────────────────────────────

def test_login_sets_token_and_auth_header():
    c = _client()
    c.session._next["post"] = _FakeResponse(json_data={"token": "abc123"})

    c.login()

    assert c.token == "abc123"
    assert c.session.headers["AuthToken"] == "abc123"
    assert c.session.headers["Content-Type"] == "application/json"


def test_ensure_logged_in_only_logs_in_once():
    c = _client()
    c.session._next["post"] = _FakeResponse(json_data={"token": "abc123"})

    c._ensure_logged_in()
    c._ensure_logged_in()

    post_calls = [call for call in c.session.calls if call[0] == "post"]
    assert len(post_calls) == 1


def test_logout_clears_token():
    c = _client()
    c.token = "abc123"
    c.session.headers["AuthToken"] = "abc123"

    c.logout()

    assert c.token is None


# ── _get / _post / _patch / _put / _delete ──────────────────────────────

def test_get_returns_json_body_on_success():
    c = _client()
    c.token = "already-logged-in"  # skip login
    c.session._next["get"] = _FakeResponse(json_data={"ok": True})

    result = c._get("/blueprints")

    assert result == {"ok": True}
    method, url, kwargs = c.session.calls[-1]
    assert method == "get"
    assert url == "https://apstra.example.test/api/blueprints"


def test_get_raises_httperror_with_json_body_on_failure():
    c = _client()
    c.token = "already-logged-in"
    c.session._next["get"] = _FakeResponse(
        status_code=404, json_data={"errors": "not found"})

    with pytest.raises(requests.HTTPError) as exc_info:
        c._get("/blueprints/missing")

    assert "not found" in str(exc_info.value)
    assert "404" in str(exc_info.value)


def test_get_raises_httperror_with_raw_text_when_body_not_json():
    c = _client()
    c.token = "already-logged-in"
    c.session._next["get"] = _FakeResponse(status_code=500, text="boom")

    with pytest.raises(requests.HTTPError) as exc_info:
        c._get("/blueprints")

    assert "boom" in str(exc_info.value)


def test_post_sends_json_body_and_triggers_login_first():
    c = _client()
    c.session._next["post"] = _FakeResponse(json_data={"token": "tok"})

    result = c._post("/blueprints", {"label": "bp1"})

    # First POST call is the login; the second is the actual request.
    assert len(c.session.calls) == 2
    method, url, kwargs = c.session.calls[-1]
    assert url == "https://apstra.example.test/api/blueprints"
    assert kwargs["json"] == {"label": "bp1"}
    assert result == {"token": "tok"}


def test_patch_and_put_use_correct_http_methods():
    c = _client()
    c.token = "already-logged-in"
    c.session._next["patch"] = _FakeResponse(json_data={"ok": "patched"})
    c.session._next["put"] = _FakeResponse(json_data={"ok": "put"})

    assert c._patch("/x", {"a": 1}) == {"ok": "patched"}
    assert c._put("/x", {"a": 1}) == {"ok": "put"}


def test_delete_raises_on_error_status():
    c = _client()
    c.token = "already-logged-in"
    c.session._next["delete"] = _FakeResponse(status_code=400)

    with pytest.raises(requests.HTTPError):
        c._delete("/blueprints/x")
