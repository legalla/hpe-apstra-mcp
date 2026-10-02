"""Tools: tags (design/blueprint catalog, assignment to nodes, search by tag)."""

from core import mcp, _client, _require_write


@mcp.tool()
def list_tags(blueprint_id: str = None) -> list:
    """List tags of a blueprint, or of the design catalog if `blueprint_id` is omitted."""
    return _client().list_tags(blueprint_id)


@mcp.tool()
def find_tagged_nodes(
    blueprint_id: str, tags: list, node_type: str = None, match: str = "all",
) -> list:
    """Nodes carrying the tag(s) (`match` 'all' = every tag, 'any' = at least one).
    `node_type`: system | interface | virtual_network | security_zone | ...
    Interfaces are returned with their `system` and `port`."""
    return _client().find_tagged(blueprint_id, tags, node_type, match)


@mcp.tool()
def get_node_tags(blueprint_id: str, node: str) -> dict:
    """Tags of one node (node id, or label of a system / VN / routing zone)."""
    return _client().get_node_tags(blueprint_id, node)


@mcp.tool()
@_require_write
def create_tag(label: str, description: str = "", blueprint_id: str = None) -> dict:
    """Create a tag in a blueprint, or in the design catalog if `blueprint_id` is omitted."""
    return _client().create_tag(label, description, blueprint_id)


@mcp.tool()
@_require_write
def update_tag(
    tag_id: str, label: str = None, description: str = None, blueprint_id: str = None,
) -> dict:
    """Rename / re-describe a tag (blueprint, or design catalog if `blueprint_id` is omitted)."""
    return _client().update_tag(tag_id, label, description, blueprint_id)


@mcp.tool()
@_require_write
def delete_tag(tag_id: str, blueprint_id: str = None) -> dict:
    """Delete a tag (blueprint, or design catalog if `blueprint_id` is omitted)."""
    return _client().delete_tag(tag_id, blueprint_id)


@mcp.tool()
@_require_write
def set_node_tags(
    blueprint_id: str, targets: list, add: list = None, remove: list = None,
    create_missing: bool = False,
) -> dict:
    """Add/remove tags (by label) on nodes. `targets`: node ids, labels (system /
    VN / routing zone) or {"system": "leaf1", "port": "ge-0/0/3"} for a port.
    Unknown tags in `add` are refused unless `create_missing=true`."""
    return _client().set_node_tags(blueprint_id, targets, add, remove, create_missing)
