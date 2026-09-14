"""Switch topology and properties (uplinks, loopbacks, link IPs, ASN)."""

import ipaddress
import requests
from typing import Any, Optional


class TopologyMixin:
    # ── Topology / Properties ────────────────────────────────────────────

    def get_switch_properties(self, blueprint_id: str, switch_id: str) -> dict:
        """Retrieve ASN, role, hostname, system_id of a switch via the graph."""
        items = self._qe(blueprint_id,
                         "node('system', id='{sid}', name='sw')".format(sid=switch_id))
        if not items:
            raise ValueError(f"Switch '{switch_id}' not found in the blueprint.")
        sw  = items[0]["sw"]
        asn = sw.get("asn")
        if not asn:
            try:
                rg = self._qe(
                    blueprint_id,
                    "node('system', id='{sid}', name='sw')"
                    ".in_('composed_of_systems').node('redundancy_group', name='rg')"
                    .format(sid=switch_id)
                )
                if rg:
                    asn = rg[0]["rg"].get("asn")
            except requests.exceptions.RequestException:
                pass
        return {
            "id":          sw.get("id"),
            "label":       sw.get("label", ""),
            "hostname":    sw.get("hostname", ""),
            "role":        sw.get("role", ""),
            "system_type": sw.get("system_type", ""),
            "asn":         asn,
            "system_id":   sw.get("system_id"),
            "deploy_mode": sw.get("deploy_mode"),
        }

    def get_switch_uplinks(self, blueprint_id: str, switch_id: str) -> list[dict]:
        """List a switch's connections to the Spines with the link IPs."""
        query = (
            "node('system', id='{sid}', name='sw')"
            ".out('hosted_interfaces').node('interface', name='local_if')"
            ".out('link').node('link', name='lnk')"
            ".in_('link').node('interface', name='remote_if')"
            ".in_('hosted_interfaces')"
            ".node('system', role='spine', name='spine')"
        ).format(sid=switch_id)
        return [
            {
                "local_interface":  item["local_if"].get("if_name", ""),
                "local_ip":         item["local_if"].get("ipv4_addr"),
                "remote_switch":    item["spine"].get("label", ""),
                "remote_switch_id": item["spine"].get("id", ""),
                "remote_interface": item["remote_if"].get("if_name", ""),
                "remote_ip":        item["remote_if"].get("ipv4_addr"),
                "link_id":          item["lnk"].get("id", ""),
                "speed":            item["lnk"].get("speed"),
            }
            for item in self._qe(blueprint_id, query)
        ]

    def get_link_ips(
        self, blueprint_id: str, switch_id_a: str, switch_id_b: str
    ) -> list[dict]:
        """Retrieve the point-to-point IP addresses between two switches."""
        query = (
            "node('system', id='{sid_a}', name='sw_a')"
            ".out('hosted_interfaces').node('interface', name='if_a')"
            ".out('link').node('link', name='lnk')"
            ".in_('link').node('interface', name='if_b')"
            ".in_('hosted_interfaces')"
            ".node('system', id='{sid_b}', name='sw_b')"
        ).format(sid_a=switch_id_a, sid_b=switch_id_b)

        items = self._qe(blueprint_id, query)
        if not items:
            raise ValueError(
                f"No link found between '{switch_id_a}' and '{switch_id_b}'."
            )
        return [
            {
                "switch_a":    item["sw_a"].get("label", ""),
                "interface_a": item["if_a"].get("if_name", ""),
                "ip_a":        item["if_a"].get("ipv4_addr"),
                "switch_b":    item["sw_b"].get("label", ""),
                "interface_b": item["if_b"].get("if_name", ""),
                "ip_b":        item["if_b"].get("ipv4_addr"),
                "link_id":     item["lnk"].get("id", ""),
                "speed":       item["lnk"].get("speed"),
            }
            for item in items
        ]

    def get_switch_loopbacks(self, blueprint_id: str, switch_id: str) -> list[dict]:
        """Retrieve all loopback interfaces configured on a switch."""
        query = (
            "node('system', id='{sid}', name='sw')"
            ".out('hosted_interfaces')"
            ".node('interface', if_type='loopback', name='lo')"
        ).format(sid=switch_id)
        return [
            {
                "switch":      item["sw"].get("label", ""),
                "if_name":     item["lo"].get("if_name", ""),
                "ipv4_addr":   item["lo"].get("ipv4_addr"),
                "ipv6_addr":   item["lo"].get("ipv6_addr"),
                "loopback_id": item["lo"].get("id"),
            }
            for item in self._qe(blueprint_id, query)
        ]

