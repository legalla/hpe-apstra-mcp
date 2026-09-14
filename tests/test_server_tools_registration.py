"""Regression test: server.py must keep registering all 61 tools + 6 prompts
after the apstra_client.py -> apstra_client/ and server.py -> core.py + tools/
split (2026-09-14), and mutating tools must stay guarded by @_require_write."""

import inspect

import pytest

import server  # noqa: F401  (triggers tool/prompt registration as import side effect)
import core
from core import mcp
from tools import blueprints as tools_blueprints
from tools import vlan as tools_vlan

_MUTATING_TOOLS = {
    "create_blueprint", "commit_blueprint", "update_virtual_network",
    "create_virtual_network", "delete_virtual_network", "apply_ct_to_interfaces",
    "enable_vn_dci", "create_security_zone", "enable_sz_dci",
    "create_generic_system", "rollback_blueprint", "revert_staging",
    "add_vlan_to_port",
}

_READ_ONLY_SAMPLE = {
    "list_blueprints", "get_version", "list_systems", "find_endpoint",
    "locate", "get_fabric_matrix", "cabling_matrix", "list_ports",
    "prepare_vlan",  # preflight only, deliberately NOT write-guarded
}


def _tools() -> dict:
    mgr = mcp._tool_manager
    return getattr(mgr, "_tools", None) or getattr(mgr, "tools", None)


def _prompts() -> dict:
    mgr = mcp._prompt_manager
    return getattr(mgr, "_prompts", None) or getattr(mgr, "prompts", None)


def _call_until_guard(fn):
    """Call *fn* with placeholder positional args for its required
    parameters, just enough to reach the @_require_write guard (it raises
    before touching any real argument value or client)."""
    sig = inspect.signature(fn)
    args = [
        "x" for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty
    ]
    return fn(*args)


def test_all_61_tools_are_registered():
    names = set(_tools().keys())
    assert len(names) == 61
    assert _MUTATING_TOOLS <= names
    assert _READ_ONLY_SAMPLE <= names


def test_all_6_prompts_are_registered():
    assert len(_prompts()) == 6


def test_mutating_tools_are_write_guarded_by_default(monkeypatch):
    monkeypatch.setattr(core, "_WRITE_ENABLED", False)
    tools = _tools()

    for name in _MUTATING_TOOLS:
        with pytest.raises(PermissionError):
            _call_until_guard(tools[name].fn)


def test_mutating_tool_reaches_client_once_write_enabled(monkeypatch):
    monkeypatch.setattr(core, "_WRITE_ENABLED", True)
    # Tool modules bind `_client` at import time (from core import ... _client),
    # so the stub must replace it in the tool module's own namespace.
    monkeypatch.setattr(
        tools_blueprints, "_client",
        lambda: (_ for _ in ()).throw(RuntimeError("no client in test")))
    tools = _tools()

    # No longer a PermissionError: the guard let the call through to the
    # (stubbed-out) client, which fails for an unrelated reason.
    with pytest.raises(RuntimeError, match="no client in test"):
        _call_until_guard(tools["create_blueprint"].fn)


def test_prepare_vlan_is_not_write_guarded(monkeypatch):
    monkeypatch.setattr(core, "_WRITE_ENABLED", False)
    monkeypatch.setattr(
        tools_vlan, "_client",
        lambda: (_ for _ in ()).throw(RuntimeError("no client in test")))
    tools = _tools()

    # prepare_vlan is read-only: it must reach the (stubbed) client instead
    # of being blocked by the write guard.
    with pytest.raises(RuntimeError, match="no client in test"):
        _call_until_guard(tools["prepare_vlan"].fn)
