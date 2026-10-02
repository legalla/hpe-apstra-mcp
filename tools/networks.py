"""Tools: Virtual Networks and Security/Routing Zones (incl. DCI)."""

from core import mcp, _client, _require_write


def _with_tagged_systems(blueprint_id, bound_to, bound_to_tags, tag_match, vlan_id):
    """Merge switches carrying `bound_to_tags` into `bound_to`; returns (bound_to, info)."""
    if not bound_to_tags:
        return bound_to, None
    systems = _client().resolve_tagged_systems(blueprint_id, bound_to_tags, tag_match)
    entries = [{"system_id": s["id"], **({"vlan_id": vlan_id} if vlan_id is not None else {})}
               for s in systems]
    info = {"tags": bound_to_tags, "match": tag_match,
            "systems": [s["label"] for s in systems]}
    return list(bound_to or []) + entries, info


def _attach_tag_info(result, info, key="bound_to_tags_resolution"):
    if info and isinstance(result, dict):
        result[key] = info
    return result


@mcp.tool()
def list_virtual_networks(blueprint_id: str) -> list:
    """List VNs."""
    return _client().list_virtual_networks(blueprint_id)

@mcp.tool()
def get_virtual_network(blueprint_id: str, vn_id: str) -> dict:
    """VN detail, plus `connectivity_templates`/`ct_id` and, per endpoint, the
    resolved `system`/`port`."""
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
    ipv4_enabled: bool = None,
    virtual_gateway_ipv4_enabled: bool = None,
    label: str = None,
    bound_to_tags: list = None,
    tag_match: str = "all",
    vlan_id: int = None,
) -> dict:
    """Update VN. Apstra rejects ipv4_subnet/ipv4_gateway unless ipv4_enabled=True
    is also sent (and virtual_gateway_ipv4_enabled=True for the gateway to be
    active); ipv4_subnet/ipv4_gateway auto-set these flags to True if omitted.
    `bound_to` REPLACES the VN's devices. `bound_to_tags` adds every switch
    carrying the tag(s) (`tag_match` 'all'|'any'; `vlan_id` = VLAN on them)."""
    bound_to, tag_info = _with_tagged_systems(
        blueprint_id, bound_to, bound_to_tags, tag_match, vlan_id)
    payload: dict = {}
    if bound_to is not None:
        payload["bound_to"] = bound_to
    if vni_id is not None:
        payload["vn_id"] = str(vni_id)
    if ipv4_subnet is not None:
        payload["ipv4_subnet"] = ipv4_subnet
        payload["ipv4_enabled"] = True
    if ipv4_gateway is not None:
        payload["virtual_gateway_ipv4"] = ipv4_gateway
        payload["virtual_gateway_ipv4_enabled"] = True
    if ipv4_enabled is not None:
        payload["ipv4_enabled"] = ipv4_enabled
    if virtual_gateway_ipv4_enabled is not None:
        payload["virtual_gateway_ipv4_enabled"] = virtual_gateway_ipv4_enabled
    if label is not None:
        payload["label"] = label
    if not payload:
        return {"status": "no_change", "message": "No field to update was provided."}
    return _attach_tag_info(
        _client().update_virtual_network(blueprint_id, vn_id, payload), tag_info)

CT_QUESTION = "Do you want to create a Connectivity Template associated to this Virtual Network ?"
CT_TAGGING_QUESTION = "Tagged or Untagged ?"
_CT_TAGGING_VALUES = ("tagged", "untagged", "both")


def _ct_policy_flags(create_ct: bool, ct_tagging: str) -> dict | None:
    """Return create_policy_* flags, or a question dict if input is missing."""
    if create_ct is None:
        return {"status": "question_required", "question": CT_QUESTION,
                "param": "create_connectivity_template", "options": ["yes", "no"]}
    if not create_ct:
        return {}
    tagging = (ct_tagging or "").strip().lower()
    if tagging not in _CT_TAGGING_VALUES:
        return {"status": "question_required", "question": CT_TAGGING_QUESTION,
                "param": "ct_tagging", "options": list(_CT_TAGGING_VALUES)}
    return {
        "create_policy_tagged": tagging in ("tagged", "both"),
        "create_policy_untagged": tagging in ("untagged", "both"),
    }


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
    ipv4_enabled: bool = None,
    virtual_gateway_ipv4_enabled: bool = None,
    create_connectivity_template: bool = None,
    ct_tagging: str = None,
    bound_to: list = None,
    bound_to_tags: list = None,
    tag_match: str = "all",
    vlan_id: int = None,
) -> dict:
    """Create VN. ipv4_subnet/ipv4_gateway auto-enable ipv4_enabled/
    virtual_gateway_ipv4_enabled unless explicitly overridden. `bound_to`: leaves
    (id, label or {system_id, vlan_id}); ESI members are mapped to their
    redundancy group (reported in `bound_to_resolution`). `bound_to_tags`: also
    bind every switch carrying the tag(s) (`tag_match` 'all'|'any'; `vlan_id` =
    VLAN on them; matched devices in `bound_to_tags_resolution`). The result carries
    `applied` (what the controller stored) and `warnings` if a requested field
    was not applied.

    Connectivity Template (Apstra 'Create Connectivity Template for' checkbox):
    if the user did not say anything about it, call WITHOUT
    `create_connectivity_template` -> the tool returns
    {"status": "question_required", "question": ...} and creates NOTHING.
    Ask the user exactly that question, then call again with
    create_connectivity_template=True/False. If True and `ct_tagging` is
    missing, the tool asks "Tagged or Untagged ?" -> pass ct_tagging=
    'tagged' | 'untagged' | 'both'. Without a CT, the VN cannot be
    assigned to any port."""
    flags = _ct_policy_flags(create_connectivity_template, ct_tagging)
    if flags.get("status") == "question_required":
        return flags
    bound_to, tag_info = _with_tagged_systems(
        blueprint_id, bound_to, bound_to_tags, tag_match, vlan_id)
    args: dict = {"label": label, "vn_type": vn_type, **flags}
    if vn_id is not None:
        args["vn_id"] = str(vn_id)
    if security_zone_id is not None:
        args["security_zone_id"] = security_zone_id
    if ipv4_subnet is not None:
        args["ipv4_subnet"] = ipv4_subnet
        args["ipv4_enabled"] = True
    if ipv4_gateway is not None:
        args["virtual_gateway_ipv4"] = ipv4_gateway
        args["virtual_gateway_ipv4_enabled"] = True
    if ipv4_enabled is not None:
        args["ipv4_enabled"] = ipv4_enabled
    if virtual_gateway_ipv4_enabled is not None:
        args["virtual_gateway_ipv4_enabled"] = virtual_gateway_ipv4_enabled
    if bound_to:
        args["bound_to"] = bound_to
    return _attach_tag_info(_client().create_virtual_network(blueprint_id, args), tag_info)

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
    ct_id: str = None,
    interface_ids: list = None,
    ports: list = None,
    vn_id: str = None,
    port_tags: list = None,
    system_tags: list = None,
    tag_match: str = "all",
) -> dict:
    """Apply CT. `interface_ids` = switch-side interface ids (see
    resolve_port_interfaces `ct_interface_id`). Alternatives: `ports` =
    [{"system": "leaf2", "port": "ge-0/0/3"}] resolved for you, and `vn_id`
    (VN node id) instead of `ct_id` to use the CT of that VN.
    Tag selection (ports facing a generic system only): `port_tags` = ports
    carrying the tag(s); `system_tags` = all ports of the switches carrying the
    tag(s); both = intersection (`tag_match` 'all'|'any'). Result has
    `tag_resolution` listing the ports reached."""
    c = _client()
    ids = list(interface_ids or [])
    tag_info = None
    resolved: list = []
    if port_tags or system_tags:
        rows, tag_info = c.resolve_tagged_ports(blueprint_id, port_tags, system_tags, tag_match)
        if not rows:
            raise ValueError(f"No port facing a generic system matches the tags. {tag_info}")
        resolved += rows
    for p in ports or []:
        rows = c.resolve_port_interfaces(
            blueprint_id, device=p.get("system"), port=p.get("port"))
        if not rows:
            raise ValueError(f"No generic-system-facing port found for {p!r}.")
        resolved.append(rows[0])
    if vn_id and resolved:
        outside = c.ports_outside_vn(blueprint_id, vn_id, resolved)
        if outside:
            raise ValueError(
                f"VN '{vn_id}' is not bound to the switch of: {outside}. Bind the VN to "
                "these switches first (virtual_network update, bound_to / bound_to_tags).")
    ids += [r["ct_interface_id"] for r in resolved]
    ids = list(dict.fromkeys(ids))
    if not ct_id and vn_id:
        cts = c.get_vn_connectivity_templates(blueprint_id, vn_id)
        if len(cts) != 1:
            raise ValueError(
                f"Expected exactly one CT on VN '{vn_id}', found {len(cts)}: {cts}. Pass ct_id.")
        ct_id = cts[0]["id"]
    if not ct_id:
        raise ValueError("'ct_id' (or 'vn_id') is required.")
    if not ids:
        raise ValueError("'interface_ids' (or 'ports') is required.")
    return _attach_tag_info(
        c.apply_ct_to_interfaces(blueprint_id, ct_id, ids), tag_info, "tag_resolution")

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
