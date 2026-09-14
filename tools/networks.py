"""Tools: Virtual Networks and Security/Routing Zones (incl. DCI)."""

from core import mcp, _client, _require_write


@mcp.tool()
def list_virtual_networks(blueprint_id: str) -> list:
    """List VNs."""
    return _client().list_virtual_networks(blueprint_id)

@mcp.tool()
def get_virtual_network(blueprint_id: str, vn_id: str) -> dict:
    """VN detail."""
    return _client().get_virtual_network(blueprint_id, vn_id)

@mcp.tool()
@_require_write
def update_virtual_network(
    blueprint_id: str,
    vn_id: str,
    bound_to: list = None,
    vni_id: int = None,
    ipv4_gateway: str = None,
    ipv4_subnet: str = None,
    label: str = None,
) -> dict:
    """Update VN."""
    payload: dict = {}
    if bound_to is not None:
        payload["bound_to"] = bound_to
    if vni_id is not None:
        payload["vn_id"] = str(vni_id)
    if ipv4_gateway is not None:
        payload["virtual_gateway_ipv4"] = ipv4_gateway
    if ipv4_subnet is not None:
        payload["ipv4_subnet"] = ipv4_subnet
    if label is not None:
        payload["label"] = label
    if not payload:
        return {"status": "no_change", "message": "No field to update was provided."}
    return _client().update_virtual_network(blueprint_id, vn_id, payload)

@mcp.tool()
@_require_write
def create_virtual_network(
    blueprint_id: str,
    label: str,
    vn_type: str,
    vn_id: int = None,
    security_zone_id: str = None,
    ipv4_subnet: str = None,
    ipv4_gateway: str = None,
) -> dict:
    """Create VN."""
    args: dict = {"label": label, "vn_type": vn_type}
    if vn_id is not None:
        args["vn_id"] = vn_id
    if security_zone_id is not None:
        args["security_zone_id"] = security_zone_id
    if ipv4_subnet is not None:
        args["ipv4_subnet"] = ipv4_subnet
    if ipv4_gateway is not None:
        args["virtual_gateway_ipv4"] = ipv4_gateway
    return _client().create_virtual_network(blueprint_id, args)

@mcp.tool()
@_require_write
def delete_virtual_network(blueprint_id: str, vn_id: str) -> dict:
    """Delete VN."""
    _client().delete_virtual_network(blueprint_id, vn_id)
    return {"status": "deleted", "vn_id": vn_id}

@mcp.tool()
def list_redundancy_groups(blueprint_id: str) -> list:
    """List ESI groups."""
    return _client().list_redundancy_groups(blueprint_id)

@mcp.tool()
def list_connectivity_templates(blueprint_id: str) -> list:
    """List CTs."""
    return _client().list_connectivity_templates(blueprint_id)

@mcp.tool()
@_require_write
def apply_ct_to_interfaces(
    blueprint_id: str,
    ct_id: str,
    interface_ids: list,
) -> dict:
    """Apply CT."""
    return _client().apply_ct_to_interfaces(blueprint_id, ct_id, interface_ids)

@mcp.tool()
@_require_write
def enable_vn_dci(
    blueprint_id: str,
    vn_id: str,
    enable_rt2: bool = True,
    enable_rt5: bool = True,
) -> dict:
    """Enable VN DCI."""
    return _client().enable_vn_dci(
        blueprint_id=blueprint_id, vn_id=vn_id,
        enable_rt2=enable_rt2, enable_rt5=enable_rt5,
    )

@mcp.tool()
def list_security_zones(blueprint_id: str) -> list:
    """List SZs."""
    return _client().list_security_zones(blueprint_id)

@mcp.tool()
@_require_write
def create_security_zone(
    blueprint_id: str,
    label: str,
    vrf_name: str,
    vni_id: int = None,
    sz_type: str = "evpn",
) -> dict:
    """Create SZ."""
    args = dict(label=label, vrf_name=vrf_name, vni_id=vni_id, sz_type=sz_type)
    return _client().create_security_zone(blueprint_id, {k: v for k, v in args.items() if v is not None})

@mcp.tool()
@_require_write
def enable_sz_dci(
    blueprint_id: str,
    sz_id: str,
    enable_rt5: bool = True,
    enable_irt: bool = True,
) -> dict:
    """Enable SZ DCI."""
    return _client().enable_sz_dci(
        blueprint_id=blueprint_id, sz_id=sz_id,
        enable_rt5=enable_rt5, enable_irt=enable_irt,
    )
