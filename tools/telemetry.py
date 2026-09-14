"""Tools: live telemetry (BGP peering state, fabric health)."""

from core import mcp, _client, _require_write


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
