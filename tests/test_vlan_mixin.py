"""Unit tests for VlanMixin.add_vlan_to_port's decision logic (ESI -> vxlan,
VNI resolution, VRF requirement) with a stubbed client — no HTTP involved."""

import pytest

from apstra_client.vlan import VlanMixin


class _StubClient(VlanMixin):
    """A minimal ApstraClient stand-in: only the methods add_vlan_to_port
    actually calls are provided, each overridable per test."""

    def __init__(self, *, leaf_found=True, bound_id=None, zones=None,
                 create_vn_result=None):
        self._leaf_found = leaf_found
        self._bound_id = bound_id
        self._zones = zones or {}
        self._create_vn_result = create_vn_result or {"id": "vn-123"}
        self.create_virtual_network_calls = []

    def _qe(self, blueprint_id, query):
        if "hosted_interfaces" in query:
            return []  # no port lookups needed in these tests
        if not self._leaf_found:
            return []
        return [{"s": {"id": "leaf-1", "label": "leaf1"}}]

    def _get(self, path, params=None):
        if path.endswith("/security-zones"):
            return {"items": self._zones}
        return {}

    def _resolve_system_id_for_bound_to(self, blueprint_id, system_id):
        return self._bound_id or system_id

    def create_virtual_network(self, blueprint_id, payload):
        self.create_virtual_network_calls.append(payload)
        return self._create_vn_result


_ZONES = {
    "default": {"id": "default-id", "vrf_name": "default"},
    "z1": {"id": "z1", "vrf_name": "Tenant1", "label": "Tenant1"},
}


def test_invalid_tagging_raises_before_any_lookup():
    client = _StubClient(leaf_found=False)
    with pytest.raises(ValueError, match="tagging"):
        client.add_vlan_to_port("bp1", "leaf1", 10, tagging="sideways")


def test_leaf_not_found_raises():
    client = _StubClient(leaf_found=False)
    with pytest.raises(ValueError, match="not found"):
        client.add_vlan_to_port("bp1", "unknown-leaf", 10)


def test_pure_vlan_on_non_esi_leaf():
    client = _StubClient(bound_id="leaf-1", zones=_ZONES)  # not ESI: bound == leaf id

    result = client.add_vlan_to_port("bp1", "leaf1", 10)

    payload = client.create_virtual_network_calls[0]
    assert payload["vn_type"] == "vlan"
    assert payload["vn_id"] == "10"
    assert result["vn_id"] == "vn-123"


def test_esi_pair_forces_vxlan_and_requires_security_zone():
    client = _StubClient(bound_id="rg-1", zones=_ZONES)  # ESI: bound != leaf id

    with pytest.raises(ValueError, match="Routing Zone"):
        client.add_vlan_to_port("bp1", "leaf1", 10)

    assert client.create_virtual_network_calls == []


def test_esi_pair_with_security_zone_by_vrf_name_uses_default_vni():
    client = _StubClient(bound_id="rg-1", zones=_ZONES)

    client.add_vlan_to_port("bp1", "leaf1", 10, security_zone_id="Tenant1")

    payload = client.create_virtual_network_calls[0]
    assert payload["vn_type"] == "vxlan"
    assert payload["security_zone_id"] == "z1"
    assert payload["vn_id"] == "10010"  # default: 10000 + vlan_id


def test_explicit_vni_overrides_default():
    client = _StubClient(bound_id="rg-1", zones=_ZONES)

    client.add_vlan_to_port("bp1", "leaf1", 10, security_zone_id="Tenant1", vni=555)

    assert client.create_virtual_network_calls[0]["vn_id"] == "555"


def test_l2_vni_overrides_explicit_vni():
    client = _StubClient(bound_id="rg-1", zones=_ZONES)

    client.add_vlan_to_port(
        "bp1", "leaf1", 10, security_zone_id="Tenant1", vni=555, l2_vni=777)

    assert client.create_virtual_network_calls[0]["vn_id"] == "777"


def test_unknown_security_zone_raises_with_available_list():
    client = _StubClient(bound_id="rg-1", zones=_ZONES)

    with pytest.raises(ValueError, match="not found"):
        client.add_vlan_to_port("bp1", "leaf1", 10, security_zone_id="NoSuchVRF")


def test_ipv4_subnet_forces_vxlan_even_without_esi():
    client = _StubClient(bound_id="leaf-1", zones=_ZONES)  # not ESI

    client.add_vlan_to_port(
        "bp1", "leaf1", 10, security_zone_id="Tenant1", ipv4_subnet="10.20.30.0/24")

    payload = client.create_virtual_network_calls[0]
    assert payload["vn_type"] == "vxlan"
    assert payload["ipv4_subnet"] == "10.20.30.0/24"
    assert payload["virtual_gateway_ipv4"] == "10.20.30.1"  # first usable address


def test_dhcp_relay_flag_reflected_in_payload():
    client = _StubClient(bound_id="leaf-1", zones=_ZONES)

    client.add_vlan_to_port("bp1", "leaf1", 10, dhcp_relay=True, security_zone_id="Tenant1")

    assert client.create_virtual_network_calls[0]["dhcp_service"] == "dhcpServiceEnabled"


# ── Reusing an existing VN ─────────────────────────────────────────────

class _ReuseClient(_StubClient):
    """Adds a VN list, a resolvable port and CT/PATCH recording."""

    def __init__(self, vns, cts=None, **kw):
        super().__init__(**kw)
        self._vns = {v["id"]: v for v in vns}
        self._cts = [{"id": "ct-1", "label": "vn_110", "tagging": ["vlan_tagged"]}] if cts is None else cts
        self.patches = []
        self.applied = []
        self.created_cts = []

    def _qe(self, blueprint_id, query):
        if "if_name='ge-0/0/3'" in query:
            return [{"i": {"id": "sw-if"}}]
        return super()._qe(blueprint_id, query)

    def resolve_port_interfaces(self, blueprint_id, device=None, port=None):
        return [{"endpoint_interface_id": "gen-if", "ct_interface_id": "ct-if"}]

    def get_vn_connectivity_templates(self, blueprint_id, vn_id):
        return self._cts

    def create_vn_connectivity_template(self, blueprint_id, vn_id, label, vn_type, tag_type):
        self.created_cts.append(tag_type)
        return {"id": "ct-new", "label": "new", "tagging": [tag_type]}

    def apply_ct_to_interfaces(self, blueprint_id, ct_id, interface_ids):
        self.applied.append((ct_id, interface_ids))
        return {}

    def _get(self, path, params=None):
        if path.endswith("/virtual-networks"):
            return {"virtual_networks": self._vns}
        if "/virtual-networks/" in path:
            return self._vns[path.rsplit("/", 1)[1]]
        return super()._get(path, params)

    def _patch(self, path, body):
        self.patches.append((path, body))
        return {}


_VN = {"id": "vn1", "label": "vn_110", "vn_type": "vxlan", "vn_id": "10110",
       "bound_to": [{"system_id": "rg-1", "vlan_id": 110, "access_switch_node_ids": []}],
       "endpoints": [{"interface_id": "other-if", "tag_type": "vlan_tagged"}]}


def test_existing_vn_without_reuse_flag_raises_explicit_error():
    client = _ReuseClient([_VN], bound_id="rg-1", zones=_ZONES)

    with pytest.raises(ValueError, match="reuse_existing"):
        client.add_vlan_to_port("bp1", "leaf1", 110, label="vn_110",
                                security_zone_id="Tenant1")

    assert client.create_virtual_network_calls == []


def test_vn_id_applies_the_vn_ct_to_the_port_without_creating():
    client = _ReuseClient([_VN], bound_id="rg-1", zones=_ZONES)

    result = client.add_vlan_to_port(
        "bp1", "leaf1", port="ge-0/0/3", tagging="tagged", vn_id="vn_110")

    assert client.create_virtual_network_calls == []
    assert result["reused_existing_vn"] is True
    assert result["ct_id"] == "ct-1"
    assert client.applied == [("ct-1", ["ct-if"])]
    assert client.patches == []  # already bound to the ESI pair: nothing to bind


def test_vn_without_ct_gets_one_created_before_apply():
    client = _ReuseClient([_VN], cts=[], bound_id="rg-1", zones=_ZONES)

    client.add_vlan_to_port("bp1", "leaf1", port="ge-0/0/3", tagging="tagged", vn_id="vn1")

    assert client.created_cts == ["vlan_tagged"]
    assert client.applied == [("ct-new", ["ct-if"])]


def test_reuse_existing_binds_vn_to_a_new_leaf():
    client = _ReuseClient([_VN], bound_id="leaf-3", zones=_ZONES)

    client.add_vlan_to_port("bp1", "leaf3", label="vn_110", reuse_existing=True)

    path, body = client.patches[0]
    assert {b["system_id"] for b in body["bound_to"]} == {"rg-1", "leaf-3"}
    assert body["bound_to"][-1]["vlan_id"] == 110  # taken from the existing binding


def test_assigning_twice_is_idempotent():
    vn = {**_VN, "endpoints": [{"interface_id": "gen-if", "tag_type": "vlan_tagged"}]}
    client = _ReuseClient([vn], bound_id="rg-1", zones=_ZONES)

    result = client.add_vlan_to_port(
        "bp1", "leaf1", port="ge-0/0/3", tagging="tagged", vn_id="vn1")

    assert client.applied == []
    assert any(s.get("status") == "already_assigned" for s in result["steps"])


def test_unknown_vn_id_raises():
    client = _ReuseClient([_VN], bound_id="rg-1", zones=_ZONES)

    with pytest.raises(ValueError, match="No VN matches"):
        client.add_vlan_to_port("bp1", "leaf1", vn_id="nope")


def test_vlan_id_required_without_vn_id():
    client = _ReuseClient([], bound_id="rg-1", zones=_ZONES)

    with pytest.raises(ValueError, match="vlan_id"):
        client.add_vlan_to_port("bp1", "leaf1")


def test_create_with_port_and_tagging_requests_ct_creation():
    client = _ReuseClient([{"id": "vn-123", "endpoints": []}], bound_id="leaf-1", zones=_ZONES)

    client.add_vlan_to_port("bp1", "leaf1", 10, port="ge-0/0/3", tagging="untagged")

    payload = client.create_virtual_network_calls[0]
    assert payload["create_policy_untagged"] is True
    assert payload["create_policy_tagged"] is False
    assert "endpoints" not in payload
