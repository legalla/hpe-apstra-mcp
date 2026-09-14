"""Flat dispatcher toolset for hpe-apstra-mcp (gated by ``APSTRA_FLAT_TOOLSET``).

Same methodology as cx-mcp's dispatch/flat_tools.py: collapse the 61 atomic
``@mcp.tool()`` functions under tools/ into a small set of scope/action-driven
dispatchers. Dispatchers only ROUTE to the existing tool-module functions
(which already carry the write-safety ``_require_write`` gate where needed) —
zero change to apstra_client/ business logic.

Activation is gated by ``APSTRA_FLAT_TOOLSET`` (default OFF, safe rollback):

    * ``APSTRA_FLAT_TOOLSET=true``  -> legacy tools are de-advertised and the
      12 flat dispatchers below are advertised instead.
    * ``APSTRA_FLAT_TOOLSET=false`` (default) -> no-op, server unchanged.

Call ``install_flat_toolset(mcp)`` once, at the very end of server.py, after
every tools/* module has been imported (so their @mcp.tool() registrations
already exist and can be de-advertised).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from tools import (
    blueprints, cabling, catalog, endpoints, networks, ports, revisions,
    systems, telemetry, topology, version_systems,
)
from tools import locate as locate_tools
from tools import vlan as vlan_tools

log = logging.getLogger("hpe-apstra-mcp.flat")


def _env_true(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# ──────────────────────────────────────────────────────────────────────
# FastMCP registry accessors (version-defensive, same as cx-mcp)
# ──────────────────────────────────────────────────────────────────────
def _tool_manager(mcp: Any):
    return getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)


def _registry(mcp: Any) -> Optional[dict]:
    mgr = _tool_manager(mcp)
    if mgr is None:
        return None
    for attr in ("_tools", "tools"):
        d = getattr(mgr, attr, None)
        if isinstance(d, dict):
            return d
    return None


def _err(dispatcher: str, message: str, valid: "list[str] | None" = None) -> dict:
    out = {"error": f"[{dispatcher}] {message}"}
    if valid is not None:
        out["valid_scopes"] = valid
    return out


# ──────────────────────────────────────────────────────────────────────
# Read dispatchers
# ──────────────────────────────────────────────────────────────────────
def list_catalog(scope: str, id: str = None, blueprint_id: str = None) -> "list | dict":
    """Design catalog & reference data. `scope`:
      asn_pools | ip_pools | vni_pools | logical_devices | interface_maps |
      rack_types | templates | configlets | configlet | blueprint_configlets |
      blueprint_configlet | property_sets | property_set |
      blueprint_property_sets | blueprint_property_set | tasks | task
    `id` = configlet_id / property_set_id / task_id (single-item scopes).
    `blueprint_id` required for the blueprint_-prefixed and task/tasks scopes."""
    if scope == "asn_pools":
        return catalog.list_asn_pools()
    if scope == "ip_pools":
        return catalog.list_ip_pools()
    if scope == "vni_pools":
        return catalog.list_vni_pools()
    if scope == "logical_devices":
        return catalog.list_logical_devices()
    if scope == "interface_maps":
        return catalog.list_interface_maps()
    if scope == "rack_types":
        return catalog.list_rack_types()
    if scope == "templates":
        return catalog.list_templates()
    if scope == "configlets":
        return catalog.list_configlets()
    if scope == "configlet":
        return catalog.get_configlet(id)
    if scope == "blueprint_configlets":
        return catalog.list_blueprint_configlets(blueprint_id)
    if scope == "blueprint_configlet":
        return catalog.get_blueprint_configlet(blueprint_id, id)
    if scope == "property_sets":
        return catalog.list_property_sets()
    if scope == "property_set":
        return catalog.get_property_set(id)
    if scope == "blueprint_property_sets":
        return catalog.list_blueprint_property_sets(blueprint_id)
    if scope == "blueprint_property_set":
        return catalog.get_blueprint_property_set(blueprint_id, id)
    if scope == "tasks":
        return catalog.list_tasks(blueprint_id=blueprint_id)
    if scope == "task":
        return catalog.get_task(task_id=id, blueprint_id=blueprint_id)
    return _err("list_catalog", f"unknown scope '{scope}'", [
        "asn_pools", "ip_pools", "vni_pools", "logical_devices", "interface_maps",
        "rack_types", "templates", "configlets", "configlet",
        "blueprint_configlets", "blueprint_configlet", "property_sets",
        "property_set", "blueprint_property_sets", "blueprint_property_set",
        "tasks", "task",
    ])


def get_blueprint(scope: str, blueprint_id: str = None, node_type: str = None) -> "list | dict":
    """Blueprint inspection (read-only). `scope`:
      list | anomalies | build_errors | logical_diff | nodes | check_commit
    `blueprint_id` required except for `list`. `nodes` accepts `node_type`."""
    if scope == "list":
        return blueprints.list_blueprints()
    if not blueprint_id:
        return _err("get_blueprint", f"scope '{scope}' requires `blueprint_id`.")
    if scope == "anomalies":
        return blueprints.get_blueprint_anomalies(blueprint_id)
    if scope == "build_errors":
        return blueprints.get_blueprint_build_errors(blueprint_id)
    if scope == "logical_diff":
        return blueprints.get_blueprint_logical_diff(blueprint_id)
    if scope == "nodes":
        return blueprints.get_blueprint_nodes(blueprint_id, node_type)
    if scope == "check_commit":
        return blueprints.check_blueprint_commit(blueprint_id)
    return _err("get_blueprint", f"unknown scope '{scope}'",
                ["list", "anomalies", "build_errors", "logical_diff", "nodes", "check_commit"])


def get_topology(scope: str, blueprint_id: str = None, switch_id: str = None,
                  switch_id_a: str = None, switch_id_b: str = None,
                  switch_if: str = None, device: str = None, port: str = None) -> "list | dict":
    """Physical/logical switch topology (read-only). `scope`:
      switch_properties | switch_uplinks | link_ips | switch_loopbacks |
      generic_systems | generic_system_port | ports
    `link_ips` needs `switch_id_a`+`switch_id_b`; `generic_system_port` needs
    `switch_id`+`switch_if`; `ports` uses optional `device`/`port` filters."""
    if not blueprint_id:
        return _err("get_topology", f"scope '{scope}' requires `blueprint_id`.")
    if scope == "switch_properties":
        return topology.get_switch_properties(blueprint_id, switch_id)
    if scope == "switch_uplinks":
        return topology.get_switch_uplinks(blueprint_id, switch_id)
    if scope == "link_ips":
        return topology.get_link_ips(blueprint_id, switch_id_a, switch_id_b)
    if scope == "switch_loopbacks":
        return topology.get_switch_loopbacks(blueprint_id, switch_id)
    if scope == "generic_systems":
        return systems.list_generic_systems_on_switch(blueprint_id, switch_id)
    if scope == "generic_system_port":
        return systems.get_generic_system_on_port(blueprint_id, switch_id, switch_if)
    if scope == "ports":
        return ports.list_ports(blueprint_id, device, port)
    return _err("get_topology", f"unknown scope '{scope}'",
                ["switch_properties", "switch_uplinks", "link_ips", "switch_loopbacks",
                 "generic_systems", "generic_system_port", "ports"])


def get_cabling(scope: str = "cabling_matrix", blueprint_id: str = None,
                rack: str = None, role_filter: str = None) -> dict:
    """Cabling matrices. `scope`: fabric_matrix (graph-based, hierarchical
    endpoint->port->leaf->spine) | cabling_matrix (/cabling-map based,
    oriented A->B links with category). `rack` filters both; `role_filter`
    only applies to cabling_matrix. `blueprint_id` omitted -> all blueprints."""
    if scope == "fabric_matrix":
        return cabling.get_fabric_matrix(rack=rack, blueprint_id=blueprint_id)
    if scope == "cabling_matrix":
        return cabling.cabling_matrix(blueprint_id=blueprint_id, rack=rack, role_filter=role_filter)
    return _err("get_cabling", f"unknown scope '{scope}'", ["fabric_matrix", "cabling_matrix"])


def get_network(scope: str, blueprint_id: str, vn_id: str = None) -> "list | dict":
    """Virtual networks / zones (read-only). `scope`:
      virtual_networks | virtual_network | redundancy_groups |
      connectivity_templates | security_zones
    `virtual_network` requires `vn_id`."""
    if scope == "virtual_networks":
        return networks.list_virtual_networks(blueprint_id)
    if scope == "virtual_network":
        return networks.get_virtual_network(blueprint_id, vn_id)
    if scope == "redundancy_groups":
        return networks.list_redundancy_groups(blueprint_id)
    if scope == "connectivity_templates":
        return networks.list_connectivity_templates(blueprint_id)
    if scope == "security_zones":
        return networks.list_security_zones(blueprint_id)
    return _err("get_network", f"unknown scope '{scope}'",
                ["virtual_networks", "virtual_network", "redundancy_groups",
                 "connectivity_templates", "security_zones"])


def get_system(scope: str = "list", system_id: str = None) -> "list | dict":
    """Apstra version + physical systems/agents inventory. `scope`:
      version | list | info | agents
    `info` requires `system_id`."""
    if scope == "version":
        return version_systems.get_version()
    if scope == "list":
        return version_systems.list_systems()
    if scope == "info":
        return version_systems.get_system(system_id)
    if scope == "agents":
        return version_systems.list_agents()
    return _err("get_system", f"unknown scope '{scope}'", ["version", "list", "info", "agents"])


def get_telemetry(scope: str, blueprint_id: str = None, system: str = None,
                   state: str = None) -> dict:
    """Live telemetry. `scope`: bgp_status | fabric_health. `bgp_status`
    accepts `system`/`state` filters. `blueprint_id` omitted -> all blueprints."""
    if scope == "bgp_status":
        return telemetry.get_bgp_status(system=system, state=state, blueprint_id=blueprint_id)
    if scope == "fabric_health":
        return telemetry.get_fabric_health(blueprint_id=blueprint_id)
    return _err("get_telemetry", f"unknown scope '{scope}'", ["bgp_status", "fabric_health"])


def locate(scope: str = "probe", mac: str = None, ip: str = None, name: str = None,
           blueprint_id: str = None) -> dict:
    """Endpoint discovery. `scope`:
      probe    : /locate — MAC (fabric MAC table, physical vs VXLAN-remote) or
                 IP (ARP + graph interfaces + EVPN Type-5), needs `mac` or `ip`
      endpoint : find_endpoint — learned ARP/MAC-table/EVPN endpoint search,
                 needs `ip` or `mac`
      vm       : get_vm_info — vCenter/NSX VM inventory, optional `name` filter
    `blueprint_id` omitted -> all blueprints are searched."""
    if scope == "probe":
        return locate_tools.locate(mac=mac, ip=ip, blueprint_id=blueprint_id)
    if scope == "endpoint":
        return endpoints.find_endpoint(ip=ip, mac=mac, name=name, blueprint_id=blueprint_id)
    if scope == "vm":
        return endpoints.get_vm_info(name=name, blueprint_id=blueprint_id)
    return _err("locate", f"unknown scope '{scope}'", ["probe", "endpoint", "vm"])


# ──────────────────────────────────────────────────────────────────────
# Write dispatchers (mutating; routed tool functions already carry
# @_require_write where applicable — the dispatcher itself is not gated).
# ──────────────────────────────────────────────────────────────────────
def configure_blueprint(scope: str, blueprint_id: str = None, label: str = None,
                         template_id: str = None,
                         init_type: str = "template_reference",
                         description: str = "") -> dict:
    """Blueprint lifecycle writes. `scope`:
      create : new blueprint, needs `label`+`template_id`
      commit : deploy staging changes of `blueprint_id` (ASK the user for
               confirmation before calling this)."""
    if scope == "create":
        return blueprints.create_blueprint(label, template_id, init_type)
    if scope == "commit":
        return blueprints.commit_blueprint(blueprint_id, description)
    return _err("configure_blueprint", f"unknown scope '{scope}'", ["create", "commit"])


def manage_revisions(scope: str, blueprint_id: str, revision_id: str = None,
                      limit: int = 20, confirmed: bool = False) -> dict:
    """Blueprint revisions / rollback / staging revert. `scope`:
      list     : read-only, list restore points (`limit` bounds it, 0 = all)
      rollback : restore to `revision_id` (impactful — use after validation)
      revert   : discard UNCOMMITTED staging changes. DESTRUCTIVE — needs
                 `confirmed=True` after asking the user; otherwise returns
                 'confirmation_required'."""
    if scope == "list":
        return revisions.list_blueprint_revisions(blueprint_id=blueprint_id, limit=limit)
    if scope == "rollback":
        return revisions.rollback_blueprint(blueprint_id=blueprint_id, revision_id=revision_id)
    if scope == "revert":
        return revisions.revert_staging(blueprint_id=blueprint_id, confirmed=confirmed)
    return _err("manage_revisions", f"unknown scope '{scope}'", ["list", "rollback", "revert"])


def configure_network(scope: str, action: str, blueprint_id: str,
                       params: dict = None) -> dict:
    """Virtual networks / zones / connectivity templates (writes). `scope`+`action`:
      virtual_network + create       : params={label, vn_type, vn_id,
                                        security_zone_id, ipv4_subnet, ipv4_gateway}
      virtual_network + update       : params={vn_id, bound_to, vni_id,
                                        ipv4_gateway, ipv4_subnet, label}
      virtual_network + delete       : params={vn_id}
      connectivity_template + apply  : params={ct_id, interface_ids}
      vn_dci + enable                : params={vn_id, enable_rt2, enable_rt5}
      security_zone + create         : params={label, vrf_name, vni_id, sz_type}
      sz_dci + enable                : params={sz_id, enable_rt5, enable_irt}
    """
    p = dict(params or {})
    key = (scope, action)
    if key == ("virtual_network", "create"):
        return networks.create_virtual_network(
            blueprint_id, label=p.get("label"), vn_type=p.get("vn_type"),
            vn_id=p.get("vn_id"), security_zone_id=p.get("security_zone_id"),
            ipv4_subnet=p.get("ipv4_subnet"), ipv4_gateway=p.get("ipv4_gateway"))
    if key == ("virtual_network", "update"):
        if not p.get("vn_id"):
            return _err("configure_network", "virtual_network/update requires params.vn_id")
        return networks.update_virtual_network(
            blueprint_id, p["vn_id"], bound_to=p.get("bound_to"),
            vni_id=p.get("vni_id"), ipv4_gateway=p.get("ipv4_gateway"),
            ipv4_subnet=p.get("ipv4_subnet"), label=p.get("label"))
    if key == ("virtual_network", "delete"):
        if not p.get("vn_id"):
            return _err("configure_network", "virtual_network/delete requires params.vn_id")
        return networks.delete_virtual_network(blueprint_id, p["vn_id"])
    if key == ("connectivity_template", "apply"):
        return networks.apply_ct_to_interfaces(
            blueprint_id, ct_id=p.get("ct_id"), interface_ids=p.get("interface_ids"))
    if key == ("vn_dci", "enable"):
        return networks.enable_vn_dci(
            blueprint_id=blueprint_id, vn_id=p.get("vn_id"),
            enable_rt2=p.get("enable_rt2", True), enable_rt5=p.get("enable_rt5", True))
    if key == ("security_zone", "create"):
        return networks.create_security_zone(
            blueprint_id, label=p.get("label"), vrf_name=p.get("vrf_name"),
            vni_id=p.get("vni_id"), sz_type=p.get("sz_type", "evpn"))
    if key == ("sz_dci", "enable"):
        return networks.enable_sz_dci(
            blueprint_id=blueprint_id, sz_id=p.get("sz_id"),
            enable_rt5=p.get("enable_rt5", True), enable_irt=p.get("enable_irt", True))
    return _err("configure_network", f"unknown scope/action '{scope}'/'{action}'", [
        "virtual_network+create", "virtual_network+update", "virtual_network+delete",
        "connectivity_template+apply", "vn_dci+enable", "security_zone+create", "sz_dci+enable",
    ])


def configure_fabric(scope: str, action: str, blueprint_id: str,
                      params: dict = None) -> dict:
    """Generic systems + leaf-port VLAN provisioning (writes). `scope`+`action`:
      generic_system + create : params={label, links, port_speed, lag_mode,
                                asn, loopback_ip, hostname}
      vlan + prepare (read-only preflight, call this FIRST) : params={leaf, port}
      vlan + apply (mutating) : params={leaf, vlan_id, port, tagging, label,
                                vn_type, security_zone_id, vni, l2_vni,
                                ipv4_subnet, virtual_gateway_ipv4, dhcp_relay,
                                instantiate_port, gs_label, commit,
                                commit_confirmed}
    """
    p = dict(params or {})
    key = (scope, action)
    if key == ("generic_system", "create"):
        return systems.create_generic_system(
            blueprint_id=blueprint_id, label=p.get("label"), links=p.get("links"),
            port_speed=p.get("port_speed"), lag_mode=p.get("lag_mode"),
            asn=p.get("asn"), loopback_ip=p.get("loopback_ip"), hostname=p.get("hostname"))
    if key == ("vlan", "prepare"):
        return vlan_tools.prepare_vlan(blueprint_id, leaf=p.get("leaf"), port=p.get("port"))
    if key == ("vlan", "apply"):
        return vlan_tools.add_vlan_to_port(
            blueprint_id, leaf=p.get("leaf"), vlan_id=p.get("vlan_id"),
            port=p.get("port"), tagging=p.get("tagging"), label=p.get("label"),
            vn_type=p.get("vn_type", "vlan"), security_zone_id=p.get("security_zone_id"),
            vni=p.get("vni"), l2_vni=p.get("l2_vni"), ipv4_subnet=p.get("ipv4_subnet"),
            virtual_gateway_ipv4=p.get("virtual_gateway_ipv4"),
            dhcp_relay=p.get("dhcp_relay", False),
            instantiate_port=p.get("instantiate_port", True),
            gs_label=p.get("gs_label"), commit=p.get("commit", False),
            commit_confirmed=p.get("commit_confirmed", False))
    return _err("configure_fabric", f"unknown scope/action '{scope}'/'{action}'", [
        "generic_system+create", "vlan+prepare", "vlan+apply",
    ])


# Ordered list of the flat dispatchers to register.
_DISPATCHERS: "list[Callable]" = [
    list_catalog, get_blueprint, get_topology, get_cabling, get_network,
    get_system, get_telemetry, locate,
    configure_blueprint, manage_revisions, configure_network, configure_fabric,
]


def install_flat_toolset(mcp: Any) -> dict:
    """When ``APSTRA_FLAT_TOOLSET`` is truthy: register the 12 flat dispatchers
    and de-advertise the 61 legacy atomic tools. No-op otherwise (default OFF).

    Never raises (fail-open): on any error the server is left advertising its
    full, unmodified tool set."""
    if not _env_true("APSTRA_FLAT_TOOLSET", "false"):
        return {"active": False, "reason": "APSTRA_FLAT_TOOLSET disabled"}

    reg = _registry(mcp)
    if reg is None:
        log.warning("flat_tools: FastMCP registry not found — server left unchanged")
        return {"active": False, "reason": "registry not found"}

    # 1) Register the flat dispatchers (a legacy tool may share a dispatcher's
    #    name, e.g. get_system/locate — drop the colliding legacy entry first
    #    so FastMCP's tool() doesn't silently keep the old registration).
    new_names: "list[str]" = []
    for fn in _DISPATCHERS:
        try:
            existing = _registry(mcp)
            if existing is not None and fn.__name__ in existing:
                del existing[fn.__name__]
            mcp.tool()(fn)
            new_names.append(fn.__name__)
        except Exception:  # pragma: no cover - one bad tool must not break the rest
            log.exception("flat_tools: failed to register %s", fn.__name__)

    # 2) De-advertise every legacy tool that is not a new dispatcher.
    reg = _registry(mcp) or reg
    keep = set(new_names)
    removed: "list[str]" = []
    for name in list(reg.keys()):
        if name not in keep:
            try:
                del reg[name]
                removed.append(name)
            except Exception:  # pragma: no cover
                log.exception("flat_tools: failed to remove %s", name)

    advertised = sorted(reg.keys())
    summary = {"active": True, "advertised": len(advertised),
               "dispatchers": new_names, "removed": len(removed), "tools": advertised}
    log.info("flat_tools: active — %d tools advertised (%d removed)",
             len(advertised), len(removed))
    return summary
