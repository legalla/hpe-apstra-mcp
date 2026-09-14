"""Tools: endpoint search (ARP/MAC/EVPN) and VM inventory."""

from core import mcp, _client, _require_write


@mcp.tool()
def find_endpoint(
    ip: str = None,
    mac: str = None,
    name: str = None,
    blueprint_id: str = None,
) -> dict:
    """Search for a learned endpoint (VM/host) by IP address or MAC address.

    Learned endpoints are not part of the design graph: they are exposed by
    telemetry. The search queries the learned ARP table (dynamicArp source:
    IP<->MAC<->interface<->VRF), the "MAC Monitor" probe (MAC address table)
    for a MAC, and the "EVPN VXLAN Type-5 Route Validation" probe (advertised
    /32-/128 routes) for an IP. The serial number reported by telemetry is
    resolved to a switch name.

    At least one criterion (ip or mac) must be provided. MAC comparison ignores
    separators; IP comparison accepts a prefix. If blueprint_id is omitted, all
    blueprints are searched.
    """
    return _client().find_endpoint(blueprint_id=blueprint_id, ip=ip, mac=mac, name=name)

@mcp.tool()
def get_vm_info(
    name: str = None,
    blueprint_id: str = None,
) -> dict:
    """Information about virtual machines (vCenter/NSX integration).

    For each VM: name, hypervisor, ESX server, fabric interfaces
    (switch:port), VLAN, port-group, MAC address, IP address (enriched from the
    ARP table) and source. If 'name' is provided, filters by VM name
    (case-insensitive, partial match). If 'blueprint_id' is omitted, all
    blueprints are searched.
    """
    return _client().get_vm_info(name=name, blueprint_id=blueprint_id)
