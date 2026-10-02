"""Virtual Networks and Security/Routing Zones (incl. DCI activation)."""

import ipaddress
import json
import requests
import time
import uuid
from typing import Any, Optional


class NetworksMixin:
    # ── Virtual Networks ──────────────────────────────────────────────────

    def list_virtual_networks(self, blueprint_id: str) -> list[dict]:
        data = self._get(f"/blueprints/{blueprint_id}/virtual-networks")
        items = self._dict_values(data.get("virtual_networks", []))
        return self._slim(items, "id", "label", "vn_type", "security_zone_id", "vn_id", "ipv4_subnet")

    def get_virtual_network(self, blueprint_id: str, vn_id: str, enrich: bool = True) -> dict:
        """VN detail. With enrich=True, adds `connectivity_templates` / `ct_id`
        and resolves each endpoint to its (system, port)."""
        vn = self._get(f"/blueprints/{blueprint_id}/virtual-networks/{vn_id}")
        if enrich and isinstance(vn, dict) and vn.get("id"):
            self._enrich_virtual_network(blueprint_id, vn)
        return vn

    def _enrich_virtual_network(self, blueprint_id: str, vn: dict) -> None:
        try:
            cts = self.get_vn_connectivity_templates(blueprint_id, vn["id"])
            vn["connectivity_templates"] = cts
            vn["ct_id"] = cts[0]["id"] if len(cts) == 1 else None
            if vn.get("endpoints"):
                by_ep: dict[str, list] = {}
                for r in self.resolve_port_interfaces(blueprint_id):
                    by_ep.setdefault(r["endpoint_interface_id"], []).append(r)
                    if r["generic_interface_id"] != r["endpoint_interface_id"]:
                        by_ep.setdefault(r["generic_interface_id"], []).append(r)
                for ep in vn["endpoints"]:
                    rs = by_ep.get(ep.get("interface_id"))
                    if rs:
                        ep.update({
                            "system": rs[0]["switch"], "port": rs[0]["port"],
                            "generic_system": rs[0]["generic_system"],
                            "ct_interface_id": rs[0]["ct_interface_id"],
                            "ports": [{"system": r["switch"], "port": r["port"]} for r in rs],
                        })
        except (requests.exceptions.RequestException, ValueError, KeyError, TypeError):
            vn.setdefault("connectivity_templates", None)

    def get_vn_connectivity_templates(self, blueprint_id: str, vn_id: str) -> list[dict]:
        """User-visible CT(s) (batch policies) attached to a VN (batch -> pipeline -> AttachSingleVLAN -> VN)."""
        rows = self._qe(
            blueprint_id,
            "node('ep_endpoint_policy', policy_type_name='batch', name='ct')"
            ".out().node('ep_endpoint_policy', policy_type_name='pipeline')"
            ".out().node('ep_endpoint_policy', name='att')"
            f".out().node('virtual_network', id='{vn_id}')",
        )
        cts: dict[str, dict] = {}
        for r in rows:
            ct = cts.setdefault(r["ct"]["id"], {
                "id": r["ct"]["id"], "label": r["ct"].get("label"), "tagging": []})
            try:
                tag = json.loads(r["att"].get("attributes") or "{}").get("tag_type")
            except ValueError:
                tag = None
            if tag and tag not in ct["tagging"]:
                ct["tagging"].append(tag)
        return list(cts.values())

    def resolve_port_interfaces(
        self, blueprint_id: str, device: str | None = None, port: str | None = None,
    ) -> list[dict]:
        """Map each leaf<->generic-system link to the interface ids needed by VN
        endpoints and CT application.

        - `endpoint_interface_id`: generic-side interface (generic-side port-channel
          for LAG members) = `interface_id` of VN `endpoints`.
        - `ct_interface_id`: switch-side application point for
          `connectivity_template apply` (ESI/switch port-channel for LAG members).
        `device` (label or id) and `port` (exact if_name) filter the result.
        """
        rows = self._qe(
            blueprint_id,
            "node('system', role='generic', name='gs').out('hosted_interfaces').node('interface', name='gi')"
            ".out('link').node('link', name='l').in_('link').node('interface', name='si')"
            ".in_('hosted_interfaces').node('system', system_type='switch', name='sw')",
        )
        lag_of: dict[str, str] = {}
        for r in self._qe(
            blueprint_id,
            "node('system', role='generic').out('hosted_interfaces')"
            ".node('interface', if_type='port_channel', name='gpo')"
            ".out('composed_of').node('interface', name='gi')",
        ):
            lag_of[r["gi"]["id"]] = r["gpo"]["id"]
        ct_po: dict[str, str] = {}
        for r in self._qe(
            blueprint_id,
            "node('system', role='generic').out('hosted_interfaces')"
            ".node('interface', if_type='port_channel', name='gpo')"
            ".out('link').node('link').in_('link')"
            ".node('interface', if_type='port_channel', name='po')"
            ".in_('hosted_interfaces').node(name='host')",
        ):
            host = r["host"]
            if host.get("type") == "redundancy_group" or (
                host.get("type") == "system" and host.get("role") != "generic"
                    and r["gpo"]["id"] not in ct_po):
                ct_po[r["gpo"]["id"]] = r["po"]["id"]

        out: dict[str, dict] = {}
        for r in rows:
            gi, si, sw, gs = r["gi"], r["si"], r["sw"], r["gs"]
            if gi.get("if_type") == "port_channel" or si.get("if_type") == "port_channel":
                continue
            if device and device not in (sw.get("id"), sw.get("label")):
                continue
            if port and si.get("if_name") != port:
                continue
            lag = lag_of.get(gi["id"])
            out[gi["id"]] = {
                "switch": sw.get("label"), "switch_id": sw["id"],
                "port": si.get("if_name"), "switch_interface_id": si["id"],
                "generic_system": gs.get("label"), "generic_system_id": gs["id"],
                "generic_interface_id": gi["id"],
                "endpoint_interface_id": lag or gi["id"],
                "ct_interface_id": (ct_po.get(lag) if lag else None) or si["id"],
                "lag": bool(lag),
            }
        return sorted(out.values(), key=lambda x: (x["switch"] or "", x["port"] or ""))

    def _resolve_bound_to(self, blueprint_id: str, entries: list) -> tuple[list[dict], list[dict]]:
        """Resolve `bound_to` entries (system id/label, dict with system_id) to
        what Apstra expects (ESI members -> their redundancy group id).
        Returns (resolved entries, notes about every remapping)."""
        resolved: list[dict] = []
        notes: list[dict] = []
        seen: set[str] = set()
        for entry in entries:
            base = dict(entry) if isinstance(entry, dict) else {}
            raw = base.get("system_id") if isinstance(entry, dict) else entry
            if not raw:
                raise ValueError(f"bound_to entry without system_id: {entry!r}")
            sys_id = raw
            if not self._qe(blueprint_id, f"node('system', id='{raw}', name='s')"):
                if self._qe(blueprint_id, f"node('redundancy_group', id='{raw}', name='r')"):
                    actual = raw
                else:
                    found = self._qe(
                        blueprint_id, f"node('system', label='{raw}', name='s')")
                    if not found:
                        raise ValueError(
                            f"bound_to: '{raw}' is neither a system id/label nor a redundancy group id.")
                    sys_id = found[0]["s"]["id"]
                    actual = self._resolve_system_id_for_bound_to(blueprint_id, sys_id)
            else:
                actual = self._resolve_system_id_for_bound_to(blueprint_id, raw)
            if actual != raw:
                notes.append({
                    "requested": raw, "resolved_to": actual,
                    "reason": ("ESI member -> redundancy group" if actual != sys_id
                               else "label -> system id")})
            if actual in seen:
                continue
            seen.add(actual)
            base["system_id"] = actual
            base.setdefault("access_switch_node_ids", [])
            resolved.append(base)
        return resolved, notes

    def create_virtual_network(self, blueprint_id: str, payload: dict) -> dict:
        """POST a VN. `bound_to` is resolved like in update (leaf -> ESI group). The
        result is checked against the controller: fields that Apstra did not
        apply are reported in `warnings`."""
        notes: list[dict] = []
        if payload.get("bound_to"):
            resolved, notes = self._resolve_bound_to(blueprint_id, payload["bound_to"])
            payload = {**payload, "bound_to": resolved}
        result = self._post(f"/blueprints/{blueprint_id}/virtual-networks", payload)
        if not (isinstance(result, dict) and result.get("id")):
            return result
        if notes:
            result["bound_to_resolution"] = notes

        def mismatches(actual: dict) -> list[str]:
            out = []
            for key in ("ipv4_subnet", "virtual_gateway_ipv4", "vn_id", "security_zone_id"):
                if payload.get(key) is not None and str(actual.get(key)) != str(payload[key]):
                    out.append(
                        f"'{key}' requested={payload[key]!r} but controller reports {actual.get(key)!r}")
            if payload.get("bound_to"):
                want = {b["system_id"] for b in payload["bound_to"]}
                got = {b.get("system_id") for b in actual.get("bound_to") or []}
                if want != got:
                    out.append(f"'bound_to' requested={sorted(want)} but controller reports {sorted(got)}")
            return out

        # The controller applies the VN asynchronously: poll briefly before judging.
        actual: dict = {}
        warnings: list[str] = []
        for attempt in range(6):
            if attempt:
                time.sleep(0.5)
            try:
                actual = self._get(f"/blueprints/{blueprint_id}/virtual-networks/{result['id']}")
            except requests.exceptions.RequestException:
                continue
            warnings = mismatches(actual)
            if not warnings:
                break
        else:
            if not actual:
                return result
        result["applied"] = {k: actual.get(k) for k in (
            "label", "vn_type", "vn_id", "security_zone_id", "bound_to",
            "ipv4_subnet", "virtual_gateway_ipv4")}
        if warnings:
            result["warnings"] = warnings
        return result

    def create_vn_connectivity_template(
        self, blueprint_id: str, vn_node_id: str, vn_label: str,
        vn_type: str, tag_type: str,
    ) -> dict:
        """Create the CT (batch -> pipeline -> AttachSingleVLAN) of an EXISTING VN.
        Same structure as the CT Apstra builds when `create_policy_*` is set at
        VN creation. `tag_type`: 'vlan_tagged' | 'untagged'."""
        kind = "VxLAN" if vn_type == "vxlan" else "VLAN"
        label = f"{'Tagged' if tag_type == 'vlan_tagged' else 'Untagged'} {kind} '{vn_label}'"
        batch, pipeline, attach = (str(uuid.uuid4()) for _ in range(3))
        self._put(f"/blueprints/{blueprint_id}/obj-policy-import", {"policies": [
            {"id": batch, "label": label, "description": "", "tags": [], "visible": True,
             "policy_type_name": "batch", "attributes": {"subpolicies": [pipeline]},
             "user_data": "{\"isSausage\": true}"},
            {"id": pipeline, "label": f"{label} pipeline", "description": "", "tags": [],
             "visible": False, "policy_type_name": "pipeline",
             "attributes": {"first_subpolicy": attach, "second_subpolicy": None,
                            "resolver": None}},
            {"id": attach, "label": label, "description": "", "tags": [], "visible": False,
             "policy_type_name": "AttachSingleVLAN",
             "attributes": {"tag_type": tag_type, "vlan_id": None, "vn_node_id": vn_node_id}},
        ]})
        return {"id": batch, "label": label, "tagging": [tag_type]}

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
        """PATCH a VN. 'bound_to' entries (system id/label) of ESI switches are
        resolved to their redundancy group; every remapping is reported in
        `bound_to_resolution`."""
        notes: list[dict] = []
        if "bound_to" in payload:
            resolved, notes = self._resolve_bound_to(blueprint_id, payload["bound_to"])
            payload = {**payload, "bound_to": resolved}
        result = self._patch(f"/blueprints/{blueprint_id}/virtual-networks/{vn_id}", payload)
        if notes:
            result = {**(result if isinstance(result, dict) else {}), "bound_to_resolution": notes}
        return result

    def list_connectivity_templates(self, blueprint_id: str) -> list[dict]:
        """Blueprint CTs (source='blueprint', usable as `ct_id`, with the VN they
        attach) followed by the generic design primitives (source='design')."""
        result: list[dict] = []
        try:
            vns: dict[str, list] = {}
            for r in self._qe(
                blueprint_id,
                "node('ep_endpoint_policy', policy_type_name='batch', name='ct')"
                ".out().node('ep_endpoint_policy', policy_type_name='pipeline')"
                ".out().node('ep_endpoint_policy', name='att')"
                ".out().node('virtual_network', name='vn')",
            ):
                vns.setdefault(r["ct"]["id"], []).append(
                    {"id": r["vn"]["id"], "label": r["vn"].get("label")})
            for r in self._qe(
                blueprint_id,
                "node('ep_endpoint_policy', policy_type_name='batch', name='ct')",
            ):
                ct = r["ct"]
                if not ct.get("visible"):
                    continue
                result.append({
                    "id": ct["id"], "label": ct.get("label"),
                    "description": ct.get("description"), "source": "blueprint",
                    "virtual_networks": vns.get(ct["id"], []),
                })
        except (requests.exceptions.RequestException, ValueError, KeyError, TypeError):
            pass
        # OpenAPI 6.1 exposes /design/endpoint-policies; blueprint fallback for compat.
        try:
            data = self._get("/design/endpoint-policies")
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            data = self._get(f"/blueprints/{blueprint_id}/endpoint-policies")
        items = data.get("items", data) if isinstance(data, dict) else data
        raw = self._dict_values(items)
        design = self._slim(raw, "id", "label", "description")
        return result + [{**d, "source": "design"} for d in design]

    def ports_outside_vn(self, blueprint_id: str, vn_id: str, rows: list[dict]) -> list[str]:
        """Ports (rows of resolve_port_interfaces) whose switch is not covered by the VN's
        `bound_to`: Apstra rejects a CT application there."""
        bound = {b.get("system_id") for b in
                 self.get_virtual_network(blueprint_id, vn_id, enrich=False).get("bound_to") or []}
        return [f"{r['switch']}:{r['port']}" for r in rows
                if r["switch_id"] not in bound
                and self._resolve_system_id_for_bound_to(blueprint_id, r["switch_id"]) not in bound]

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
        return self._patch(
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
        vn = self.get_virtual_network(blueprint_id, vn_id, enrich=False)
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
            rt5 = (l3.get("export_route_targets") or vn.get("l3_export_route_targets")
                   or vn.get("route_target"))
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
        raw = data["items"] if isinstance(data, dict) and "items" in data else data
        return self._slim(self._dict_values(raw), "id", "label", "vrf_name", "sz_type", "vni_id")

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
            rt5_exp = sz.get("export_route_targets") or sz.get("route_target")
            if not rt5_exp:
                raise ValueError(f"No RT5 export found on Security Zone '{sz_id}'.")
            payload["export_route_targets"] = rt5_exp

        if enable_irt:
            irt = (sz.get("import_route_targets") or sz.get("export_route_targets")
                   or sz.get("route_target"))
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

