"""Live telemetry: BGP peering state and fabric health."""

import ipaddress
import requests
from typing import Any, Optional


class TelemetryMixin:
    # ── BGP peering state (UC#3) ──────────────────────────────────────

    def get_bgp_status(
        self,
        blueprint_id: str | None = None,
        system: str | None = None,
        state: str | None = None,
    ) -> dict:
        """State of all the fabric's BGP peerings (real-time telemetry).

        For each BGP session: source device, neighbor, source/neighbor ASN,
        VRF, address family, state (up/down), expected state, state machine
        (fsm_state), flap count and timestamp of the last change (uptime
        proxy). The received/advertised prefix counters are not exposed
        by Apstra telemetry and are therefore absent.

        'system' filters by device (label/hostname, case-insensitive
        fragment). 'state' filters by state ('up' or 'down'). 'blueprint_id'
        omitted -> all blueprints are traversed.
        """
        sys_q = system.lower() if system else None
        state_q = state.lower() if state else None

        if blueprint_id:
            blueprints = [blueprint_id]
        else:
            blueprints = [b["id"] for b in self.list_blueprints() if b.get("id")]

        per_bp: list[dict] = []
        all_sessions: list[dict] = []

        for bp in blueprints:
            serial_map = self._serial_to_system(bp)
            sessions: list[dict] = []
            for serial, info in serial_map.items():
                if info.get("system_type") != "switch":
                    continue
                try:
                    data = self._get(f"/systems/{serial}/services/bgp/data")
                except requests.exceptions.RequestException:
                    continue
                for it in data.get("items", []):
                    ident = it.get("identity", {})
                    sw_label = info.get("system_label") or ident.get("source_hostname")
                    sess = {
                        "switch":          sw_label,
                        "source_hostname": ident.get("source_hostname"),
                        "source_ip":       ident.get("source_ip"),
                        "source_asn":      ident.get("source_asn"),
                        "neighbor":        ident.get("destination_hostname"),
                        "neighbor_ip":     ident.get("destination_ip"),
                        "neighbor_asn":    ident.get("destination_asn"),
                        "vrf_name":        ident.get("vrf_name"),
                        "addr_family":     ident.get("addr_family"),
                        "state":           (it.get("actual") or {}).get("value"),
                        "expected_state":  (it.get("expected") or {}).get("value"),
                        "fsm_state":       it.get("fsm_state"),
                        "flap_count":      it.get("flap_count"),
                        "status":          it.get("status"),
                        "last_change":     it.get("last_modified_at"),
                    }
                    if sys_q and sys_q not in (
                        f"{sess['switch'] or ''} {sess['source_hostname'] or ''} "
                        f"{sess['neighbor'] or ''}"
                    ).lower():
                        continue
                    if state_q and (sess["state"] or "").lower() != state_q:
                        continue
                    sessions.append(sess)

            up = sum(1 for s in sessions if (s["state"] or "").lower() == "up")
            down = sum(1 for s in sessions if (s["state"] or "").lower() == "down")
            sessions.sort(key=lambda s: ((s["state"] or "") != "down",
                                         s["switch"] or "", s["neighbor_ip"] or ""))
            per_bp.append({
                "blueprint_id": bp,
                "total": len(sessions),
                "up": up,
                "down": down,
                "sessions": sessions,
            })
            all_sessions.extend(sessions)

        total = len(all_sessions)
        up = sum(1 for s in all_sessions if (s["state"] or "").lower() == "up")
        down = sum(1 for s in all_sessions if (s["state"] or "").lower() == "down")
        return {
            "blueprint_id": blueprint_id,
            "blueprints_searched": blueprints,
            "filter": {"system": system, "state": state},
            "summary": {"total": total, "up": up, "down": down},
            "note": (
                "Received/advertised prefixes not exposed by Apstra telemetry; "
                "'last_change' serves as an uptime proxy (last state change)."
            ),
            "blueprints": per_bp,
        }

    # ── Fabric health state (UC#5) ─────────────────────────────────

    def get_fabric_health(self, blueprint_id: str | None = None) -> dict:
        """Fabric health state: spine/leaf links, interfaces, alerts.

        For each blueprint: state of the fabric links (spine<->leaf, up/down via
        the interface telemetry), interfaces in error (operationally down
        or in mismatch), interfaces with non-zero error counters
        (rx/tx errors, FCS, alignment, symbol, runts, giants) and active alerts
        (anomalies grouped by type). 'blueprint_id' omitted -> all
        blueprints are traversed.
        """
        if blueprint_id:
            blueprints = [blueprint_id]
        else:
            blueprints = [b["id"] for b in self.list_blueprints() if b.get("id")]

        _err_fields = (
            "rx_error_packets", "tx_error_packets", "rx_discard_packets",
            "tx_discard_packets", "alignment_errors", "fcs_errors",
            "symbol_errors", "runts", "giants",
        )

        per_bp: list[dict] = []
        for bp in blueprints:
            serial_map = self._serial_to_system(bp)

            fabric_links: list[dict] = []
            ifaces_in_error: list[dict] = []
            ifaces_with_errors: list[dict] = []

            for serial, info in serial_map.items():
                if info.get("system_type") != "switch":
                    continue
                sw_label = info.get("system_label") or serial

                # Interface state (up/down) + spine_leaf role
                try:
                    idata = self._get(f"/systems/{serial}/services/interface/data")
                except requests.exceptions.RequestException:
                    idata = {"items": []}
                for it in idata.get("items", []):
                    name = (it.get("identity") or {}).get("interface_name")
                    actual = (it.get("actual") or {}).get("value")
                    expected = (it.get("expected") or {}).get("value")
                    role = it.get("role")
                    status = it.get("status")
                    # Fabric links: physical spine<->leaf interfaces only
                    if role == "spine_leaf" and name and "." not in name:
                        fabric_links.append({
                            "switch": sw_label, "interface": name,
                            "state": actual, "status": status,
                        })
                    # In error only if the real state diverges from the expected
                    if (status and status.lower() == "mismatch") or \
                       (actual and expected and actual.lower() != expected.lower()):
                        ifaces_in_error.append({
                            "switch": sw_label, "interface": name,
                            "state": actual, "expected": expected,
                            "role": role, "status": status,
                        })

                # Error counters
                try:
                    cdata = self._get(
                        f"/systems/{serial}/services/interface_counters/data")
                except requests.exceptions.RequestException:
                    cdata = {"items": []}
                for it in cdata.get("items", []):
                    errs = {f: it.get(f, 0) for f in _err_fields if it.get(f, 0)}
                    if errs:
                        ifaces_with_errors.append({
                            "switch": sw_label,
                            "interface": it.get("interface_name"),
                            "errors": errs,
                        })

            # Active alerts (anomalies)
            try:
                anomalies = self.get_blueprint_anomalies(bp)
            except requests.exceptions.RequestException:
                anomalies = []
            anomaly_by_type: dict[str, int] = {}
            for a in anomalies:
                t = a.get("anomaly_type", "unknown")
                anomaly_by_type[t] = anomaly_by_type.get(t, 0) + 1

            links_up = sum(1 for l in fabric_links if (l["state"] or "").lower() == "up")
            links_down = sum(1 for l in fabric_links if (l["state"] or "").lower() == "down")

            if links_down or ifaces_in_error or anomaly_by_type.get("bgp"):
                verdict = "critical" if (links_down or anomaly_by_type.get("bgp")) else "degraded"
            elif ifaces_with_errors or anomalies:
                verdict = "degraded"
            else:
                verdict = "healthy"

            per_bp.append({
                "blueprint_id": bp,
                "health": verdict,
                "fabric_links": {
                    "total": len(fabric_links),
                    "up": links_up,
                    "down": links_down,
                    "links": sorted(fabric_links,
                                    key=lambda l: ((l["state"] or "") != "down",
                                                   l["switch"] or "", l["interface"] or "")),
                },
                "interfaces_in_error": ifaces_in_error,
                "interfaces_with_error_counters": ifaces_with_errors,
                "active_alerts": {
                    "total": len(anomalies),
                    "by_type": anomaly_by_type,
                },
            })

        return {
            "blueprint_id": blueprint_id,
            "blueprints_searched": blueprints,
            "blueprints": per_bp,
        }

