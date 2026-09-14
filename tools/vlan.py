"""Tools: add a VLAN on a leaf port (UC#1)."""

from core import mcp, _client, _require_write


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
@_require_write
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
