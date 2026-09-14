"""Tools: switch topology and properties."""

from core import mcp, _client, _require_write


@mcp.tool()
def get_switch_properties(blueprint_id: str, switch_id: str) -> dict:
    """Switch properties."""
    return _client().get_switch_properties(blueprint_id=blueprint_id, switch_id=switch_id)

@mcp.tool()
def get_switch_uplinks(blueprint_id: str, switch_id: str) -> list:
    """Switch uplinks."""
    return _client().get_switch_uplinks(blueprint_id=blueprint_id, switch_id=switch_id)

@mcp.tool()
def get_link_ips(blueprint_id: str, switch_id_a: str, switch_id_b: str) -> dict:
    """Link IPs."""
    return _client().get_link_ips(
        blueprint_id=blueprint_id, switch_id_a=switch_id_a, switch_id_b=switch_id_b)

@mcp.tool()
def get_switch_loopbacks(blueprint_id: str, switch_id: str) -> list:
    """Switch loopbacks."""
    return _client().get_switch_loopbacks(blueprint_id=blueprint_id, switch_id=switch_id)
