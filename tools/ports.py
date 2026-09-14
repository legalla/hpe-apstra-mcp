"""Tools: port listing (status, config, LACP, Connectivity Templates)."""

from core import mcp, _client, _require_write


@mcp.tool()
def list_ports(
    blueprint_id: str,
    device: str = None,
    port: str = None,
) -> dict:
    """List the ports of a device (status, config, LACP, CT).

    For each port (standard format, e.g. 'xe-0/0/1'): type, description, admin
    state and real-time operational state (up/down via telemetry), LACP/LAG
    config (aggregate 'ae*' and its members, lacp mode), VLAN, IP address and
    associated Connectivity Templates (by name).

    Scope to clarify with the user if needed:
      - 'device' specified -> ports of this device (label/id/serial number).
        If a single device must be listed and it is not specified, ASK for it.
      - 'port' specified (with 'device') -> only this port. If the user wants a
        specific port without indicating it, ASK for it.
      - neither 'device' nor 'port' -> all devices of the blueprint.
    """
    return _client().list_ports(
        blueprint_id=blueprint_id, device=device, port=port)
