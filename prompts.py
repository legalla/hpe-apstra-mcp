"""Reusable Apstra operation guides exposed as MCP prompts."""

from core import mcp


@mcp.prompt()
def blueprint_health(blueprint_id: str) -> str:
    """Health check of a blueprint (anomalies + commit state)."""
    return (
        f"Produce a health check of blueprint \"{blueprint_id}\".\n\n"
        f"1. List the anomalies with get_blueprint_anomalies('{blueprint_id}') and "
        "group them by type (cabling, BGP, liveness, config, deployment).\n"
        f"2. Check the commit state with check_blueprint_commit('{blueprint_id}'): "
        "are there any uncommitted changes?\n"
        f"3. Inspect the recent tasks via list_tasks('{blueprint_id}') and "
        "flag those that failed or are in progress.\n"
        "4. Summarize the global state (healthy / degraded / critical) with the "
        "most impactful anomalies and the recommended corrective actions."
    )

@mcp.prompt()
def create_virtual_network_guide(
    blueprint_id: str,
    label: str = "",
    security_zone: str = "",
) -> str:
    """Guide the creation of a virtual network (VN) in a blueprint."""
    name = f" named \"{label}\"" if label else ""
    sz = f" in the security zone \"{security_zone}\"" if security_zone else ""
    return (
        f"Help me create a virtual network{name}{sz} in blueprint \"{blueprint_id}\".\n\n"
        f"1. List the existing security zones with list_security_zones('{blueprint_id}') "
        "to choose/validate the target VRF.\n"
        f"2. List the existing VNs with list_virtual_networks('{blueprint_id}') to "
        "avoid VNI/subnet duplicates.\n"
        "3. Ask me for the missing parameters (vlan/vxlan type, VNI, IPv4 subnet, "
        "gateway) then create the VN with create_virtual_network.\n"
        f"4. Check with check_blueprint_commit('{blueprint_id}'), show me the diff, "
        "then propose to commit with commit_blueprint after my validation."
    )

@mcp.prompt()
def verify_fabric(blueprint_id: str) -> str:
    """Verification of the cabling and devices of a fabric."""
    return (
        f"Verify the fabric of blueprint \"{blueprint_id}\".\n\n"
        f"1. Retrieve the switch nodes via get_blueprint_nodes('{blueprint_id}', "
        "node_type='system') and identify spines/leafs.\n"
        "2. For the relevant leafs, check the uplinks with get_switch_uplinks "
        "and the loopbacks with get_switch_loopbacks.\n"
        "3. Check the addressing of the fabric links with get_link_ips between "
        "spine/leaf pairs.\n"
        f"4. Cross-reference with get_blueprint_anomalies('{blueprint_id}') to spot "
        "cabling or BGP underlay anomalies.\n"
        "5. Present a state of the fabric (topology, links, anomalies)."
    )

@mcp.prompt()
def deploy_generic_system(blueprint_id: str, switch_id: str = "") -> str:
    """Guide the connection of a server/device (generic system)."""
    on_switch = f" connected to switch \"{switch_id}\"" if switch_id else ""
    arg_sw = f"'{blueprint_id}', '{switch_id}'" if switch_id else f"'{blueprint_id}', <switch_id>"
    return (
        f"Help me connect a new generic system{on_switch} in "
        f"blueprint \"{blueprint_id}\".\n\n"
        f"1. List the generic systems already present with list_generic_systems_on_switch({arg_sw}) "
        "and check the free ports with get_generic_system_on_port.\n"
        "2. Ask me for the parameters: label, port speed, links (switch/port), "
        "possible LAG mode, ASN/loopback if routed.\n"
        "3. Create the device with create_generic_system.\n"
        "4. If connectivity templates are needed, list them with "
        f"list_connectivity_templates('{blueprint_id}') and apply them via "
        "apply_ct_to_interfaces.\n"
        f"5. Show the diff (check_blueprint_commit) then propose commit_blueprint after validation."
    )

@mcp.prompt()
def configure_dci(blueprint_id: str) -> str:
    """Guide the activation of Data Center Interconnect (DCI)."""
    return (
        f"Help me enable DCI (Data Center Interconnect) on blueprint "
        f"\"{blueprint_id}\".\n\n"
        f"1. List the security zones with list_security_zones('{blueprint_id}') and the "
        f"VNs with list_virtual_networks('{blueprint_id}').\n"
        "2. For the SZs to extend, enable DCI with enable_sz_dci (route-target "
        "IRT/RT5 as needed) and explain the impact to me.\n"
        "3. For the VNs to extend in L2/L3, enable enable_vn_dci (RT2/RT5).\n"
        f"4. Check consistency with get_blueprint_anomalies('{blueprint_id}').\n"
        f"5. Show the diff (check_blueprint_commit), then propose commit_blueprint "
        "after my validation."
    )

@mcp.prompt()
def audit_resources() -> str:
    """Inventory of resource pools and design catalogs."""
    return (
        "Perform an inventory of the Apstra resources and design catalog.\n\n"
        "1. Pools: list_asn_pools, list_ip_pools and list_vni_pools — indicate the "
        "utilization rate and the pools close to exhaustion.\n"
        "2. Design catalog: list_logical_devices, list_interface_maps, "
        "list_rack_types and list_templates.\n"
        "3. Configlets and property sets: list_configlets and list_property_sets.\n"
        "4. Present a clear summary (category, number of objects, points of "
        "attention) and flag the pools to extend."
    )
