"""Tools: generic systems (servers/devices connected to the fabric)."""

from core import mcp, _client, _require_write


@mcp.tool()
@_require_write
def create_generic_system(
    blueprint_id: str,
    label: str,
    links: list,
    port_speed: str,
    lag_mode: str = None,
    asn: int = None,
    loopback_ip: str = None,
    hostname: str = None,
) -> dict:
    """Create generic system."""
    return _client().create_generic_system(
        blueprint_id=blueprint_id, label=label, links=links,
        port_speed=port_speed, lag_mode=lag_mode, asn=asn,
        loopback_ip=loopback_ip, hostname=hostname,
    )

@mcp.tool()
def list_generic_systems_on_switch(blueprint_id: str, switch_id: str) -> list:
    """List generic systems."""
    return _client().list_generic_systems_on_switch(blueprint_id=blueprint_id, switch_id=switch_id)

@mcp.tool()
def get_generic_system_on_port(blueprint_id: str, switch_id: str, switch_if: str) -> dict:
    """Generic system on port."""
    data = _client().get_generic_system_on_port(
        blueprint_id=blueprint_id, switch_id=switch_id, switch_if=switch_if)
    return data if data is not None else {"status": "free", "message": f"No generic system on {switch_if}"}
