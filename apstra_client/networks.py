"""Virtual Networks and Security/Routing Zones (incl. DCI activation)."""

import ipaddress
import requests
from typing import Any, Optional


class NetworksMixin:
    # ── Virtual Networks ──────────────────────────────────────────────────

    def list_virtual_networks(self, blueprint_id: str) -> list[dict]:
        items = self._get(f"/blueprints/{blueprint_id}/virtual-networks").get("virtual_networks", [])
        return self._slim(items, "id", "label", "vn_type", "security_zone_id", "vn_id", "ipv4_subnet")

    def get_virtual_network(self, blueprint_id: str, vn_id: str) -> dict:
        return self._get(f"/blueprints/{blueprint_id}/virtual-networks/{vn_id}")

    def create_virtual_network(self, blueprint_id: str, payload: dict) -> dict:
        return self._post(f"/blueprints/{blueprint_id}/virtual-networks", payload)

    def _generic_side_interface(
        self, blueprint_id: str, switch_iface_id: str,
    ) -> str | None:
        """Return the generic-system-side interface of a switch port's link.

        To assign a VN to a port via Apstra's native mechanism (which
        auto-creates the Connectivity Template), the endpoint must reference
        the interface of the generic system on the other end, not the switch
        port interface.
        """
        rows = self._qe(
            blueprint_id,
            f"node('interface', id='{switch_iface_id}')"
            f".out('link').node('link')"
            f".in_('link').node('interface', name='oi')"
            f".in_('hosted_interfaces').node('system', role='generic')",
        )
        return rows[0]["oi"]["id"] if rows else None

    def delete_virtual_network(self, blueprint_id: str, vn_id: str) -> dict:
        """Delete a VN, first removing its VN endpoints (and auto CT).

        A VN with endpoints cannot be deleted directly: each endpoint is
        removed (which removes the auto-generated Connectivity Template) before
        deleting the VN.
        """
        removed = []
        try:
            vn = self._get(
                f"/blueprints/{blueprint_id}/virtual-networks/{vn_id}")
        except requests.HTTPError:
            vn = {}
        for ep in (vn.get("endpoints") or []):
            ep_id = ep.get("vn_endpoint_id")
            if ep_id:
                self._delete(
                    f"/blueprints/{blueprint_id}/virtual-networks/{vn_id}"
                    f"/endpoints/{ep_id}")
                removed.append(ep_id)
        self._delete(f"/blueprints/{blueprint_id}/virtual-networks/{vn_id}")
        return {"deleted_vn": vn_id, "removed_endpoints": removed}

    def _resolve_system_id_for_bound_to(self, blueprint_id: str, system_id: str) -> str:
        """Return the redundancy_group ID if the switch is in an ESI pair, otherwise its own ID."""
        items = self._qe(
            blueprint_id,
            "node('system', id='{sid}', name='sw')"
            ".in_('composed_of_systems').node('redundancy_group', name='rg')"
            .format(sid=system_id),
        )
        if items:
            return items[0]["rg"]["id"]
        return system_id

    def list_redundancy_groups(self, blueprint_id: str) -> list[dict]:
        """List the ESI pairs (redundancy groups) and their members."""
        items = self._qe(
            blueprint_id,
            "node('redundancy_group', name='rg')"
            ".out('composed_of_systems').node('system', name='sw')",
        )
        groups: dict[str, dict] = {}
        for item in items:
            rg_id = item["rg"]["id"]
            if rg_id not in groups:
                groups[rg_id] = {
                    "id":      rg_id,
                    "label":   item["rg"].get("label", ""),
                    "members": [],
                }
            groups[rg_id]["members"].append({
                "id":    item["sw"]["id"],
                "label": item["sw"].get("label", ""),
                "role":  item["sw"].get("role", ""),
            })
        return list(groups.values())

    def update_virtual_network(self, blueprint_id: str, vn_id: str, payload: dict) -> dict:
        """PATCH a VN. If 'bound_to' contains system_ids of ESI switches, resolve to the RG."""
        if "bound_to" in payload:
            resolved = []
            seen: set[str] = set()
            for entry in payload["bound_to"]:
                raw_id = entry["system_id"] if isinstance(entry, dict) else entry
                actual_id = self._resolve_system_id_for_bound_to(blueprint_id, raw_id)
                if actual_id not in seen:
                    seen.add(actual_id)
                    base = entry if isinstance(entry, dict) else {"system_id": raw_id}
                    resolved.append({**base, "system_id": actual_id})
            payload = {**payload, "bound_to": resolved}
        return self._patch(f"/blueprints/{blueprint_id}/virtual-networks/{vn_id}", payload)

    def list_connectivity_templates(self, blueprint_id: str) -> list[dict]:
        # OpenAPI 6.1 exposes /design/endpoint-policies; blueprint fallback for compat.
        try:
            data = self._get("/design/endpoint-policies")
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            data = self._get(f"/blueprints/{blueprint_id}/endpoint-policies")
        items = data.get("items", data) if isinstance(data, dict) else data
        raw = items if isinstance(items, list) else list(items.values())
        return self._slim(raw, "id", "label", "description")

    def apply_ct_to_interfaces(
        self, blueprint_id: str, ct_id: str, interface_ids: list[str]
    ) -> dict:
        """Apply a Connectivity Template to a list of interfaces."""
        payload = {
            "application_points": [
                {"id": iface_id, "policies": [{"policy": ct_id, "used": True}]}
                for iface_id in interface_ids
            ]
        }
        return self._put(
            f"/blueprints/{blueprint_id}/obj-policy-batch-apply", payload
        )

    def enable_vn_dci(
        self,
        blueprint_id: str,
        vn_id: str,
        enable_rt2: bool = True,
        enable_rt5: bool = True,
    ) -> dict:
        """
        Enable DCI on a Virtual Network.
        Retrieves the existing RTs and applies them automatically.
          RT2: export/import_route_targets (L2 MAC/IP)
          RT5: l3_vni export/import route targets (L3 IP prefixes)
        """
        vn = self.get_virtual_network(blueprint_id, vn_id)
        payload: dict = {}

        if enable_rt2:
            rt2 = vn.get("export_route_targets") or vn.get("route_target")
            if not rt2:
                raise ValueError(
                    f"No RT2 found on VN '{vn_id}'. "
                    "Check that the VN indeed has Route Targets configured."
                )
            payload["export_route_targets"] = rt2
            payload["import_route_targets"] = vn.get("import_route_targets", rt2)

        if enable_rt5:
            l3  = vn.get("l3_vni", {})
            rt5 = l3.get("export_route_targets") or vn.get("l3_export_route_targets")
            if not rt5:
                raise ValueError(
                    f"No RT5 found on VN '{vn_id}'. "
                    "Check that the VN has an L3 VNI configured with Route Targets."
                )
            payload["l3_vni"] = {
                "export_route_targets": rt5,
                "import_route_targets": l3.get("import_route_targets", rt5),
            }

        result = self._patch(
            f"/blueprints/{blueprint_id}/virtual-networks/{vn_id}", payload
        )
        result["dci_activated"] = {
            "vn_id": vn_id, "rt2_enabled": enable_rt2,
            "rt5_enabled": enable_rt5, "applied_payload": payload,
        }
        return result

    # ── Security / Routing Zones ──────────────────────────────────────────

    def list_security_zones(self, blueprint_id: str) -> list[dict]:
        data = self._get(f"/blueprints/{blueprint_id}/security-zones")
        if isinstance(data, dict) and "items" in data:
            raw = data["items"]
        else:
            raw = list(data.values()) if isinstance(data, dict) else data
        return self._slim(raw, "id", "label", "vrf_name", "sz_type", "vni_id")

    def get_security_zone(self, blueprint_id: str, sz_id: str) -> dict:
        return self._get(f"/blueprints/{blueprint_id}/security-zones/{sz_id}")

    def create_security_zone(self, blueprint_id: str, payload: dict) -> dict:
        return self._post(f"/blueprints/{blueprint_id}/security-zones", payload)

    def enable_sz_dci(
        self,
        blueprint_id: str,
        sz_id: str,
        enable_rt5: bool = True,
        enable_irt: bool = True,
    ) -> dict:
        """
        Enable DCI on a Security Zone (VRF).
        Retrieves the existing RTs and applies them automatically.
          RT5: export/import of inter-DC IP prefixes
          iRT: import local Route Targets
        """
        sz = self.get_security_zone(blueprint_id, sz_id)
        payload: dict = {}

        if enable_rt5:
            rt5_exp = sz.get("export_route_targets")
            if not rt5_exp:
                raise ValueError(f"No RT5 export found on Security Zone '{sz_id}'.")
            payload["export_route_targets"] = rt5_exp

        if enable_irt:
            irt = sz.get("import_route_targets") or sz.get("export_route_targets")
            if not irt:
                raise ValueError(f"No iRT found on Security Zone '{sz_id}'.")
            payload["import_route_targets"] = irt

        result = self._patch(
            f"/blueprints/{blueprint_id}/security-zones/{sz_id}", payload
        )
        result["dci_activated"] = {
            "sz_id": sz_id, "rt5_enabled": enable_rt5,
            "irt_enabled": enable_irt, "applied_payload": payload,
        }
        return result

