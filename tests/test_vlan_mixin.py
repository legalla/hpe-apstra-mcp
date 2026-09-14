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
