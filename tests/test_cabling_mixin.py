"""Unit tests for CablingMixin._topology_links: de-duplication of half-links
and exclusion of self-loops, from raw query-engine rows."""

from apstra_client.cabling import CablingMixin


class _StubClient(CablingMixin):
    def __init__(self, rows):
        self._rows = rows

    def _qe(self, blueprint_id, query):
        return self._rows


def _system(id_, label, role, system_type="switch"):
    return {"id": id_, "label": label, "role": role, "system_type": system_type}


def _iface(if_name):
    return {"id": f"iface-{if_name}", "if_name": if_name}


def test_self_loop_is_excluded():
    rows = [{
        "s1": _system("sw1", "leaf1", "leaf"), "i1": _iface("xe-0/0/0"),
        "s2": _system("sw1", "leaf1", "leaf"), "i2": _iface("xe-0/0/1"),
        "l": {"role": "spine_leaf"},
    }]
    client = _StubClient(rows)
    assert client._topology_links("bp1") == []


def test_half_links_are_deduplicated():
    row_a_to_b = {
        "s1": _system("leaf1", "leaf1", "leaf"), "i1": _iface("xe-0/0/0"),
        "s2": _system("spine1", "spine1", "spine"), "i2": _iface("et-0/0/0"),
        "l": {"role": "spine_leaf"},
    }
    # The reverse half-link (same physical link, other endpoint queried first).
    row_b_to_a = {
        "s1": _system("spine1", "spine1", "spine"), "i1": _iface("et-0/0/0"),
        "s2": _system("leaf1", "leaf1", "leaf"), "i2": _iface("xe-0/0/0"),
        "l": {"role": "spine_leaf"},
    }
    client = _StubClient([row_a_to_b, row_b_to_a])

    links = client._topology_links("bp1")

    assert len(links) == 1
    assert links[0]["a_system"] == "leaf1"
    assert links[0]["b_system"] == "spine1"
    assert links[0]["role"] == "spine_leaf"


def test_distinct_links_are_all_kept():
    rows = [
        {
            "s1": _system("leaf1", "leaf1", "leaf"), "i1": _iface("xe-0/0/0"),
            "s2": _system("spine1", "spine1", "spine"), "i2": _iface("et-0/0/0"),
            "l": {"role": "spine_leaf"},
        },
        {
            "s1": _system("leaf1", "leaf1", "leaf"), "i1": _iface("xe-0/0/1"),
            "s2": _system("spine2", "spine2", "spine"), "i2": _iface("et-0/0/1"),
            "l": {"role": "spine_leaf"},
        },
    ]
    client = _StubClient(rows)

    assert len(client._topology_links("bp1")) == 2
