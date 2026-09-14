"""Tools: cabling matrices (graph-based and /cabling-map based)."""

from core import mcp, _client, _require_write


@mcp.tool()
def get_fabric_matrix(
    rack: str = None,
    blueprint_id: str = None,
) -> dict:
    """Hierarchical cabling matrix endpoint -> port -> leaf -> spine.

    Returns the topology organized by rack then by leaf: uplinks of each leaf
    to the spines (leaf port -> spine:port) and endpoints (generic systems)
    connected with their leaf port. Also provides 'rows', a flat list usable as
    a table (endpoint, leaf, port, spines).

    'rack' filters on a rack (exact name or fragment, case-insensitive): omit
    for the full matrix, provide a name for a single rack.
    'blueprint_id' omitted -> all blueprints are searched.
    """
    return _client().get_fabric_matrix(rack=rack, blueprint_id=blueprint_id)

@mcp.tool()
def cabling_matrix(
    blueprint_id: str = None,
    rack: str = None,
    role_filter: str = None,
) -> dict:
    """Cabling matrix of a blueprint via /cabling-map (/cabling_matrix).

    Each physical link is normalized into an oriented A -> B row: local fabric
    switch (leaf/border, spine, superspine) as A, external endpoint
    (server/generic/remote DC) as B, with role, port, IP and operational state
    of each side, plus a category (leaf-spine, endpoint-leaf,
    spine-superspine). Provides 'links' (usable list) and 'by_category'.

    - 'blueprint_id' omitted: all blueprints are searched.
    - 'rack': keep only links with one endpoint in this rack
      (exact name or fragment, case-insensitive).
    - 'role_filter': keep only one category of links (e.g. 'leaf-spine',
      'endpoint-leaf', 'spine-superspine').
    """
    return _client().cabling_matrix(
        blueprint_id=blueprint_id, rack=rack, role_filter=role_filter)
