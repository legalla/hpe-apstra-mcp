"""Port listing: status, config, LACP and Connectivity Templates."""

import ipaddress
import requests
from typing import Any, Optional


class PortsMixin:
    # ── Port list (status + config + LACP + CT) ─────────────────────

    @staticmethod
    def _port_sort_key(name: str):
        import re
        prefix_m = re.match(r"^[a-zA-Z]+", name or "")
        prefix = prefix_m.group(0) if prefix_m else (name or "")
        nums = [int(x) for x in re.findall(r"\d+", name or "")]
        return (prefix, nums, name or "")

    def list_ports(
        self,
        blueprint_id: str,
        device: str | None = None,
        port: str | None = None,
    ) -> dict:
        """List the ports of a device (or of the whole blueprint).

        For each port (standard format, e.g. 'xe-0/0/1'): type, description,
        admin state (graph) and real-time operational state (up/down telemetry),
        LACP/LAG configuration (membership in an aggregate or members of an
        aggregate, lacp mode), VLAN, IP address, and the associated
        Connectivity Templates (by name).

        Scope:
          - 'device' provided -> ports of this device (label, id or serial
            number; partial match on the label);
          - 'port' provided (with 'device') -> only this port;
          - neither 'device' nor 'port' -> all the devices of the blueprint.
        """
        import re
        phys_re = re.compile(r"^(et|xe|ge|mge|fte|xle|ce)-\d+/\d+/\d+$")
        port_q = port.split(".")[0] if port else None

        systems = self._qe(
            blueprint_id, "node('system', system_type='switch', name='sw')")
        dev_q = device.lower() if device else None
        targets = []
        for s in systems:
            sw = s["sw"]
            if dev_q:
                if (dev_q not in (sw.get("label") or "").lower()
                        and dev_q != (sw.get("id") or "").lower()
                        and dev_q != (sw.get("system_id") or "").lower()):
                    continue
            targets.append(sw)
        if device and not targets:
            raise ValueError(f"Device '{device}' not found in the blueprint.")

        # CT (visible batch) by (switch, interface) — a single query.
        ct_map: dict[tuple, list] = {}
        ct_query = (
            "node('system', name='sw')"
            ".out('hosted_interfaces').node('interface', name='i')"
            ".out('ep_member_of').node('ep_group', name='g')"
            ".in_('ep_affected_by').node('ep_application_instance', name='ai')"
            ".out('ep_nested').node('ep_endpoint_policy', policy_type_name='batch', name='ep')"
        )
        try:
            for r in self._qe(blueprint_id, ct_query):
                ep = r.get("ep", {})
                if not ep.get("visible"):
                    continue
                key = (r["sw"].get("label"), r["i"].get("if_name"))
                lbl = ep.get("label")
                if lbl:
                    ct_map.setdefault(key, [])
                    if lbl not in ct_map[key]:
                        ct_map[key].append(lbl)
        except requests.exceptions.RequestException:
            pass

        devices_out = []
        for sw in targets:
            label = sw.get("label")
            serial = sw.get("system_id")
            node_id = sw.get("id")

            # Interface config (graph).
            cfg: dict[str, dict] = {}
            for r in self._qe(
                blueprint_id,
                f"node('system', id='{node_id}')"
                f".out('hosted_interfaces').node('interface', name='i')",
            ):
                i = r["i"]
                ifn = i.get("if_name")
                if ifn:
                    cfg[ifn] = i

            # LAG membership: port_channel -composed_of-> members.
            po_members: dict[str, dict] = {}
            member_of: dict[str, str] = {}
            for r in self._qe(
                blueprint_id,
                f"node('system', id='{node_id}').out('hosted_interfaces')"
                f".node('interface', if_type='port_channel', name='po')"
                f".out('composed_of').node('interface', name='m')",
            ):
                po = r["po"]
                m = r["m"]
                po_name = po.get("if_name")
                mem = m.get("if_name")
                if not po_name:
                    continue
                entry = po_members.setdefault(po_name, {
                    "lag_mode": po.get("lag_mode"),
                    "po_control_protocol": po.get("po_control_protocol"),
                    "members": [],
                })
                if mem and mem not in entry["members"]:
                    entry["members"].append(mem)
                if mem:
                    member_of[mem] = po_name

            # Real-time operational state (telemetry).
            status: dict[str, str] = {}
            if serial:
                try:
                    td = self._get(f"/systems/{serial}/services/interface/data")
                    for it in td.get("items", []):
                        nm = (it.get("identity") or {}).get("interface_name")
                        if nm:
                            status[nm] = (it.get("actual") or {}).get("value")
                except requests.exceptions.RequestException:
                    pass

            def oper_state(ifn: str):
                if ifn in status:
                    return status[ifn]
                subs = [v for k, v in status.items() if k.startswith(ifn + ".")]
                if any((v or "").lower() == "up" for v in subs):
                    return "up"
                if subs:
                    return "down"
                return None

            # Set of ports: physical (graph + telemetry) + aggregates.
            names: set = set()
            for ifn, i in cfg.items():
                base = ifn.split(".")[0]
                if i.get("if_type") == "port_channel":
                    names.add(ifn)
                elif phys_re.match(base):
                    names.add(base)
            for nm in status:
                base = nm.split(".")[0]
                if phys_re.match(base):
                    names.add(base)
            names.update(po_members.keys())

            if port_q:
                names = {n for n in names if n == port_q}

            ports = []
            for ifn in sorted(names, key=self._port_sort_key):
                ic = cfg.get(ifn, {})
                # LAG / LACP
                lag = None
                lacp = False
                if ifn in po_members:
                    pm = po_members[ifn]
                    lag = {
                        "role": "aggregate",
                        "members": sorted(pm["members"], key=self._port_sort_key),
                        "lag_mode": pm["lag_mode"],
                        "po_control_protocol": pm["po_control_protocol"],
                    }
                    lacp = bool(pm["lag_mode"] and "lacp" in pm["lag_mode"].lower())
                elif ifn in member_of:
                    ae = member_of[ifn]
                    ae_mode = po_members.get(ae, {}).get("lag_mode")
                    lag = {
                        "role": "member",
                        "aggregate": ae,
                        "lag_mode": ae_mode,
                    }
                    lacp = bool(ae_mode and "lacp" in ae_mode.lower())

                # Connectivity Templates: direct + inherited from the aggregate.
                cts = list(ct_map.get((label, ifn), []))
                if ifn in member_of:
                    for c in ct_map.get((label, member_of[ifn]), []):
                        if c not in cts:
                            cts.append(c)

                ports.append({
                    "name": ifn,
                    "type": ic.get("if_type") or (
                        "port_channel" if ifn in po_members else "ethernet"),
                    "description": ic.get("description"),
                    "admin_state": ic.get("operation_state"),
                    "oper_state": oper_state(ifn),
                    "lacp": lacp,
                    "lag": lag,
                    "vlan_id": ic.get("vlan_id"),
                    "ipv4_addr": ic.get("ipv4_addr"),
                    "connectivity_templates": cts,
                })

            devices_out.append({
                "device": label,
                "role": sw.get("role"),
                "system_id": serial,
                "port_count": len(ports),
                "ports": ports,
            })

        if port and not any(d["ports"] for d in devices_out):
            raise ValueError(
                f"Port '{port}' not found" +
                (f" on '{device}'." if device else " in the blueprint."))

        return {
            "blueprint_id": blueprint_id,
            "filter": {"device": device, "port": port},
            "device_count": len(devices_out),
            "devices": devices_out,
        }

