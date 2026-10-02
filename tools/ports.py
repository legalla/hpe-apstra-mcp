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


@mcp.tool()
def resolve_port_interfaces(
    blueprint_id: str,
    device: str = None,
    port: str = None,
    port_tags: list = None,
    system_tags: list = None,
    tag_match: str = "all",
) -> list:
    """Resolve (leaf, port) to the interface ids of Apstra's graph.

    For each leaf port facing a generic system: `ct_interface_id` (application
    point for Connectivity Templates, i.e. `interface_ids` of a CT apply) and
    `endpoint_interface_id` (generic-side interface = `interface_id` of a VN's
    `endpoints`). LAG members resolve to their port-channel. `device`: leaf
    label or id; `port`: exact name (e.g. 'ge-0/0/3'); both optional.
    Tag filters: `port_tags` (ports carrying the tag(s)) and/or `system_tags`
    (ports of switches carrying the tag(s)); both = intersection; `tag_match`
    'all'|'any'. `device`/`port` still apply on top."""
    c = _client()
    if not (port_tags or system_tags):
        return c.resolve_port_interfaces(blueprint_id, device=device, port=port)
    rows, _ = c.resolve_tagged_ports(blueprint_id, port_tags, system_tags, tag_match)
    return [r for r in rows
            if (not device or device in (r["switch_id"], r["switch"]))
            and (not port or r["port"] == port)]
