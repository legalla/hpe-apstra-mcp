"""Unit tests for dispatch/flat_tools.py (opt-in flat dispatcher toolset,
APSTRA_FLAT_TOOLSET). Dispatcher-routing logic is tested in-process against a
fake client; toolset activation/registration is tested via a subprocess since
FastMCP's tool registry is a process-wide singleton (mirrors the pattern used
for the always-on 61-tool baseline in test_server_tools_registration.py)."""

import json
import os
import subprocess
import sys

import pytest

import core
from dispatch import flat_tools as ft

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeClient:
    def list_asn_pools(self):
        return ["asn1"]

    def list_blueprints(self):
        return ["bp1"]

    def get_blueprint_nodes(self, blueprint_id, node_type=None):
        return {"bp": blueprint_id, "node_type": node_type}

    def get_switch_properties(self, blueprint_id, switch_id):
        return {"bp": blueprint_id, "switch": switch_id}

    def get_fabric_matrix(self, rack=None, blueprint_id=None):
        return {"rack": rack, "bp": blueprint_id}

    def list_virtual_networks(self, blueprint_id):
        return [blueprint_id]

    def get_version(self):
        return {"version": "5.0"}

    def get_bgp_status(self, blueprint_id=None, system=None, state=None):
        return {"bp": blueprint_id, "system": system, "state": state}

    def find_endpoint(self, blueprint_id=None, ip=None, mac=None, name=None):
        return {"ip": ip, "mac": mac}

    def create_virtual_network(self, blueprint_id, args):
        return {"bp": blueprint_id, "args": args}

    def add_vlan_preflight(self, blueprint_id, leaf, port=None):
        return {"bp": blueprint_id, "leaf": leaf, "port": port}


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(core, "_apstra_client", client)
    return client


# ── Read dispatchers: correct routing ──────────────────────────────────

def test_list_catalog_routes_by_scope(fake_client):
    assert ft.list_catalog("asn_pools") == ["asn1"]


def test_get_blueprint_list_needs_no_blueprint_id(fake_client):
    assert ft.get_blueprint("list") == ["bp1"]


def test_get_blueprint_requires_blueprint_id_for_other_scopes(fake_client):
    assert "error" in ft.get_blueprint("nodes")


def test_get_blueprint_nodes_forwards_node_type(fake_client):
    result = ft.get_blueprint("nodes", blueprint_id="bp1", node_type="system")
    assert result == {"bp": "bp1", "node_type": "system"}


def test_get_topology_requires_blueprint_id(fake_client):
    assert "error" in ft.get_topology("switch_properties")


def test_get_cabling_fabric_matrix_scope(fake_client):
    result = ft.get_cabling(scope="fabric_matrix", blueprint_id="bp1", rack="rack1")
    assert result == {"rack": "rack1", "bp": "bp1"}


def test_get_network_virtual_networks(fake_client):
    assert ft.get_network("virtual_networks", "bp1") == ["bp1"]


def test_get_system_version(fake_client):
    assert ft.get_system("version") == {"version": "5.0"}


def test_get_telemetry_bgp_status(fake_client):
    result = ft.get_telemetry("bgp_status", blueprint_id="bp1", system="leaf1")
    assert result == {"bp": "bp1", "system": "leaf1", "state": None}


def test_locate_endpoint_scope(fake_client):
    assert ft.locate(scope="endpoint", ip="10.0.0.1") == {"ip": "10.0.0.1", "mac": None}


# ── Unknown scope/action -> clear error dict, not an exception ─────────

@pytest.mark.parametrize("dispatcher, kwargs", [
    (ft.list_catalog, {"scope": "bogus"}),
    (ft.get_blueprint, {"scope": "bogus", "blueprint_id": "bp1"}),
    (ft.get_topology, {"scope": "bogus", "blueprint_id": "bp1"}),
    (ft.get_cabling, {"scope": "bogus"}),
    (ft.get_network, {"scope": "bogus", "blueprint_id": "bp1"}),
    (ft.get_system, {"scope": "bogus"}),
    (ft.get_telemetry, {"scope": "bogus"}),
    (ft.locate, {"scope": "bogus"}),
])
def test_unknown_scope_returns_error_dict(dispatcher, kwargs, fake_client):
    result = dispatcher(**kwargs)
    assert "error" in result and "valid_scopes" in result


def test_configure_network_unknown_scope_action(fake_client):
    result = ft.configure_network("bogus", "create", "bp1")
    assert "error" in result and "valid_scopes" in result


def test_configure_fabric_unknown_scope_action(fake_client):
    result = ft.configure_fabric("bogus", "create", "bp1")
    assert "error" in result and "valid_scopes" in result


# ── Write dispatchers: still gated by APSTRA_WRITE_ENABLED ────────────

def test_configure_network_blocked_read_only_by_default(monkeypatch, fake_client):
    monkeypatch.setattr(core, "_WRITE_ENABLED", False)
    with pytest.raises(PermissionError):
        ft.configure_network("virtual_network", "create", "bp1",
                              params={"label": "vn1", "vn_type": "vlan"})


def test_configure_network_reaches_client_once_write_enabled(monkeypatch, fake_client):
    monkeypatch.setattr(core, "_WRITE_ENABLED", True)
    result = ft.configure_network("virtual_network", "create", "bp1",
                                   params={"label": "vn1", "vn_type": "vlan"})
    assert result == {"bp": "bp1", "args": {"label": "vn1", "vn_type": "vlan"}}


def test_configure_fabric_vlan_prepare_is_not_write_guarded(monkeypatch, fake_client):
    monkeypatch.setattr(core, "_WRITE_ENABLED", False)
    result = ft.configure_fabric("vlan", "prepare", "bp1",
                                 params={"leaf": "leaf1", "port": "xe-0/0/1"})
    assert result == {"bp": "bp1", "leaf": "leaf1", "port": "xe-0/0/1"}


def test_configure_network_update_requires_vn_id(fake_client):
    assert "error" in ft.configure_network("virtual_network", "update", "bp1", params={})


def test_configure_network_delete_requires_vn_id(fake_client):
    assert "error" in ft.configure_network("virtual_network", "delete", "bp1", params={})


# ── Toolset activation (process-wide registry -> subprocess isolation) ─

def _run_server_snippet(snippet: str, **extra_env) -> dict:
    env = {**os.environ, **extra_env}
    code = "import json, server\n" + snippet
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    last_line = proc.stdout.strip().splitlines()[-1]
    return json.loads(last_line)


def _tool_names_snippet() -> str:
    return (
        "mgr = getattr(server.mcp, '_tool_manager', None) or getattr(server.mcp, 'tool_manager', None)\n"
        "tools = getattr(mgr, '_tools', None) or getattr(mgr, 'tools', None)\n"
        "print(json.dumps(sorted(tools.keys())))\n"
    )


def test_flat_toolset_disabled_by_default_keeps_61_tools():
    names = _run_server_snippet(_tool_names_snippet())
    assert len(names) == 61


def test_flat_toolset_enabled_advertises_12_dispatchers():
    names = _run_server_snippet(_tool_names_snippet(), APSTRA_FLAT_TOOLSET="true")
    assert names == sorted([
        "list_catalog", "get_blueprint", "get_topology", "get_cabling",
        "get_network", "get_system", "get_telemetry", "locate",
        "configure_blueprint", "manage_revisions", "configure_network",
        "configure_fabric",
    ])
