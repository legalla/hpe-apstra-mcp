"""Tools: locate a host by MAC or IP."""

from core import mcp, _client, _require_write


@mcp.tool()
def locate(
    mac: str = None,
    ip: str = None,
    blueprint_id: str = None,
) -> dict:
    """Locate an endpoint on the network by MAC or IP address (/locate).

    - MAC: reads the fabric MAC table (IBA 'MAC Monitor' probe) and distinguishes
      the real PHYSICAL location (port + leaf, mac_result.physical_location) from
      REMOTE locations learned via VXLAN/VTEP (mac_result.remote_locations).
    - IP: correlates three sources in ip_result -> learned ARP table (real host
      on its leaf and port: arp_matches), graph interface IP
      (fabric/underlay: interface_matches) and EVPN Type-5 subnet containing
      the IP (advertising leaf + route target: evpn_routes).

    At least one criterion (mac or ip) must be provided. MAC comparison ignores
    separators. 'blueprint_id' omitted -> all blueprints are searched.
    """
    return _client().locate(blueprint_id=blueprint_id, mac=mac, ip=ip)
