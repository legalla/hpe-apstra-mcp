"""Unit tests for BlueprintsMixin.get_blueprint_build_errors and
get_blueprint_logical_diff (response-flattening logic), with a stubbed
_get — no HTTP involved."""

from apstra_client.blueprints import BlueprintsMixin


class _StubClient(BlueprintsMixin):
    def __init__(self, response):
        self._response = response

    def _get(self, path, params=None):
        return self._response


# ── get_blueprint_build_errors ──────────────────────────────────────────

def test_build_errors_flattens_nodes_and_relationships():
    client = _StubClient({
        "version": 7,
        "errors_count": 2,
        "warnings_count": 1,
        "nodes": {
            "node-1": [{
                "severity": "error", "message": "bad loopback",
                "error_type": "ipv4_missing", "display_category": "Addressing",
                "entity_type": "interface",
                "resolutions": [{"category": "config", "hint": "set an address"}],
            }],
        },
        "relationships": {
            "rel-1": [{
                "severity": "warning", "message": "dangling link",
                "error_type": "link_orphan", "display_category": "Cabling",
                "entity_type": "link",
                "resolutions": [],
            }],
        },
    })

    result = client.get_blueprint_build_errors("bp1")

    assert result["errors_count"] == 2
    assert result["warnings_count"] == 1
    assert result["version"] == 7
    assert len(result["errors"]) == 2

    node_err = next(e for e in result["errors"] if e["scope"] == "nodes")
    assert node_err["entity_id"] == "node-1"
    assert node_err["severity"] == "error"
    assert node_err["resolutions"] == [{"category": "config", "hint": "set an address"}]

    rel_err = next(e for e in result["errors"] if e["scope"] == "relationships")
    assert rel_err["entity_id"] == "rel-1"
    assert rel_err["message"] == "dangling link"


def test_build_errors_handles_empty_response():
    client = _StubClient({"errors_count": 0, "warnings_count": 0})

    result = client.get_blueprint_build_errors("bp1")

    assert result["errors"] == []
    assert result["errors_count"] == 0


# ── get_blueprint_logical_diff ───────────────────────────────────────────

def test_logical_diff_flattens_categories_and_ignores_digest():
    client = _StubClient({
        "digest": {"node": 3, "relationship": 5},
        "endpoint_policies": {
            "added": {"ep-1": {"label": "demo-ct"}},
        },
        "security_zones": {
            "added": {"sz-1": {"label": "Tenant-Test"}},
        },
        "static_routes": None,  # empty category: must be ignored
    })

    result = client.get_blueprint_logical_diff("bp1")

    assert result["digest"] == {"node": 3, "relationship": 5}
    assert result["change_count"] == 2

    types = {c["type"] for c in result["changes"]}
    assert types == {"Connectivity Template", "Routing Zone"}
    assert all(c["action"] == "added" for c in result["changes"])


def test_logical_diff_handles_no_changes():
    client = _StubClient({"digest": {"node": 0, "relationship": 0}})

    result = client.get_blueprint_logical_diff("bp1")

    assert result["changes"] == []
    assert result["change_count"] == 0
