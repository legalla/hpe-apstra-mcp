"""Unit tests for TagsMixin (selection by tag, assignment validation) with a stubbed graph."""

import pytest

from apstra_client.base import BaseMixin
from apstra_client.tags import TagsMixin


class _Stub(TagsMixin):
    _dict_values = staticmethod(BaseMixin._dict_values)
    _slim = staticmethod(BaseMixin._slim)

    def __init__(self, tags=("blue",), tagged=None, ports=None):
        self._tags = [{"id": f"t-{t}", "label": t, "description": ""} for t in tags]
        self._tagged = tagged or {}  # tag label -> list of node dicts
        self._ports = ports or []
        self.posts = []
        self.created = []

    def list_tags(self, blueprint_id=None):
        return self._tags

    def create_tag(self, label, description="", blueprint_id=None):
        self.created.append(label)
        self._tags.append({"id": f"t-{label}", "label": label})
        return {}

    def _qe(self, bp, q):
        if "node('tag'" in q:
            label = q.split("label='")[1].split("'")[0]
            return [{"x": n} for n in self._tagged.get(label, [])]
        if "hosted_interfaces').node('system', name='s')" in q:
            return [{"s": {"label": "leaf1"}}]
        return []

    def resolve_port_interfaces(self, blueprint_id, device=None, port=None):
        return self._ports

    def _post(self, path, body):
        self.posts.append((path, body))
        return ""

    def node_tags(self, blueprint_id, node_id):
        return ["blue"]

    def _resolve_tag_target(self, blueprint_id, target):
        return {"id": target, "type": "system", "label": target}


_SW = {"id": "s1", "type": "system", "label": "leaf1", "system_type": "switch", "role": "leaf"}
_GEN = {"id": "g1", "type": "system", "label": "srv", "system_type": "server", "role": "generic"}


def test_find_tagged_match_all_vs_any():
    c = _Stub(tags=("a", "b"), tagged={"a": [_SW, _GEN], "b": [_SW]})
    assert [n["id"] for n in c.find_tagged("bp", ["a", "b"])] == ["s1"]
    assert {n["id"] for n in c.find_tagged("bp", ["a", "b"], match="any")} == {"s1", "g1"}


def test_find_tagged_rejects_unknown_tag():
    with pytest.raises(ValueError, match="Unknown tag"):
        _Stub().find_tagged("bp", ["nope"])


def test_resolve_tagged_systems_keeps_switches_only_and_raises_when_empty():
    c = _Stub(tagged={"blue": [_SW, _GEN]})
    assert [s["id"] for s in c.resolve_tagged_systems("bp", ["blue"])] == ["s1"]
    with pytest.raises(ValueError, match="No switch"):
        _Stub(tagged={"blue": [_GEN]}).resolve_tagged_systems("bp", ["blue"])


_ROWS = [
    {"switch": "leaf1", "switch_id": "s1", "port": "ge-0/0/3", "switch_interface_id": "i1", "ct_interface_id": "c1"},
    {"switch": "leaf2", "switch_id": "s2", "port": "ge-0/0/3", "switch_interface_id": "i2", "ct_interface_id": "c2"},
]


def test_resolve_tagged_ports_by_port_tag_and_intersection_with_system_tag():
    port_node = {"id": "i1", "type": "interface", "if_name": "ge-0/0/3"}
    other = {"id": "i2", "type": "interface", "if_name": "ge-0/0/3"}
    c = _Stub(tags=("p", "blue"), ports=_ROWS, tagged={"p": [port_node, other], "blue": [_SW]})

    rows, _ = c.resolve_tagged_ports("bp", port_tags=["p"])
    assert [r["port"] for r in rows] == ["ge-0/0/3", "ge-0/0/3"]
    rows, info = c.resolve_tagged_ports("bp", port_tags=["p"], system_tags=["blue"])
    assert [r["switch"] for r in rows] == ["leaf1"]
    assert info["matched_ports"] == [{"system": "leaf1", "port": "ge-0/0/3"}]


def test_resolve_tagged_ports_requires_a_selector():
    with pytest.raises(ValueError, match="required"):
        _Stub().resolve_tagged_ports("bp")


def test_set_node_tags_refuses_unknown_tag_unless_create_missing():
    c = _Stub()
    with pytest.raises(ValueError, match="not found"):
        c.set_node_tags("bp", ["leaf1"], add=["new"])
    assert c.posts == []

    c.set_node_tags("bp", ["leaf1"], add=["new"], create_missing=True)
    assert c.created == ["new"]
    assert c.posts[0][1] == {"nodes": ["leaf1"], "add": ["new"], "remove": []}


def test_set_node_tags_requires_add_or_remove():
    with pytest.raises(ValueError, match="Nothing to do"):
        _Stub().set_node_tags("bp", ["leaf1"])
