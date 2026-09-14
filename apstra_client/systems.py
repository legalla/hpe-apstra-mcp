"""Systems/devices and generic systems (incl. the Query Engine helper _qe)."""

import ipaddress
import requests
from typing import Any, Optional


class SystemsMixin:
    # ── Systems / Devices ─────────────────────────────────────────────────

    def list_systems(self) -> list[dict]:
        items = self._get("/systems").get("items", [])
        return self._slim(items, "id", "label", "hostname", "status", "device_profile")

    def get_system(self, system_id: str) -> dict:
        return self._get(f"/systems/{system_id}")

    def list_agents(self) -> list[dict]:
        items = self._get("/system-agents").get("items", [])
        return self._slim(items, "id", "label", "status", "system_id")

    def get_agent(self, agent_id: str) -> dict:
        return self._get(f"/system-agents/{agent_id}")

    # ── Generic Systems ───────────────────────────────────────────────────

    def _normalize_speed(self, speed: str) -> str:
        return self._SPEED_MAP.get(speed.lower().strip(), speed.upper().strip())

    def _get_switch_interface_map_id(self, blueprint_id: str, switch_id: str) -> str:
        """Retrieve the ID of the interface map assigned to a switch in the blueprint."""
        try:
            assignments = self._get(f"/blueprints/{blueprint_id}/interface-map-assignments")
            if switch_id in assignments:
                return assignments[switch_id]
            for node_id, im_id in assignments.items():
                if node_id.startswith(switch_id) or switch_id.startswith(node_id):
                    return im_id
        except requests.exceptions.RequestException:
            pass

        nodes = self.get_blueprint_nodes(blueprint_id, node_type="system")
        for node in nodes:
            if node.get("id") == switch_id or node.get("system_id") == switch_id:
                im_id = node.get("interface_map_id") or node.get("im_id")
                if im_id:
                    return im_id

        raise ValueError(
            f"Interface map not found for switch '{switch_id}'. "
            "Check that the switch is properly assigned in this blueprint."
        )

    def _list_available_speeds(self, im: dict) -> list[str]:
        speeds = set()
        for iface in im.get("interfaces", []):
            s = iface.get("speed") or iface.get("setting", {}).get("speed")
            if s:
                speeds.add(str(s))
        for transform in im.get("transformations", []):
            for iface in transform.get("interfaces", []):
                s = iface.get("speed")
                if s:
                    speeds.add(str(s))
        return sorted(speeds)

    def _find_transformation_id(
        self, blueprint_id: str, switch_id: str, port_speed: str
    ) -> int:
        """Find the transformation_id matching a port speed."""
        target_speed = self._normalize_speed(port_speed)
        im_id = self._get_switch_interface_map_id(blueprint_id, switch_id)
        im    = self._get(f"/design/interface-maps/{im_id}")

        for iface in im.get("interfaces", []):
            speed_val = (
                iface.get("speed")
                or iface.get("setting", {}).get("speed")
                or (iface.get("setting", {}).get("param") or [{}])[0].get("value")
            )
            if speed_val and self._normalize_speed(str(speed_val)) == target_speed:
                t_id = iface.get("transformation_id") or iface.get("transformationId")
                if t_id is not None:
                    return int(t_id)

        for transform in im.get("transformations", []):
            for iface in transform.get("interfaces", []):
                if self._normalize_speed(str(iface.get("speed", ""))) == target_speed:
                    return int(transform.get("id", 1))

        raise ValueError(
            f"No '{target_speed}' transformation found in interface map "
            f"'{im_id}' of switch '{switch_id}'. "
            f"Available speeds: {self._list_available_speeds(im)}"
        )

    def _qe(self, blueprint_id: str, query: str) -> list[dict]:
        """Execute a query on the Apstra Query Engine.

        On some environments, /qe may return 404 and try an internal GraphQL
        backend (/graphql/main). We then fall back to /ql-readonly.
        """
        try:
            data = self._post(f"/blueprints/{blueprint_id}/qe", {"query": query})
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            data = self._post(f"/blueprints/{blueprint_id}/ql-readonly", {"query": query})
        return self._extract_items(data)

    def create_generic_system(
        self,
        blueprint_id: str,
        label: str,
        links: list[dict],
        port_speed: str,
        lag_mode: str | None = None,
        asn: int | None = None,
        loopback_ip: str | None = None,
        hostname: str | None = None,
    ) -> dict:
        """
        Create a generic system in a blueprint.
        The transformation_id is resolved automatically from the port speed.

        links: list of dicts with the keys:
          - switch_id  : switch ID
          - switch_if  : interface on the switch (e.g. xe-0/0/0)
          - system_if  : server-side interface (e.g. eth0) -- optional
        """
        if not links:
            raise ValueError("At least one link must be provided.")

        transform_id = self._find_transformation_id(
            blueprint_id, links[0]["switch_id"], port_speed
        )

        link_objects = []
        for i, lnk in enumerate(links):
            entry = {
                "switch": {
                    "system_id":         lnk["switch_id"],
                    "transformation_id": transform_id,
                    "if_name":           lnk["switch_if"],
                },
                "system": {
                    "if_name":           lnk.get("system_if", f"eth{i}"),
                    "transformation_id": 1,
                },
            }
            if lag_mode:
                entry["lag_mode"] = lag_mode
            link_objects.append(entry)

        system: dict = {
            "system_type": "generic",
            "label":    label,
            "hostname": hostname or label,
            "links":    link_objects,
        }
        if asn is not None:
            system["asn"] = asn
        if loopback_ip:
            system["loopback_ip"] = loopback_ip

        result = self._post(
            f"/blueprints/{blueprint_id}/generic-systems",
            {"new_systems": [system]},
        )
        result["summary"] = {
            "label":             label,
            "hostname":          hostname or label,
            "port_speed":        self._normalize_speed(port_speed),
            "transformation_id": transform_id,
            "lag_mode":          lag_mode,
            "link_count":        len(links),
            "asn":               asn,
            "loopback_ip":       loopback_ip,
        }
        return result

    def list_generic_systems_on_switch(
        self, blueprint_id: str, switch_id: str
    ) -> list[dict]:
        """List all generic systems connected to a switch. Groups LAG links."""
        query = (
            "node('system', system_type='switch', id='{sid}', name='sw')"
            ".out('hosted_interfaces').node('interface', name='sw_if')"
            ".out('link').node('link', name='lnk')"
            ".in_('link').node('interface', name='gs_if')"
            ".in_('hosted_interfaces')"
            ".node('system', system_type='generic', name='gs')"
        ).format(sid=switch_id)

        grouped: dict[str, dict] = {}
        for item in self._qe(blueprint_id, query):
            gs_id = item["gs"]["id"]
            if gs_id not in grouped:
                grouped[gs_id] = {
                    "id":       gs_id,
                    "label":    item["gs"].get("label", ""),
                    "hostname": item["gs"].get("hostname", ""),
                    "asn":      item["gs"].get("asn"),
                    "links":    [],
                }
            grouped[gs_id]["links"].append({
                "switch_if": item["sw_if"].get("if_name", ""),
                "system_if": item["gs_if"].get("if_name", ""),
                "lag_mode":  item["lnk"].get("lag_mode"),
                "link_id":   item["lnk"].get("id", ""),
            })
        return list(grouped.values())

    def get_generic_system_on_port(
        self, blueprint_id: str, switch_id: str, switch_if: str
    ) -> dict | None:
        """Find the generic system on a specific port. Returns None if free."""
        query = (
            "node('system', system_type='switch', id='{sid}', name='sw')"
            ".out('hosted_interfaces')"
            ".node('interface', if_name='{iface}', name='sw_if')"
            ".out('link').node('link', name='lnk')"
            ".in_('link').node('interface', name='gs_if')"
            ".in_('hosted_interfaces')"
            ".node('system', system_type='generic', name='gs')"
        ).format(sid=switch_id, iface=switch_if)

        items = self._qe(blueprint_id, query)
        if not items:
            return None
        item = items[0]
        return {
            "id":        item["gs"]["id"],
            "label":     item["gs"].get("label", ""),
            "hostname":  item["gs"].get("hostname", ""),
            "asn":       item["gs"].get("asn"),
            "switch_if": item["sw_if"].get("if_name", ""),
            "system_if": item["gs_if"].get("if_name", ""),
            "lag_mode":  item["lnk"].get("lag_mode"),
            "link_id":   item["lnk"].get("id", ""),
        }

