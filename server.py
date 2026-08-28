"""Apstra MCP server."""

import json
import logging
import os
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import apstra_auth
from apstra_client import ApstraClient

load_dotenv()

logging.basicConfig(level=os.environ.get("APSTRA_LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("hpe-apstra-mcp")

# ── Initialisation ────────────────────────────────────────────────────

_host = os.environ.get("MCP_HOST", "0.0.0.0")
_port = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("hpe-apstra-mcp", host=_host, port=_port)


# ── Security: optional Bearer authentication ──────────────────────────
# Disabled by default (backward-compatible). Enable via APSTRA_AUTH_ENABLED=true
# (typically in docker-compose.yml) after creating at least one token with
# `apstra_token_manager.py generate --name <client>`.


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


_AUTH_ENABLED = _env_bool("APSTRA_AUTH_ENABLED", False)
_TOKENS_FILE = os.environ.get("APSTRA_TOKENS_FILE", apstra_auth.DEFAULT_TOKENS_FILE)
_MCP_PATH = os.environ.get("APSTRA_MCP_PATH", "/mcp")
_TRUST_FORWARDED = _env_bool("APSTRA_TRUST_FORWARDED_FOR", False)

# Built lazily at startup (see __main__).
_token_store: "apstra_auth.TokenStore | None" = None


def _init_security() -> None:
    """Build the token store and apply the startup rules.

    Policy: if Bearer auth is enabled but no token exists yet, the server still
    starts but in LOCKED mode — every MCP request is refused (HTTP 503) until a
    token is created. This makes it possible to generate the first token without
    having to disable authentication; a restart then activates it.
    """
    global _token_store

    if not _AUTH_ENABLED:
        logger.warning(
            "🔓 Bearer authentication is DISABLED (APSTRA_AUTH_ENABLED not set). "
            "The MCP endpoint is open to any client that can reach it."
        )
        return

    _token_store = apstra_auth.TokenStore(_TOKENS_FILE)
    if len(_token_store) == 0:
        logger.warning(
            "🔒 APSTRA_AUTH_ENABLED=true but no token found in '%s'. "
            "Starting in LOCKED mode: all MCP requests are refused "
            "(HTTP 503) until a token exists. Create the first one with: "
            "docker compose exec hpe-apstra-mcp python apstra_token_manager.py "
            "generate --name <client> — then RESTART the container.",
            _TOKENS_FILE,
        )
    else:
        logger.info(
            "🔒 Bearer authentication ENABLED — %d token(s) loaded from %s",
            len(_token_store), _TOKENS_FILE,
        )


_apstra_client: ApstraClient | None = None

def _client() -> ApstraClient:
    global _apstra_client
    if _apstra_client is None:
        host       = os.environ["APSTRA_HOST"]
        username   = os.environ["APSTRA_USERNAME"]
        password   = os.environ["APSTRA_PASSWORD"]
        verify_ssl = os.environ.get("APSTRA_VERIFY_SSL", "false").lower() == "true"
        _apstra_client = ApstraClient(host, username, password, verify_ssl)
    return _apstra_client


# ══════════════════════════════════════════════════════════════════════
# TOOLS — Version & Systems
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def get_version() -> dict:
    """Apstra version."""
    return _client().get_version()

@mcp.tool()
def list_systems() -> list:
    """List devices."""
    return _client().list_systems()

@mcp.tool()
def get_system(system_id: str) -> dict:
    """Device detail."""
    return _client().get_system(system_id)

@mcp.tool()
def list_agents() -> list:
    """List agents."""
    return _client().list_agents()


# ══════════════════════════════════════════════════════════════════════
# TOOLS — Blueprints
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_blueprints() -> list:
    """List blueprints."""
    return _client().list_blueprints()

@mcp.tool()
def create_blueprint(label: str, template_id: str, init_type: str = "template_reference") -> dict:
    """Create blueprint."""
    return _client().create_blueprint(label, template_id, init_type)

@mcp.tool()
def get_blueprint_anomalies(blueprint_id: str) -> dict:
    """Blueprint anomalies."""
    return _client().get_blueprint_anomalies(blueprint_id)

@mcp.tool()
def get_blueprint_build_errors(blueprint_id: str) -> dict:
    """Build (staging) errors of a blueprint = Uncommitted > Build Errors tab.

    DO NOT confuse with get_blueprint_anomalies (runtime telemetry).
    Returns errors_count/warnings_count and the list of errors (message,
    error_type, category, severity, suggested resolutions).
    """
    return _client().get_blueprint_build_errors(blueprint_id)

@mcp.tool()
def get_blueprint_logical_diff(blueprint_id: str) -> dict:
    """Logical diff (staging) of a blueprint = Uncommitted > Logical Diff tab.

    Lists the uncommitted changes (type, action added/removed/changed, name)
    and a digest (number of nodes/relationships added/removed/changed).
    """
    return _client().get_blueprint_logical_diff(blueprint_id)

@mcp.tool()
def get_blueprint_nodes(blueprint_id: str, node_type: str = None) -> dict:
    """Blueprint nodes."""
    return _client().get_blueprint_nodes(blueprint_id, node_type)

@mcp.tool()
def check_blueprint_commit(blueprint_id: str) -> dict:
    """Check commit."""
    return _client().check_blueprint_commit(blueprint_id)

@mcp.tool()
def commit_blueprint(blueprint_id: str, description: str = "") -> dict:
    """Commit (deploy) the staging changes of a blueprint.

    Automatically reads the current staging version and triggers the
    deployment (PUT /deploy). Deployment is asynchronous: convergence toward
    the devices is handled by Apstra.

    MANDATORY CONFIRMATION: before calling this tool, ASK the user
    "The change is about to be committed — are you sure?". If NO, do not commit
    and provide a summary of the changes left in staging.
    """
    return _client().commit_blueprint(blueprint_id, description)


# ══════════════════════════════════════════════════════════════════════
# TOOLS — Virtual Networks
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_virtual_networks(blueprint_id: str) -> list:
    """List VNs."""
    return _client().list_virtual_networks(blueprint_id)

@mcp.tool()
def get_virtual_network(blueprint_id: str, vn_id: str) -> dict:
    """VN detail."""
    return _client().get_virtual_network(blueprint_id, vn_id)

@mcp.tool()
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
def apply_ct_to_interfaces(
    blueprint_id: str,
    ct_id: str,
    interface_ids: list,
) -> dict:
    """Apply CT."""
    return _client().apply_ct_to_interfaces(blueprint_id, ct_id, interface_ids)

@mcp.tool()
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


# ══════════════════════════════════════════════════════════════════════
# TOOLS — Security / Routing Zones
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_security_zones(blueprint_id: str) -> list:
    """List SZs."""
    return _client().list_security_zones(blueprint_id)

@mcp.tool()
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


# ══════════════════════════════════════════════════════════════════════
# TOOLS — Generic Systems
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
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


# ══════════════════════════════════════════════════════════════════════
# TOOLS — Topology / Switch properties
# ══════════════════════════════════════════════════════════════════════

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


@mcp.tool()
def get_fabric_matrix(
    rack: str = None,
    blueprint_id: str = None,
) -> dict:
    """Hierarchical cabling matrix endpoint -> port -> leaf -> spine.

    Returns the topology organized by rack then by leaf: uplinks of each leaf
    to the spines (leaf port -> spine:port) and endpoints (generic systems)
    connected with their leaf port. Also provides 'rows', a flat list usable as
    a table (endpoint, leaf, port, spines).

    'rack' filters on a rack (exact name or fragment, case-insensitive): omit
    for the full matrix, provide a name for a single rack.
    'blueprint_id' omitted -> all blueprints are searched.
    """
    return _client().get_fabric_matrix(rack=rack, blueprint_id=blueprint_id)


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


@mcp.tool()
def cabling_matrix(
    blueprint_id: str = None,
    rack: str = None,
    role_filter: str = None,
) -> dict:
    """Cabling matrix of a blueprint via /cabling-map (/cabling_matrix).

    Each physical link is normalized into an oriented A -> B row: local fabric
    switch (leaf/border, spine, superspine) as A, external endpoint
    (server/generic/remote DC) as B, with role, port, IP and operational state
    of each side, plus a category (leaf-spine, endpoint-leaf,
    spine-superspine). Provides 'links' (usable list) and 'by_category'.

    - 'blueprint_id' omitted: all blueprints are searched.
    - 'rack': keep only links with one endpoint in this rack
      (exact name or fragment, case-insensitive).
    - 'role_filter': keep only one category of links (e.g. 'leaf-spine',
      'endpoint-leaf', 'spine-superspine').
    """
    return _client().cabling_matrix(
        blueprint_id=blueprint_id, rack=rack, role_filter=role_filter)


@mcp.tool()
def get_bgp_status(
    system: str = None,
    state: str = None,
    blueprint_id: str = None,
) -> dict:
    """State of all BGP peerings in the fabric (real-time telemetry).

    For each session: source device, neighbor, source/neighbor ASN, VRF,
    address family, state (up/down), expected state, state machine (fsm_state),
    flap count and timestamp of the last change (uptime proxy). Provides an
    up/down summary. Received/advertised prefix counters are not exposed by
    Apstra telemetry.

    'system' filters by device (label/hostname, case-insensitive fragment).
    'state' filters by state ('up' or 'down'). 'blueprint_id' omitted ->
    all blueprints are searched.
    """
    return _client().get_bgp_status(
        blueprint_id=blueprint_id, system=system, state=state)


@mcp.tool()
def get_fabric_health(blueprint_id: str = None) -> dict:
    """Fabric health state: spine/leaf links, interfaces, alerts.

    Returns, per blueprint: the state of fabric spine<->leaf links (up/down),
    interfaces in error (operationally down or in mismatch), interfaces with
    non-zero error counters (rx/tx errors, FCS, alignment, symbol, runts,
    giants) and active alerts (anomalies by type), with a global verdict
    (healthy / degraded / critical).

    'blueprint_id' omitted -> all blueprints are searched.
    """
    return _client().get_fabric_health(blueprint_id=blueprint_id)


@mcp.tool()
def list_blueprint_revisions(blueprint_id: str, limit: int = 20) -> dict:
    """List the revisions (restore points) of a blueprint.

    Each revision is a committed config version to which you can roll back
    without CLI (see rollback_blueprint). Sorted from the most recent to the
    oldest; 'limit' bounds the result (0 = all).
    """
    return _client().list_blueprint_revisions(
        blueprint_id=blueprint_id, limit=limit)


@mcp.tool()
def rollback_blueprint(blueprint_id: str, revision_id: str) -> dict:
    """Restore the blueprint to a previous revision (without CLI).

    Triggers a configuration rollback via the Apstra API to the given
    'revision_id' (must be eligible: see list_blueprint_revisions).
    Convergence toward the devices is handled by the Apstra deployer
    (minimum duration set by the vendor). Impactful operation: use only
    after validation.
    """
    return _client().rollback_blueprint(
        blueprint_id=blueprint_id, revision_id=revision_id)


@mcp.tool()
def revert_staging(blueprint_id: str, confirmed: bool = False) -> dict:
    """Revert the UNCOMMITTED staging changes of a blueprint.

    Restores staging to the last committed/deployed version: all uncommitted
    changes are discarded (equivalent to the 'Revert' button of the Apstra UI).
    Can be triggered on simple user request.

    CONFIRMATION LOCK (DESTRUCTIVE operation): if confirmed=False, the tool
    does NOTHING and returns status 'confirmation_required' with the question
    "Do you want to discard the change and trigger a revert?". Ask the
    question, then:
      - if YES: call again with confirmed=True (the revert is executed);
      - if NO: do nothing; the changes remain in staging.
    If there are no staging changes, returns 'nothing_to_revert'.
    """
    return _client().revert_staging(
        blueprint_id=blueprint_id, confirmed=confirmed)



@mcp.tool()
def prepare_vlan(blueprint_id: str, leaf: str, port: str = None) -> dict:
    """Pre-flight to call BEFORE add_vlan_to_port to prepare the questionnaire.

    Returns the context needed to ask ALL missing questions in one go
    (without causing an error):
      - 'is_esi' / 'vxlan_required': is the leaf in an ESI pair (=> VN forced to
        vxlan, so Routing Zone MANDATORY);
      - 'routing_zone_required' + 'routing_zones': required VRF and list of
        selectable VRFs;
      - 'port_state': does the port exist or will it be instantiated;
      - 'questions_to_ask': the exact list of questions to ask the user.

    Expected workflow: call prepare_vlan -> ask IN ONE GO all the questions in
    'questions_to_ask' (tagging, IPv4, Routing Zone where applicable)
    -> gather the answers -> call add_vlan_to_port with all the parameters.
    Do NOT call add_vlan_to_port before you have all the answers.
    """
    return _client().add_vlan_preflight(blueprint_id, leaf, port)


@mcp.tool()
def add_vlan_to_port(
    blueprint_id: str,
    leaf: str,
    vlan_id: int,
    port: str = None,
    tagging: str = None,
    label: str = None,
    vn_type: str = "vlan",
    security_zone_id: str = None,
    vni: int = None,
    l2_vni: int = None,
    ipv4_subnet: str = None,
    virtual_gateway_ipv4: str = None,
    dhcp_relay: bool = False,
    instantiate_port: bool = True,
    gs_label: str = None,
    commit: bool = False,
    commit_confirmed: bool = False,
) -> dict:
    """Create a VLAN (Virtual Network) on a leaf and assign it to a port.

    Creates a Virtual Network local to the leaf (no impact on the other leafs).
    When a 'port' and a 'tagging' mode are provided, Apstra AUTO-CREATES the
    Connectivity Template that connects the port to the VLAN: this server NEVER
    creates a CT manually. The commit (push to the device) only happens if
    commit=True.

    MANDATORY WORKFLOW:
      STEP 0 — First call prepare_vlan(blueprint_id, leaf, port) to know the
      context (ESI? required VRF? available VRFs? port state) and the exact
      list of questions to ask.

      STEP 1 — Ask the user, IN ONE GO, ALL the missing questions (do not ask
      them one by one, do not rely on the tool errors):
        a. "Should the VLAN be tagged (802.1Q) or untagged (native) on the
           port?" -> 'tagging'. If neither: 'tagging' empty => NO CT, port NOT
           connected (inform the user).
        b. "Do you want IPv4 connectivity? If so, which subnet and which
           virtual gateway?" -> 'ipv4_subnet' (+ 'virtual_gateway_ipv4'
           optional, default 1st usable address). If not, leave empty.
        c. "In which Routing Zone (VRF) should this VLAN be placed?" -> 'security_zone_id'
           (VRF id or name). To ask as soon as prepare_vlan indicates
           routing_zone_required=true (ESI leaf) OR if the user requested
           IPv4/DHCP connectivity or an L2VNI.
      Only call add_vlan_to_port AFTER collecting all the answers.

      STEP 2 — Before any commit: ASK for explicit confirmation
      "The change is about to be committed — are you sure?". If YES: call with
      commit=True AND commit_confirmed=True. If NO: do NOT commit, then ask
      the question "Do you want to discard the change and trigger a revert?";
      if YES -> call revert_staging(confirmed=True); if NO -> do nothing
      (the VN stays in staging) and provide a short summary. The commit is
      LOCKED: with commit=True but commit_confirmed=False, the tool does not
      commit and returns 'confirmation_required' — that is the signal to
      ask the question.

    'tagging': 'tagged' (802.1Q) or 'untagged' (native VLAN) on the port; None =
    no CT (port not connected). Options: 'l2_vni' (forces vxlan), 'dhcp_relay'.
    On an ESI leaf or with an L3/L2VNI option, the VN switches to 'vxlan'
    automatically and REQUIRES a Routing Zone (VRF) via 'security_zone_id'.

    If the 'port' is unused (no interface in the graph), it is instantiated
    automatically when 'instantiate_port' is true (default) before assignment.

    'leaf': switch id/label. 'vlan_id': 1-4094. 'port': e.g. 'xe-0/0/0'.
    'security_zone_id': VRF (Routing Zone) for a vxlan VN (id or name).
    'commit': False by default (staging).

    COMMIT LOCK: if commit=True but commit_confirmed stays False, the tool
    does NOT commit and returns a step status 'confirmation_required' with the
    question to ask. You must then ask the user "The change is about to be
    committed — are you sure?"; if YES, call again with commit=True AND
    commit_confirmed=True; if NO, do nothing more and summarize.
    """
    return _client().add_vlan_to_port(
        blueprint_id=blueprint_id, leaf=leaf, vlan_id=vlan_id, port=port,
        tagging=tagging, label=label, vn_type=vn_type,
        security_zone_id=security_zone_id, vni=vni, l2_vni=l2_vni,
        ipv4_subnet=ipv4_subnet, virtual_gateway_ipv4=virtual_gateway_ipv4,
        dhcp_relay=dhcp_relay, instantiate_port=instantiate_port,
        gs_label=gs_label, commit=commit, commit_confirmed=commit_confirmed)


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


# ══════════════════════════════════════════════════════════════════════
# TOOLS — Resources
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_asn_pools() -> list:
    """List ASN pools."""
    return _client().list_asn_pools()

@mcp.tool()
def list_ip_pools() -> list:
    """List IP pools."""
    return _client().list_ip_pools()

@mcp.tool()
def list_vni_pools() -> list:
    """List VNI pools."""
    return _client().list_vni_pools()


# ══════════════════════════════════════════════════════════════════════
# TOOLS — Design
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_logical_devices() -> list:
    """List logical devices."""
    return _client().list_logical_devices()

@mcp.tool()
def list_interface_maps() -> list:
    """List interface maps."""
    return _client().list_interface_maps()

@mcp.tool()
def list_rack_types() -> list:
    """List rack types."""
    return _client().list_rack_types()

@mcp.tool()
def list_templates() -> list:
    """List templates."""
    return _client().list_templates()


# ══════════════════════════════════════════════════════════════════════
# TOOLS — Configlets
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_configlets() -> list:
    """List configlets."""
    return _client().list_configlets()

@mcp.tool()
def get_configlet(configlet_id: str) -> dict:
    """Configlet detail."""
    return _client().get_configlet(configlet_id)

@mcp.tool()
def list_blueprint_configlets(blueprint_id: str) -> list:
    """Blueprint configlets."""
    return _client().list_blueprint_configlets(blueprint_id)

@mcp.tool()
def get_blueprint_configlet(blueprint_id: str, configlet_id: str) -> dict:
    """Blueprint configlet detail."""
    return _client().get_blueprint_configlet(blueprint_id, configlet_id)


# ══════════════════════════════════════════════════════════════════════
# TOOLS — Property Sets
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_property_sets() -> list:
    """List property sets."""
    return _client().list_property_sets()

@mcp.tool()
def get_property_set(property_set_id: str) -> dict:
    """Property set detail."""
    return _client().get_property_set(property_set_id)

@mcp.tool()
def list_blueprint_property_sets(blueprint_id: str) -> list:
    """Blueprint property sets."""
    return _client().list_blueprint_property_sets(blueprint_id)

@mcp.tool()
def get_blueprint_property_set(blueprint_id: str, property_set_id: str) -> dict:
    """Blueprint property set detail."""
    return _client().get_blueprint_property_set(blueprint_id, property_set_id)


# ══════════════════════════════════════════════════════════════════════
# TOOLS — Tasks
# ══════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_tasks(blueprint_id: str = None) -> list:
    """List tasks."""
    return _client().list_tasks(blueprint_id=blueprint_id)

@mcp.tool()
def get_task(task_id: str, blueprint_id: str = None) -> dict:
    """Task detail."""
    return _client().get_task(task_id=task_id, blueprint_id=blueprint_id)


# ══════════════════════════════════════════════════════════════════════
# PROMPTS — reusable Apstra operations templates
# ══════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════
# ENTRY POINT — stdio or SSE depending on MCP_TRANSPORT
# ══════════════════════════════════════════════════════════════════════

class _SecurityMiddleware:
    """Optional Bearer authentication for the MCP endpoint (ASGI).

    When authentication is disabled, this is a zero-cost pass-through. Only the
    MCP path (``/mcp`` by default) is protected; any other path is left
    untouched.
    """

    def __init__(self, app, *, auth_enabled, token_store, mcp_path, trust_forwarded):
        self._app = app
        self._auth_enabled = auth_enabled
        self._token_store = token_store
        self._mcp_path = mcp_path
        self._trust_forwarded = trust_forwarded

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self._auth_enabled:
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith(self._mcp_path):
            await self._app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        src_ip = self._client_ip(scope, headers)

        # LOCKED mode: auth required but no token exists yet.
        if self._token_store is None or len(self._token_store) == 0:
            logger.warning("🔒 MCP request refused — server LOCKED (no "
                           "token configured) from %s %s", src_ip, path)
            await self._send_503_locked(send)
            return

        actor = self._resolve_actor(headers)
        if actor is None:
            logger.warning("🚫 Unauthenticated request rejected from %s %s", src_ip, path)
            await self._send_401(send)
            return

        await self._app(scope, receive, send)

    def _resolve_actor(self, headers: dict) -> "str | None":
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[7:].strip()
        return self._token_store.resolve(token)

    def _client_ip(self, scope, headers: dict) -> str:
        if self._trust_forwarded:
            xff = headers.get("x-forwarded-for")
            if xff:
                return xff.split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    @staticmethod
    async def _send_401(send) -> None:
        payload = json.dumps({"error": "Missing or invalid bearer token"}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": payload})

    @staticmethod
    async def _send_503_locked(send) -> None:
        payload = json.dumps({
            "error": "Service locked: authentication is enabled but no token is "
                     "configured. Create the first token with "
                     "`docker compose exec hpe-apstra-mcp python apstra_token_manager.py "
                     "generate --name <client>`, then restart the container.",
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                (b"retry-after", b"0"),
            ],
        })
        await send({"type": "http.response.body", "body": payload})


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http").lower()
    _init_security()

    # Bearer authentication only makes sense on the network HTTP transport.
    if _AUTH_ENABLED and transport == "streamable-http":
        import uvicorn

        app = _SecurityMiddleware(
            mcp.streamable_http_app(),
            auth_enabled=_AUTH_ENABLED,
            token_store=_token_store,
            mcp_path=_MCP_PATH,
            trust_forwarded=_TRUST_FORWARDED,
        )
        uvicorn.run(app, host=_host, port=_port)
    else:
        mcp.run(transport=transport)