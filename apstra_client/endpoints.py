"""Endpoint search (ARP/MAC/EVPN) and VM inventory (vCenter integration)."""

import ipaddress
import requests
from typing import Any, Optional


class EndpointsMixin:
    # ── Endpoint search (IP / MAC / VM) ──────────────────────────────

    @staticmethod
    def _normalize_mac(value: str) -> str:
        """Normalize a MAC: lowercase, without separators (: - .)."""
        return "".join(c for c in value.lower() if c in "0123456789abcdef")

    @staticmethod
    def _strip_cidr(ip: str | None) -> str:
        """Remove the CIDR mask from an address (10.0.0.1/31 -> 10.0.0.1)."""
        return ip.split("/")[0] if ip else ""

    def _find_probe_id(self, blueprint_id: str, label: str) -> str | None:
        """Return the id of the first IBA probe whose label matches."""
        try:
            probes = self._get(f"/blueprints/{blueprint_id}/probes").get("items", [])
        except requests.exceptions.RequestException:
            return None
        for p in probes:
            if p.get("label") == label:
                return p.get("id")
        return None

    def _query_probe_stage(self, blueprint_id: str, probe_id: str, stage: str) -> list[dict]:
        """Retrieve the entries of an IBA probe stage.

        Access to stage data is done via POST .../probes/{id}/query
        with a body {"stage": "<stage name>"}.
        """
        try:
            data = self._post(
                f"/blueprints/{blueprint_id}/probes/{probe_id}/query",
                {"stage": stage},
            )
        except requests.exceptions.RequestException:
            return []
        if isinstance(data, dict):
            return data.get("items", []) or []
        return data if isinstance(data, list) else []

    def _query_arp(
        self,
        blueprint_id: str,
        ip: str | None = None,
        mac: str | None = None,
    ) -> list[dict]:
        """Query the learned ARP/MAC table (dynamicArp source) of the blueprint.

        Apstra endpoint POST /blueprints/{bp}/query/arp: returns the
        IP <-> MAC <-> interface <-> VRF tuples learned by the devices.
        """
        body: dict = {}
        if ip:
            body["ip_address"] = self._strip_cidr(ip)
        if mac:
            body["mac_address"] = mac
        try:
            data = self._post(f"/blueprints/{blueprint_id}/query/arp", body)
        except requests.exceptions.RequestException:
            return []
        if isinstance(data, dict):
            return data.get("items", []) or []
        return data if isinstance(data, list) else []

    def _serial_to_system(self, blueprint_id: str) -> dict:
        """Build a mapping serial number (telemetry system_id) -> switch info."""
        mapping: dict[str, dict] = {}
        try:
            items = self._qe(blueprint_id, "node('system', name='sys')")
        except requests.exceptions.RequestException:
            return mapping
        for item in items:
            sys = item.get("sys", {})
            serial = sys.get("system_id")
            if serial:
                mapping[serial] = {
                    "system_label": sys.get("label", ""),
                    "hostname":     sys.get("hostname", ""),
                    "system_type":  sys.get("system_type", ""),
                    "role":         sys.get("role", ""),
                    "node_id":      sys.get("id"),
                }
        return mapping

    def find_endpoint(
        self,
        blueprint_id: str | None = None,
        ip: str | None = None,
        mac: str | None = None,
        name: str | None = None,
    ) -> dict:
        """Search for a learned endpoint (VM/host) by IP or MAC in the telemetry.

        Learned endpoints do not appear in the design graph: they are
        exposed by the telemetry. This search queries:
          - the learned ARP table (dynamicArp source) which directly correlates
            IP <-> MAC <-> interface <-> VRF on the device;
          - the "MAC Monitor" probe (stage "MAC Address Table") for a MAC;
          - the "EVPN VXLAN Type-5 Route Validation" probe (stage "EVPN Table")
            for an IP (/32 or /128 advertised routes).
        The serial number (system_id) reported by the telemetry is resolved to a
        switch name via the graph. If blueprint_id is omitted, all blueprints
        are traversed. MAC comparison ignores separators; IP comparison accepts
        a prefix.
        """
        if not any([ip, mac, name]):
            raise ValueError("Provide at least one criterion: ip, mac or name.")

        ip_q  = self._strip_cidr(ip).lower() if ip else None
        mac_q = self._normalize_mac(mac) if mac else None

        if blueprint_id:
            blueprints = [blueprint_id]
        else:
            blueprints = [b["id"] for b in self.list_blueprints() if b.get("id")]

        matches: list[dict] = []

        for bp in blueprints:
            serial_map: dict | None = None  # resolved lazily on first hit

            def resolve(serial: str) -> dict:
                nonlocal serial_map
                if serial_map is None:
                    serial_map = self._serial_to_system(bp)
                return serial_map.get(serial, {})

            # 0) Learned ARP table (dynamicArp source): IP <-> MAC <-> interface <-> VRF
            for entry in self._query_arp(bp, ip=ip, mac=mac):
                serial = entry.get("system_id", "")
                sw = resolve(serial)
                matches.append({
                    "match_type":    "arp",
                    "source":        entry.get("type"),
                    "blueprint_id":  bp,
                    "ip":            entry.get("ip_address"),
                    "mac":           entry.get("mac_address"),
                    "switch_serial": serial,
                    "switch":        sw.get("system_label") or sw.get("hostname") or serial,
                    "interface":     entry.get("interface_name"),
                    "vrf_name":      entry.get("vrf_name"),
                    "timestamp":     entry.get("last_modified_at"),
                })

            # 1) MAC -> probe "MAC Monitor", stage "MAC Address Table"
            if mac_q:
                probe_id = self._find_probe_id(bp, "MAC Monitor")
                if probe_id:
                    for entry in self._query_probe_stage(bp, probe_id, "MAC Address Table"):
                        props = entry.get("properties", entry) or {}
                        entry_mac = self._normalize_mac(props.get("mac", ""))
                        if entry_mac and mac_q in entry_mac:
                            serial = props.get("system_id", "")
                            sw = resolve(serial)
                            matches.append({
                                "match_type":   "mac_table",
                                "blueprint_id": bp,
                                "mac":          props.get("mac"),
                                "switch_serial": serial,
                                "switch":       sw.get("system_label") or sw.get("hostname") or serial,
                                "interface":    props.get("interface"),
                                "vlan":         props.get("vlan"),
                                "vn_id":        props.get("vn_id"),
                                "vn_type":      props.get("vn_type"),
                                "vrf_name":     props.get("vrf_name"),
                                "next_hop_type": props.get("next_hop_type"),
                                "state":        entry.get("value"),
                                "timestamp":    entry.get("timestamp"),
                            })

            # 2) IP -> probe "EVPN VXLAN Type-5 Route Validation", stage "EVPN Table"
            if ip_q:
                probe_id = self._find_probe_id(bp, "EVPN VXLAN Type-5 Route Validation")
                if probe_id:
                    for entry in self._query_probe_stage(bp, probe_id, "EVPN Table"):
                        props = entry.get("properties", entry) or {}
                        subnet = (props.get("subnet") or props.get("prefix") or "")
                        entry_ip = self._strip_cidr(subnet).lower()
                        if entry_ip and entry_ip.startswith(ip_q):
                            serial = props.get("system_id", "")
                            sw = resolve(serial)
                            matches.append({
                                "match_type":    "evpn_type5",
                                "blueprint_id":  bp,
                                "ip":            subnet,
                                "address_family": props.get("address_family"),
                                "switch_serial": serial,
                                "switch":        sw.get("system_label") or sw.get("hostname") or serial,
                                "next_hop":      props.get("next_hop"),
                                "endpoint":      props.get("endpoint"),
                                "route_distinguisher": props.get("rd"),
                                "route_target":  props.get("rt"),
                                "state":         entry.get("value"),
                                "timestamp":     entry.get("timestamp"),
                            })

        return {
            "blueprint_id": blueprint_id,
            "blueprints_searched": blueprints,
            "criteria": {"ip": ip, "mac": mac, "name": name},
            "match_count": len(matches),
            "matches": matches,
        }

    # ── Virtual machines (virtual-infra / vCenter integration) ──────────

    @staticmethod
    def _mac_from_vnic(vnic: str) -> str | None:
        """Extract the MAC address from a vNIC identifier ('vm-416' + '00:50:56:..')."""
        import re
        m = re.search(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})", vnic or "")
        return m.group(1) if m else None

    @staticmethod
    def _clean_iface_desc(desc: str) -> str:
        """'facing_dc1-leaf1:xe-0/0/24' -> 'dc1-leaf1:xe-0/0/24'."""
        return (desc or "").replace("facing_", "")

    def get_vm_info(
        self,
        name: str | None = None,
        blueprint_id: str | None = None,
    ) -> dict:
        """Information about virtual machines (vCenter/NSX integration).

        Data from the "VMs Without Fabric Configured VLANs" probe
        (stage "VMs on hypervisors connected to Fabric"): VM name,
        hypervisor, ESX server, fabric interfaces, VLAN, port-group and MAC.
        The VM IP address (often absent from the probe) is enriched from
        the learned ARP table. If 'name' is provided, filters by VM name
        (case-insensitive, partial match). If 'blueprint_id' is
        omitted, all blueprints are traversed.
        """
        name_q = name.lower() if name else None

        if blueprint_id:
            blueprints = [blueprint_id]
        else:
            blueprints = [b["id"] for b in self.list_blueprints() if b.get("id")]

        vms: list[dict] = []

        for bp in blueprints:
            probe_id = self._find_probe_id(bp, "VMs Without Fabric Configured VLANs")
            if not probe_id:
                continue
            entries = self._query_probe_stage(
                bp, probe_id, "VMs on hypervisors connected to Fabric"
            )

            # Aggregate by VM (one row per fabric interface)
            agg: dict[str, dict] = {}
            for entry in entries:
                props = entry.get("properties", entry) or {}
                vm_name = props.get("virtual_machine", "")
                if not vm_name:
                    continue
                if name_q and name_q not in vm_name.lower():
                    continue
                key = props.get("vm_node_id") or vm_name
                rec = agg.get(key)
                if rec is None:
                    rec = {
                        "blueprint_id":  bp,
                        "vm_name":       vm_name,
                        "vm_node_id":    props.get("vm_node_id"),
                        "hypervisor":    props.get("hypervisor"),
                        "server":        props.get("server"),
                        "vlan":          props.get("vlan"),
                        "port_group":    props.get("vnet"),
                        "mac":           self._mac_from_vnic(props.get("vnic", "")),
                        "vnic":          props.get("vnic"),
                        "vm_ip":         props.get("virtual_machine_ip") or None,
                        "interfaces":    [],
                        "source":        "vcenter",
                    }
                    agg[key] = rec
                iface = self._clean_iface_desc(props.get("interface_desc", ""))
                if iface and iface not in rec["interfaces"]:
                    rec["interfaces"].append(iface)

            # Enrich the IP via the ARP table (MAC -> IP) if absent
            for rec in agg.values():
                if not rec["vm_ip"] and rec["mac"]:
                    arp = self._query_arp(bp, mac=rec["mac"])
                    if arp:
                        rec["vm_ip"] = arp[0].get("ip_address")
                        rec["vrf_name"] = arp[0].get("vrf_name")
                vms.append(rec)

        return {
            "blueprint_id": blueprint_id,
            "blueprints_searched": blueprints,
            "criteria": {"name": name},
            "vm_count": len(vms),
            "vms": vms,
        }

