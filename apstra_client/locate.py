"""Locate a host by MAC or IP (ARP, graph interfaces, EVPN Type-5 routes)."""

import ipaddress
import requests
from typing import Any, Optional


class LocateMixin:
    # ── Endpoint location by MAC or IP ──────────────────────────

    def _locate_mac(self, blueprint_id: str, mac_norm: str) -> dict:
        """Locate a MAC via the IBA 'MAC Monitor' probe (stage MAC Address Table).

        Distinguishes the real physical location (next_hop_type != vxlan) from
        remote locations learned via VXLAN/VTEP.
        """
        probe_id = self._find_probe_id(blueprint_id, "MAC Monitor")
        local: list[dict] = []
        remote: list[dict] = []
        if not probe_id:
            return {"probe_id": None, "physical_location": local, "remote_locations": remote}
        serials = self._serial_to_system(blueprint_id)
        for row in self._query_probe_stage(blueprint_id, probe_id, "MAC Address Table"):
            props = row.get("properties", row) or {}
            if self._normalize_mac(props.get("mac", "")) != mac_norm:
                continue
            serial = props.get("system_id")
            sw = serials.get(serial, {})
            entry = {
                "blueprint_id": blueprint_id,
                "leaf": sw.get("system_label") or sw.get("hostname") or serial,
                "leaf_serial": serial,
                "interface": props.get("interface"),
                "vlan": props.get("vlan"),
                "vni": props.get("vn_id"),
                "vn_type": props.get("vn_type"),
                "vrf": props.get("vrf_name"),
                "next_hop_type": props.get("next_hop_type"),
                "state": row.get("value"),
            }
            if props.get("next_hop_type") == "vxlan" or str(props.get("interface", "")).startswith("vtep"):
                remote.append(entry)
            else:
                local.append(entry)
        return {"probe_id": probe_id, "physical_location": local, "remote_locations": remote}

    def _locate_ip(self, blueprint_id: str, ip: str) -> dict:
        """Locate an IP: ARP table (real host) + graph interfaces + EVPN Type-5 routes."""
        try:
            target = ipaddress.ip_address(ip)
        except ValueError:
            target = None
        serials = self._serial_to_system(blueprint_id)

        # 0) Host learned via ARP (real physical leaf + port)
        arp: list[dict] = []
        for entry in self._query_arp(blueprint_id, ip=ip):
            serial = entry.get("system_id", "")
            sw = serials.get(serial, {})
            arp.append({
                "blueprint_id": blueprint_id,
                "leaf": sw.get("system_label") or sw.get("hostname") or serial,
                "leaf_serial": serial,
                "interface": entry.get("interface_name"),
                "mac": entry.get("mac_address"),
                "vrf": entry.get("vrf_name"),
                "arp_type": entry.get("type"),
            })

        # 1) Exact match on the graph interface IPs
        interfaces: list[dict] = []
        query = "node('system', name='s').out('hosted_interfaces').node('interface', name='i')"
        try:
            for it in self._qe(blueprint_id, query):
                addr = (it.get("i", {}).get("ipv4_addr") or "").split("/")[0]
                if addr and addr == ip:
                    interfaces.append({
                        "blueprint_id": blueprint_id,
                        "system": it.get("s", {}).get("label"),
                        "role": it.get("s", {}).get("role"),
                        "interface": it.get("i", {}).get("if_name"),
                        "ip": addr,
                    })
        except requests.exceptions.RequestException:
            pass

        # 2) EVPN Type-5 subnet containing the IP (advertising leaf + RT)
        routes: list[dict] = []
        seen: set = set()
        probe_id = self._find_probe_id(blueprint_id, "EVPN VXLAN Type-5 Route Validation")
        if probe_id and target is not None:
            for row in self._query_probe_stage(blueprint_id, probe_id, "EVPN Table"):
                props = row.get("properties", row) or {}
                subnet = props.get("subnet") or props.get("prefix")
                if not subnet:
                    continue
                try:
                    network = ipaddress.ip_network(subnet, strict=False)
                except ValueError:
                    continue
                if network.prefixlen == 0 or target not in network:
                    continue
                serial = props.get("system_id")
                sw = serials.get(serial, {})
                key = (serial, subnet, props.get("rt"))
                if key in seen:
                    continue
                seen.add(key)
                routes.append({
                    "blueprint_id": blueprint_id,
                    "leaf": sw.get("system_label") or sw.get("hostname") or serial,
                    "leaf_serial": serial,
                    "subnet": subnet,
                    "prefixlen": network.prefixlen,
                    "address_family": props.get("address_family"),
                    "route_target": props.get("rt"),
                    "route_distinguisher": props.get("rd"),
                })
            routes.sort(key=lambda r: (-r["prefixlen"], r.get("leaf") or ""))

        return {
            "probe_id": probe_id,
            "arp_matches": arp,
            "interface_matches": interfaces,
            "evpn_routes": routes,
        }

    def locate(
        self,
        blueprint_id: str | None = None,
        mac: str | None = None,
        ip: str | None = None,
    ) -> dict:
        """Locate an endpoint on the network by MAC or IP address.

        - MAC: reads the fabric MAC table (IBA 'MAC Monitor' probe) and
          distinguishes the real physical location (port + leaf) from remote
          locations learned via VXLAN/VTEP.
        - IP: correlates three sources -> learned ARP table (real host on its
          leaf and its port), graph interface IP (fabric/underlay), and EVPN
          Type-5 subnet containing the IP (advertising leaf + route target).

        'blueprint_id' omitted: all blueprints are traversed. The serial
        number (system_id) reported by the telemetry is resolved to a leaf name.
        """
        if not (mac or ip):
            raise ValueError("Provide at least one criterion: mac or ip.")
        mac_norm = self._normalize_mac(mac) if mac else None
        ip_q = self._strip_cidr(ip) if ip else None

        if blueprint_id:
            blueprints = [blueprint_id]
        else:
            blueprints = [b["id"] for b in self.list_blueprints() if b.get("id")]

        mac_result: dict | None = None
        ip_result: dict | None = None
        if mac_norm:
            mac_result = {"physical_location": [], "remote_locations": [], "probes": {}}
        if ip_q:
            ip_result = {"arp_matches": [], "interface_matches": [], "evpn_routes": [], "probes": {}}

        for bp in blueprints:
            if mac_norm:
                r = self._locate_mac(bp, mac_norm)
                mac_result["physical_location"].extend(r["physical_location"])
                mac_result["remote_locations"].extend(r["remote_locations"])
                mac_result["probes"][bp] = r["probe_id"]
            if ip_q:
                r = self._locate_ip(bp, ip_q)
                ip_result["arp_matches"].extend(r["arp_matches"])
                ip_result["interface_matches"].extend(r["interface_matches"])
                ip_result["evpn_routes"].extend(r["evpn_routes"])
                ip_result["probes"][bp] = r["probe_id"]

        found = False
        if mac_result:
            found = found or bool(mac_result["physical_location"] or mac_result["remote_locations"])
        if ip_result:
            found = found or bool(
                ip_result["arp_matches"] or ip_result["interface_matches"] or ip_result["evpn_routes"]
            )
        return {
            "criteria": {"mac": mac, "ip": ip},
            "blueprints_searched": blueprints,
            "found": found,
            "mac_result": mac_result,
            "ip_result": ip_result,
        }

