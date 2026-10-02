"""Unit tests for NetworksMixin: dict-shaped listings, bound_to resolution and
create-time verification, with a stubbed controller."""

from apstra_client.networks import NetworksMixin
from apstra_client.base import BaseMixin


class _Stub(NetworksMixin):
    _dict_values = staticmethod(BaseMixin._dict_values)
    _slim = staticmethod(BaseMixin._slim)

    def __init__(self, get=None, qe=None, post=None):
        self._get_map = get or {}
        self._qe_fn = qe or (lambda bp, q: [])
        self._post_result = post or {"id": "vn1"}
        self.posted = []

    def _get(self, path, params=None):
        return self._get_map[path]

    def _qe(self, bp, q):
        return self._qe_fn(bp, q)

    def _post(self, path, body):
        self.posted.append(body)
        return dict(self._post_result)


def test_list_virtual_networks_handles_id_indexed_dict():
    c = _Stub(get={"/blueprints/bp/virtual-networks": {"virtual_networks": {
        "a": {"id": "a", "label": "vn_a", "vn_id": "10", "extra": 1}}}})
    assert c.list_virtual_networks("bp") == [{"id": "a", "label": "vn_a", "vn_id": "10"}]


def test_list_security_zones_handles_items_dict():
    c = _Stub(get={"/blueprints/bp/security-zones": {"items": {
        "z": {"id": "z", "label": "blue", "vrf_name": "blue"}}}})
    assert c.list_security_zones("bp") == [{"id": "z", "label": "blue", "vrf_name": "blue"}]


def _qe_rg(bp, q):
    if "node('system', id='leaf1'" in q and "composed_of_systems" in q:
        return [{"rg": {"id": "rg-1"}}]
    if "node('system', id='leaf1'" in q:
        return [{"s": {"id": "leaf1"}}]
    return []


def test_bound_to_esi_member_is_mapped_and_reported():
    c = _Stub(qe=_qe_rg)
    resolved, notes = c._resolve_bound_to("bp", ["leaf1", {"system_id": "leaf1", "vlan_id": 5}])
    assert [r["system_id"] for r in resolved] == ["rg-1"]
    assert notes[0]["requested"] == "leaf1" and notes[0]["resolved_to"] == "rg-1"


def test_create_reports_fields_the_controller_did_not_apply(monkeypatch):
    monkeypatch.setattr("apstra_client.networks.time.sleep", lambda s: None)
    c = _Stub(
        get={"/blueprints/bp/virtual-networks/vn1": {
            "label": "v", "vn_id": "10110", "ipv4_subnet": None,
            "virtual_gateway_ipv4": None, "bound_to": []}},
        post={"id": "vn1"})
    result = c.create_virtual_network("bp", {"label": "v", "ipv4_subnet": "192.168.110.0/24"})
    assert any("ipv4_subnet" in w for w in result["warnings"])
    assert "applied" in result
