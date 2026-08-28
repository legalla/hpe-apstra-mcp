"""
Client for the Juniper Apstra REST API.
"""

import ipaddress
import requests
import urllib3
from typing import Any, Optional

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ApstraClient:
    """HTTP client for the Apstra API."""

    _SPEED_MAP = {
        "1g": "1G",   "1000m": "1G",
        "10g": "10G", "10000m": "10G",
        "25g": "25G",
        "40g": "40G",
        "100g": "100G",
        "400g": "400G",
    }

    @staticmethod
    def _slim(items: list[dict], *keys: str) -> list[dict]:
        """Filter each dict of a list to keep only the specified keys."""
        return [{k: item[k] for k in keys if k in item} for item in items]

    def __init__(self, host: str, username: str, password: str, verify_ssl: bool = False):
        self.base_url   = f"https://{host}/api"
        self.username   = username
        self.password   = password
        self.verify_ssl = verify_ssl
        self.session    = requests.Session()
        self.token: Optional[str] = None

    # ── Auth ─────────────────────────────────────────────────────────────

    def login(self) -> None:
        resp = self.session.post(
            f"{self.base_url}/user/login",
            json={"username": self.username, "password": self.password},
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        self.token = resp.json()["token"]
        self.session.headers.update(
            {"AuthToken": self.token, "Content-Type": "application/json"}
        )

    def logout(self) -> None:
        if self.token:
            self.session.post(f"{self.base_url}/user/logout", verify=self.verify_ssl)
            self.token = None

    def _ensure_logged_in(self) -> None:
        if not self.token:
            self.login()

    def _raise_for_status(self, r: requests.Response) -> None:
        """Enriched raise_for_status: includes the response body in the error message."""
        if not r.ok:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            raise requests.HTTPError(
                f"{r.status_code} {r.reason} — {detail}",
                response=r,
            )

    def _get(self, path: str, params: dict | None = None) -> Any:
        self._ensure_logged_in()
        r = self.session.get(f"{self.base_url}{path}", params=params, verify=self.verify_ssl)
        self._raise_for_status(r)
        return r.json()

    def _post(self, path: str, body: dict) -> Any:
        self._ensure_logged_in()
        r = self.session.post(f"{self.base_url}{path}", json=body, verify=self.verify_ssl)
        self._raise_for_status(r)
        return r.json()

    def _patch(self, path: str, body: dict) -> Any:
        self._ensure_logged_in()
        r = self.session.patch(f"{self.base_url}{path}", json=body, verify=self.verify_ssl)
        self._raise_for_status(r)
        return r.json()

    def _put(self, path: str, body: dict) -> Any:
        self._ensure_logged_in()
        r = self.session.put(f"{self.base_url}{path}", json=body, verify=self.verify_ssl)
        self._raise_for_status(r)
        return r.json()

    def _delete(self, path: str) -> None:
        self._ensure_logged_in()
        r = self.session.delete(f"{self.base_url}{path}", verify=self.verify_ssl)
        r.raise_for_status()

    @staticmethod
    def _extract_items(data: Any) -> list[dict]:
        """Normalize listed responses coming from different API variants."""
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return items
            rows = data.get("data")
            if isinstance(rows, list):
                return rows
            result = data.get("result")
            if isinstance(result, list):
                return result
            return []
        if isinstance(data, list):
            return data
        return []

    # ── Blueprints ────────────────────────────────────────────────────────

    def list_blueprints(self) -> list[dict]:
        items = self._get("/blueprints").get("items", [])
        return self._slim(items, "id", "label", "status", "design")

    def create_blueprint(self, label: str, template_id: str, init_type: str = "template_reference") -> dict:
        return self._post("/blueprints", {"label": label, "template_id": template_id, "init_type": init_type})

    def get_blueprint(self, blueprint_id: str) -> dict:
        return self._get(f"/blueprints/{blueprint_id}")

    def get_blueprint_anomalies(self, blueprint_id: str) -> list[dict]:
        return self._get(f"/blueprints/{blueprint_id}/anomalies").get("items", [])

    def get_blueprint_build_errors(self, blueprint_id: str) -> dict:
        """Build (staging) errors of the blueprint = Uncommitted > Build Errors tab.

        Distinct from anomalies (runtime telemetry). Flattens the errors per
        node/relationship into a single list with message, type, category,
        severity and suggested resolutions.
        """
        data = self._get(f"/blueprints/{blueprint_id}/errors")
        items: list[dict] = []
        for scope in ("nodes", "relationships"):
            group = data.get(scope) or {}
            for entity_id, errs in group.items():
                for e in errs or []:
                    items.append({
                        "scope": scope,
                        "entity_id": entity_id,
                        "severity": e.get("severity"),
                        "message": e.get("message"),
                        "error_type": e.get("error_type"),
                        "display_category": e.get("display_category"),
                        "entity_type": e.get("entity_type"),
                        "resolutions": [
                            {"category": r.get("category"), "hint": r.get("hint")}
                            for r in (e.get("resolutions") or [])
                        ],
                    })
        return {
            "blueprint_id": blueprint_id,
            "errors_count": data.get("errors_count", len(items)),
            "warnings_count": data.get("warnings_count", 0),
            "version": data.get("version"),
            "errors": items,
        }

    def get_blueprint_nodes(self, blueprint_id: str, node_type: Optional[str] = None) -> list[dict]:
        params = {"node_type": node_type} if node_type else None
        data = self._get(f"/blueprints/{blueprint_id}/nodes", params=params)
        if isinstance(data, dict) and "nodes" in data:
            nodes = list(data["nodes"].values())
        else:
            nodes = data if isinstance(data, list) else []
        return self._slim(nodes, "id", "label", "hostname", "role", "system_type", "asn", "deploy_mode")

    def get_blueprint_diff(self, blueprint_id: str) -> dict:
        return self._get(f"/blueprints/{blueprint_id}/diff")

    def get_blueprint_logical_diff(self, blueprint_id: str) -> dict:
        """Logical diff (staging) = Uncommitted > Logical Diff tab.

        Flattens the /diff response (category -> added/removed/changed) into a
        list of items {type, action, id, name}. Ignores empty categories.
        """
        # Internal API key -> label shown in the WebUI
        type_labels = {
            "endpoint_policies": "Connectivity Template",
            "security_zones": "Routing Zone",
            "virtual_network": "Virtual Network",
            "routing_policies": "Routing Policy",
            "routing_zone_constraint": "Routing Zone Constraint",
            "static_routes": "Static Route",
            "interface_policy": "Interface Policy",
            "fabric_policy": "Fabric Policy",
            "policy": "Policy",
            "configlet": "Configlet",
            "property_set": "Property Set",
            "dci_settings": "DCI Settings",
            "remote_gateway": "Remote Gateway",
        }
        data = self._get(f"/blueprints/{blueprint_id}/diff")
        digest = data.get("digest")
        items: list[dict] = []
        for key, val in data.items():
            if key == "digest" or not isinstance(val, dict):
                continue
            for action in ("added", "removed", "changed"):
                for entity_id, info in (val.get(action) or {}).items():
                    items.append({
                        "type": type_labels.get(key, key.replace("_", " ").title()),
                        "category": key,
                        "action": action,
                        "id": entity_id,
                        "name": (info or {}).get("label"),
                    })
        return {
            "blueprint_id": blueprint_id,
            "change_count": len(items),
            "digest": digest,
            "changes": items,
        }

    def check_blueprint_commit(self, blueprint_id: str) -> dict:
        """Validate staging without deploying. Returns errors and warnings."""
        self._ensure_logged_in()
        # Some Apstra versions use POST, others don't have this endpoint
        for method in ("post", "put"):
            r = getattr(self.session, method)(
                f"{self.base_url}/blueprints/{blueprint_id}/commit-check",
                json={},
                verify=self.verify_ssl,
            )
            if r.status_code != 405:
                self._raise_for_status(r)
                return r.json()
        # Fallback: diff + anomalies for a pre-commit overview
        diff = self._get(f"/blueprints/{blueprint_id}/diff")
        anomalies = self._get(f"/blueprints/{blueprint_id}/anomalies").get("items", [])
        return {
            "method": "fallback_diff_anomalies",
            "diff": diff,
            "anomalies": anomalies,
            "anomaly_count": len(anomalies),
        }

    def commit_blueprint(self, blueprint_id: str, description: str = "") -> dict:
        """Deploy (commit) the staging changes to the devices.

        Apstra requires the current staging version: it is read via diff-status
        then the deployment is triggered with PUT /deploy (a POST returns 404).
        """
        ds = self._get(f"/blueprints/{blueprint_id}/diff-status")
        staging_version = ds.get("staging_version")
        if staging_version is None:
            raise ValueError(
                "Staging version not found (diff-status): cannot commit the "
                "blueprint.")
        self._ensure_logged_in()
        r = self.session.put(
            f"{self.base_url}/blueprints/{blueprint_id}/deploy",
            json={"version": staging_version, "description": description},
            verify=self.verify_ssl,
        )
        self._raise_for_status(r)
        return {
            "status": "deploy_requested",
            "staging_version": staging_version,
            "deployed_version_before": ds.get("deployed_version"),
            "description": description,
            "note": (
                "Deployment triggered (asynchronous): convergence toward the "
                "devices is handled by Apstra. Check the state via "
                "diff-status/anomalies."
            ),
        }

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

    def delete_virtual_network(self, blueprint_id: str, vn_id: str) -> None:
        self._delete(f"/blueprints/{blueprint_id}/virtual-networks/{vn_id}")

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
        except Exception:
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
            except Exception:
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
        except Exception:
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
        except Exception:
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
        except Exception:
            return []
        if isinstance(data, dict):
            return data.get("items", []) or []
        return data if isinstance(data, list) else []

    def _serial_to_system(self, blueprint_id: str) -> dict:
        """Build a mapping serial number (telemetry system_id) -> switch info."""
        mapping: dict[str, dict] = {}
        try:
            items = self._qe(blueprint_id, "node('system', name='sys')")
        except Exception:
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

    # ── Cabling matrix (endpoint -> port -> leaf -> spine) ─────────────

    def _topology_links(self, blueprint_id: str) -> list[dict]:
        """Return the unique physical links of the blueprint.

        Each link: {a_system, a_role, a_port, b_system, b_role, b_port, role}.
        Self-loops and duplicates (half-links) are eliminated.
        """
        query = (
            "node('system', name='s1')"
            ".out('hosted_interfaces').node('interface', name='i1')"
            ".out('link').node('link', name='l')"
            ".in_('link').node('interface', name='i2')"
            ".in_('hosted_interfaces').node('system', name='s2')"
        )
        seen: set = set()
        links: list[dict] = []
        for it in self._qe(blueprint_id, query):
            s1, i1, s2, i2, l = (
                it.get("s1", {}), it.get("i1", {}), it.get("s2", {}),
                it.get("i2", {}), it.get("l", {}),
            )
            if s1.get("id") and s1.get("id") == s2.get("id"):
                continue
            key = tuple(sorted([i1.get("id", ""), i2.get("id", "")]))
            if key in seen:
                continue
            seen.add(key)
            links.append({
                "a_system": s1.get("label"), "a_role": s1.get("role"),
                "a_type":   s1.get("system_type"), "a_port": i1.get("if_name"),
                "b_system": s2.get("label"), "b_role": s2.get("role"),
                "b_type":   s2.get("system_type"), "b_port": i2.get("if_name"),
                "role":     l.get("role"),
            })
        return links

    def _system_rack_map(self, blueprint_id: str) -> dict:
        """Map each system (label) to the name of its rack."""
        query = (
            "node('system', name='s')"
            ".out('part_of_rack').node('rack', name='r')"
        )
        mapping: dict[str, str] = {}
        try:
            for it in self._qe(blueprint_id, query):
                s = it.get("s", {})
                r = it.get("r", {})
                if s.get("label") and r.get("label"):
                    mapping[s["label"]] = r["label"]
        except Exception:
            pass
        return mapping

    def get_fabric_matrix(
        self,
        rack: str | None = None,
        blueprint_id: str | None = None,
    ) -> dict:
        """Hierarchical cabling matrix endpoint -> port -> leaf -> spine.

        Returns, organized by rack and by leaf:
          - the uplinks of each leaf to the spines (leaf port -> spine:port);
          - the endpoints (generic systems) connected to each leaf, with the
            leaf port.
        Also provides 'rows': a flat, usable list (table) linking
        each endpoint to its leaf, port and upstream spines.

        'rack' filters on a rack (exact name or fragment, case-insensitive).
        'blueprint_id' omitted -> all blueprints are traversed.
        """
        rack_q = rack.lower() if rack else None

        if blueprint_id:
            blueprints = [blueprint_id]
        else:
            blueprints = [b["id"] for b in self.list_blueprints() if b.get("id")]

        result_bps: list[dict] = []
        flat_rows: list[dict] = []

        for bp in blueprints:
            links = self._topology_links(bp)
            rack_map = self._system_rack_map(bp)

            # leaf -> list of uplinks to spines
            leaf_uplinks: dict[str, list[dict]] = {}
            # leaf -> list of connected endpoints
            leaf_endpoints: dict[str, list[dict]] = {}

            for ln in links:
                # Normalize: identify the leaf side and the facing side
                if ln["role"] == "spine_leaf":
                    if ln["a_role"] == "leaf":
                        leaf, leaf_port = ln["a_system"], ln["a_port"]
                        spine, spine_port = ln["b_system"], ln["b_port"]
                    else:
                        leaf, leaf_port = ln["b_system"], ln["b_port"]
                        spine, spine_port = ln["a_system"], ln["a_port"]
                    leaf_uplinks.setdefault(leaf, []).append({
                        "leaf_port": leaf_port,
                        "spine": spine,
                        "spine_port": spine_port,
                    })
                elif ln["role"] == "to_generic":
                    if ln["a_role"] == "leaf":
                        leaf, leaf_port = ln["a_system"], ln["a_port"]
                        endpoint, ep_port = ln["b_system"], ln["b_port"]
                    else:
                        leaf, leaf_port = ln["b_system"], ln["b_port"]
                        endpoint, ep_port = ln["a_system"], ln["a_port"]
                    leaf_endpoints.setdefault(leaf, []).append({
                        "endpoint": endpoint,
                        "endpoint_port": ep_port,
                        "leaf_port": leaf_port,
                    })

            # Group by rack
            racks: dict[str, dict] = {}
            all_leaves = set(leaf_uplinks) | set(leaf_endpoints)
            for leaf in sorted(all_leaves):
                rack_name = rack_map.get(leaf, "(no-rack)")
                if rack_q and rack_q not in rack_name.lower():
                    continue
                uplinks = sorted(leaf_uplinks.get(leaf, []),
                                 key=lambda x: (x["leaf_port"] or ""))
                endpoints = sorted(leaf_endpoints.get(leaf, []),
                                   key=lambda x: (x["leaf_port"] or ""))
                racks.setdefault(rack_name, {"rack": rack_name, "leaves": []})
                racks[rack_name]["leaves"].append({
                    "leaf": leaf,
                    "uplinks": uplinks,
                    "endpoints": endpoints,
                })

                # Flat usable rows
                spine_summary = sorted({u["spine"] for u in uplinks})
                for ep in endpoints:
                    flat_rows.append({
                        "blueprint_id": bp,
                        "rack": rack_name,
                        "endpoint": ep["endpoint"],
                        "endpoint_port": ep["endpoint_port"],
                        "leaf": leaf,
                        "leaf_port": ep["leaf_port"],
                        "spines": spine_summary,
                        "uplinks": uplinks,
                    })

            if racks:
                result_bps.append({
                    "blueprint_id": bp,
                    "racks": list(racks.values()),
                })

        return {
            "blueprint_id": blueprint_id,
            "blueprints_searched": blueprints,
            "filter": {"rack": rack},
            "row_count": len(flat_rows),
            "rows": flat_rows,
            "topology": result_bps,
        }

    # ── Cabling matrix via /cabling-map (endpoint -> port -> leaf -> spine) ──

    # Role hierarchy to orient each link: the switch of the local fabric
    # (leaf/border, then spine, then superspine) is placed at A;
    # the external end (server / generic / remote DC) at B.
    _CABLING_ROLE_ORDER = {
        "leaf": 0, "access": 0, "border": 0,
        "spine": 1, "superspine": 2,
        "generic": 3, "server": 3, "l3_server": 3,
    }

    @staticmethod
    def _cabling_endpoint_info(endpoint: dict) -> dict:
        iface = endpoint.get("interface", {}) or {}
        system = endpoint.get("system", {}) or {}
        return {
            "system": system.get("label", ""),
            "role": system.get("role", ""),
            "interface": iface.get("if_name", ""),
            "ip": iface.get("ipv4_addr"),
            "state": iface.get("operation_state"),
        }

    @staticmethod
    def _cabling_category(role_a: str, role_b: str) -> str:
        roles = {role_a, role_b}
        if roles == {"leaf", "spine"}:
            return "leaf-spine"
        if "generic" in roles or "server" in roles or "l3_server" in roles:
            return "endpoint-leaf"
        if roles == {"spine", "superspine"}:
            return "spine-superspine"
        return f"{role_a}-{role_b}"

    def cabling_matrix(
        self,
        blueprint_id: str | None = None,
        rack: str | None = None,
        role_filter: str | None = None,
    ) -> dict:
        """Cabling matrix of a blueprint via /blueprints/{bp}/cabling-map.

        Each physical link is normalized into a line oriented A -> B:
        the switch of the local fabric (leaf/border, spine, superspine) at A,
        the external end (server/generic/remote DC) at B, with role, port,
        IP and operational state of each side, and a link category
        (leaf-spine, endpoint-leaf, spine-superspine).

        - 'blueprint_id' omitted: all blueprints are traversed.
        - 'rack': keep only the links with one end in this rack
          (exact name or fragment, case-insensitive).
        - 'role_filter': keep only one link category (e.g. 'leaf-spine',
          'endpoint-leaf', 'spine-superspine').
        """
        rack_q = rack.lower() if rack else None
        role_q = role_filter.lower() if role_filter else None

        if blueprint_id:
            targets = [(blueprint_id, None)]
        else:
            targets = [(b["id"], b.get("label")) for b in self.list_blueprints() if b.get("id")]

        rows: list[dict] = []
        for bp, bp_label in targets:
            rack_map = self._system_rack_map(bp) if rack_q else {}
            data = self._get(f"/blueprints/{bp}/cabling-map")
            links = data.get("links", []) if isinstance(data, dict) else []
            for link in links:
                endpoints = link.get("endpoints", [])
                if len(endpoints) != 2:
                    continue
                ep1 = self._cabling_endpoint_info(endpoints[0])
                ep2 = self._cabling_endpoint_info(endpoints[1])
                order = self._CABLING_ROLE_ORDER
                if order.get(ep1["role"], 2) <= order.get(ep2["role"], 2):
                    a, b = ep1, ep2
                else:
                    a, b = ep2, ep1
                category = self._cabling_category(a["role"], b["role"])
                if role_q and category.lower() != role_q:
                    continue
                if rack_q:
                    racks = {rack_map.get(a["system"], ""), rack_map.get(b["system"], "")}
                    if not any(rack_q in (r or "").lower() for r in racks):
                        continue
                rows.append({
                    "blueprint_id": bp,
                    "blueprint": bp_label,
                    "link_id": link.get("id", ""),
                    "link_type": link.get("type", ""),
                    "speed": link.get("speed", ""),
                    "category": category,
                    "a_system": a["system"], "a_role": a["role"],
                    "a_interface": a["interface"], "a_ip": a["ip"], "a_state": a["state"],
                    "b_system": b["system"], "b_role": b["role"],
                    "b_interface": b["interface"], "b_ip": b["ip"], "b_state": b["state"],
                })

        rows.sort(key=lambda r: (r["category"], r["a_system"] or "", r["a_interface"] or ""))
        by_category: dict[str, int] = {}
        for r in rows:
            by_category[r["category"]] = by_category.get(r["category"], 0) + 1
        return {
            "blueprint_id": blueprint_id,
            "filter": {"rack": rack, "role_filter": role_filter},
            "link_count": len(rows),
            "by_category": by_category,
            "links": rows,
        }

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
        except Exception:
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

    # ── Revisions / Configuration rollback (UC#4) ──────────────────────


    def list_blueprint_revisions(
        self, blueprint_id: str, limit: int = 20
    ) -> dict:
        """List the revisions (committed config versions) of a blueprint.

        Each revision is a restore point that can be rolled back to without CLI.
        The revisions are sorted from most recent to oldest;
        'limit' bounds the number returned (0 = all).
        """
        items = self._get(f"/blueprints/{blueprint_id}/revisions").get("items", [])
        slim = self._slim(
            items, "revision_id", "created_at", "user", "user_ip",
            "description", "keep", "aos_version", "rollback_eligible",
        )

        def _rev_key(r: dict):
            try:
                return int(r.get("revision_id"))
            except (TypeError, ValueError):
                return r.get("created_at") or ""

        slim.sort(key=_rev_key, reverse=True)
        if limit and limit > 0:
            slim = slim[:limit]
        return {
            "blueprint_id": blueprint_id,
            "total_revisions": len(items),
            "returned": len(slim),
            "revisions": slim,
        }

    def rollback_blueprint(self, blueprint_id: str, revision_id: str) -> dict:
        """Restore the blueprint to a previous revision (without CLI).

        Triggers a config rollback via the Apstra API. The push to the
        devices is then handled by the Apstra deployer (minimal convergence
        time specific to the vendor). The 'revision_id' must be
        eligible for rollback (see list_blueprint_revisions).
        """
        revision_id = str(revision_id)
        result = self._post(
            f"/blueprints/{blueprint_id}/rollback",
            {"revision_id": revision_id},
        )
        return {
            "blueprint_id": blueprint_id,
            "rolled_back_to": revision_id,
            "method": "api_rollback",
            "note": (
                "Rollback triggered via the API (no CLI). Convergence to "
                "the devices is ensured by the Apstra deployer."
            ),
            "result": result,
        }

    def revert_staging(self, blueprint_id: str, confirmed: bool = False) -> dict:
        """Revert the UNCOMMITTED staging changes.

        Restores the staging to the last committed/deployed version
        (deployed_version): all uncommitted changes are discarded. This is
        the equivalent of the 'Revert' button in the Apstra UI (Time Voyager).

        DESTRUCTIVE and LOCKED operation: as long as confirmed=False, nothing
        is done and the tool returns 'confirmation_required' with the question
        to ask. Call again with confirmed=True to actually trigger the revert.
        """
        ds = self._get(f"/blueprints/{blueprint_id}/diff-status")
        staging_version = ds.get("staging_version")
        deployed_version = ds.get("deployed_version")

        if staging_version is not None and deployed_version is not None \
                and staging_version == deployed_version:
            return {
                "blueprint_id": blueprint_id,
                "status": "nothing_to_revert",
                "staging_version": staging_version,
                "deployed_version": deployed_version,
                "note": ("No changes in staging: staging_version == "
                         "deployed_version. Nothing to revert."),
            }

        if not confirmed:
            return {
                "blueprint_id": blueprint_id,
                "status": "confirmation_required",
                "staging_version": staging_version,
                "deployed_version": deployed_version,
                "question_to_ask": (
                    "Do you want to cancel the change and trigger a revert?"),
                "if_yes": ("call revert_staging again with confirmed=True (the "
                           "staging changes will be permanently discarded)."),
                "if_no": "do nothing: the changes remain in staging.",
            }

        # Revert target: the revision matching the deployed version
        # (= last commit). Otherwise, the most recent eligible revision.
        revs = self._get(f"/blueprints/{blueprint_id}/revisions").get("items", [])

        def _rid(r):
            try:
                return int(r.get("revision_id"))
            except (TypeError, ValueError):
                return -1

        target = None
        if deployed_version is not None:
            for r in revs:
                if _rid(r) == int(deployed_version):
                    target = r
                    break
        if target is None:
            eligible = [r for r in revs if r.get("rollback_eligible")]
            if eligible:
                target = max(eligible, key=_rid)
        if target is None:
            raise ValueError(
                "No eligible revision found for the staging revert.")

        revision_id = str(target.get("revision_id"))
        result = self._post(
            f"/blueprints/{blueprint_id}/rollback",
            {"revision_id": revision_id},
        )
        return {
            "blueprint_id": blueprint_id,
            "status": "reverted",
            "reverted_to_revision": revision_id,
            "staging_version_before": staging_version,
            "deployed_version": deployed_version,
            "method": "api_rollback_to_deployed",
            "note": ("Revert done: the staging is restored to the last "
                     "committed version. Uncommitted changes were discarded."),
            "result": result,
        }

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
                except Exception:
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
                except Exception:
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
                except Exception:
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
            except Exception:
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

    # ── Add a VLAN on a port of a single leaf (UC#1) ────────────────

    def instantiate_port(
        self,
        blueprint_id: str,
        leaf_id: str,
        leaf_label: str,
        port: str,
        gs_label: str | None = None,
    ) -> dict:
        """Instantiate an unused port by creating a minimal generic system on it.

        In Apstra, a port that faces no system has no interface node
        in the graph and is therefore not a Connectivity Template application
        point. This method creates a single-port generic system on the port,
        which instantiates the interface node and makes the port assignable.

        Returns {interface_id, gs_id, gs_label, link_ids}. To cancel, pass
        link_ids to delete_switch_system_links().
        """
        # Leaf interface map: the design catalog (/design/interface-maps)
        # returns 404 in this environment, so we read the graph node.
        im_rows = self._qe(
            blueprint_id,
            f"node('system', id='{leaf_id}')"
            f".out('interface_map').node('interface_map', name='im')",
        )
        if not im_rows:
            raise ValueError(
                f"Interface map not found for leaf '{leaf_label}'.")
        im = im_rows[0]["im"]
        entry = next((i for i in im.get("interfaces", [])
                      if i.get("name") == port), None)
        if not entry or not entry.get("mapping") or len(entry["mapping"]) < 2:
            raise ValueError(
                f"Port '{port}' absent from the interface map of '{leaf_label}' "
                f"or without a usable transformation.")
        # transformation_id = 2nd element of the interface's 'mapping' array.
        transformation_id = entry["mapping"][1]
        speed = entry.get("speed") or {}
        speed_value = speed.get("value")
        if not speed_value:
            raise ValueError(f"Speed of port '{port}' unknown.")
        # Single-port logical device matching the speed (AOS-1x<G>-1).
        ld_name = f"AOS-1x{int(speed_value)}-1"
        ld = self._get(f"/design/logical-devices/{ld_name}")
        ld_dict = {k: ld[k] for k in ("id", "display_name", "panels")
                   if k in ld}
        gs_label = gs_label or f"GS-{leaf_label}-{port.replace('/', '-')}"
        hostname = gs_label.lower().replace("_", "-")[:32]
        body = {
            "links": [{
                "switch": {
                    "system_id": leaf_id,
                    "transformation_id": transformation_id,
                    "if_name": port,
                },
                # empty 'system' + 'new_system_index' at the link level: the only
                # combination accepted by switch-system-links to create a
                # new system (index 0 under 'system' is not recognized).
                "system": {},
                "new_system_index": 0,
                "lag_mode": None,
                "link_group_label": "link1",
            }],
            "new_systems": [{
                "system_type": "server",
                "label": gs_label,
                "hostname": hostname,
                "logical_device": ld_dict,
                "port_channel_id_min": 0,
                "port_channel_id_max": 0,
            }],
        }
        resp = self._post(
            f"/blueprints/{blueprint_id}/switch-system-links", body)
        link_ids = resp.get("ids", []) if isinstance(resp, dict) else []
        # Re-resolve the now-instantiated interface node. The contextual
        # graph is rebuilt asynchronously after the link creation:
        # retry a few times until the interface appears.
        import time
        iface_id = None
        for _ in range(20):
            ifs = self._qe(
                blueprint_id,
                f"node('system', id='{leaf_id}')"
                f".out('hosted_interfaces')"
                f".node('interface', if_name='{port}', name='i')",
            )
            if ifs:
                iface_id = ifs[0]["i"]["id"]
                break
            time.sleep(0.5)
        gs_id = next(
            (r["s"]["id"] for r in self._qe(
                blueprint_id, "node('system', name='s')")
             if r["s"].get("label") == gs_label),
            None)
        return {
            "interface_id": iface_id,
            "gs_id": gs_id,
            "gs_label": gs_label,
            "link_ids": link_ids,
        }

    def delete_switch_system_links(
        self, blueprint_id: str, link_ids: list,
    ) -> dict:
        """Delete switch<->system links (and the orphaned generic system).

        Used in particular to cancel a port instantiation created by
        instantiate_port().
        """
        return self._post(
            f"/blueprints/{blueprint_id}/delete-switch-system-links",
            {"link_ids": link_ids})

    def add_vlan_preflight(
        self, blueprint_id: str, leaf: str, port: str | None = None,
    ) -> dict:
        """Context to retrieve BEFORE add_vlan_to_port to ask the right questionnaire.

        Indicates notably whether the VN will be forced to 'vxlan' (leaf in an ESI
        pair), hence whether a Routing Zone (VRF) will be REQUIRED, and provides
        the list of selectable VRFs. Allows the assistant to ask ALL the
        missing questions at once, without triggering an error.
        """
        rows = self._qe(
            blueprint_id,
            f"node('system', system_type='switch', id='{leaf}', name='s')",
        )
        if not rows:
            rows = self._qe(
                blueprint_id,
                f"node('system', system_type='switch', label='{leaf}', name='s')",
            )
        if not rows:
            raise ValueError(f"Leaf '{leaf}' not found in the blueprint.")
        leaf_node = rows[0]["s"]
        leaf_id = leaf_node["id"]
        leaf_label = leaf_node.get("label", leaf)

        bound_id = self._resolve_system_id_for_bound_to(blueprint_id, leaf_id)
        is_esi = bound_id != leaf_id
        # An ESI leaf cannot switch a pure VLAN -> VN forced to vxlan, which
        # requires a Routing Zone (VRF).
        vxlan_required = is_esi
        routing_zone_required = vxlan_required

        raw = self._get(f"/blueprints/{blueprint_id}/security-zones")
        items = raw.get("items", raw) if isinstance(raw, dict) else raw
        zones = list(items.values()) if isinstance(items, dict) else items
        routing_zones = [{
            "id": z.get("id"),
            "vrf_name": z.get("vrf_name"),
            "label": z.get("label"),
        } for z in zones if (z.get("vrf_name") or "").lower() != "default"]

        # Port state (possible instantiation).
        port_state = None
        if port:
            ifs = self._qe(
                blueprint_id,
                f"node('system', id='{leaf_id}')"
                f".out('hosted_interfaces')"
                f".node('interface', if_name='{port}', name='i')",
            )
            port_state = "exists" if ifs else "unused_will_be_instantiated"

        questions = [
            "Should the VLAN be 'tagged' (802.1Q) or 'untagged' (native) on "
            "the port? (otherwise: no Connectivity Template, the port will NOT "
            "be connected to the VLAN)",
            "Do you want IPv4 connectivity? If yes: which subnet (e.g. "
            "10.20.30.0/24) and which virtual gateway (default: 1st usable "
            "address)?",
        ]
        if routing_zone_required:
            questions.append(
                "In which Routing Zone (VRF) should this VLAN be placed? "
                "(mandatory because the VN will be of type vxlan): "
                + ", ".join(z["vrf_name"] or z["label"] or z["id"]
                            for z in routing_zones))

        return {
            "blueprint_id": blueprint_id,
            "leaf": leaf_label,
            "leaf_id": leaf_id,
            "is_esi": is_esi,
            "vxlan_required": vxlan_required,
            "routing_zone_required": routing_zone_required,
            "routing_zones": routing_zones,
            "port": port,
            "port_state": port_state,
            "questions_to_ask": questions,
            "note": (
                "Ask ALL the questions above at once (based on "
                "the missing information) BEFORE calling add_vlan_to_port. "
                "A Routing Zone (VRF) also becomes required if the user "
                "requests IPv4/DHCP connectivity or an L2VNI, even outside ESI."
            ),
        }

    def add_vlan_to_port(
        self,
        blueprint_id: str,
        leaf: str,
        vlan_id: int,
        port: str | None = None,
        tagging: str | None = None,
        label: str | None = None,
        vn_type: str = "vlan",
        security_zone_id: str | None = None,
        vni: int | None = None,
        l2_vni: int | None = None,
        ipv4_subnet: str | None = None,
        virtual_gateway_ipv4: str | None = None,
        dhcp_relay: bool = False,
        instantiate_port: bool = True,
        gs_label: str | None = None,
        commit: bool = False,
        commit_confirmed: bool = False,
    ) -> dict:
        """Create a VLAN (Virtual Network) on a leaf and assign it to a port.

        Creates a Virtual Network local to the indicated leaf (no impact on the
        other leafs). If 'port' is provided WITH 'tagging' ('tagged' or
        'untagged'), the VN is assigned to the port via Apstra's native
        mechanism: the Connectivity Template is AUTO-CREATED by Apstra (the
        server never creates a CT manually). The commit (push to the device)
        only happens if commit=True.

        'tagging':
          - 'tagged'   -> the VLAN is tagged (802.1Q) on the port;
          - 'untagged' -> the VLAN is native/untagged on the port;
          - None       -> NO CT is created: the VN is created but not assigned
            to the port (the user must be informed that no connectivity has
            been set up on the port).

        L3/L2 options (all optional):
          - 'l2_vni': associate an L2 VNI (forces a 'vxlan' VN with this VNI);
          - 'ipv4_subnet' (e.g. '10.20.30.0/24'): configure an IP gateway
            (SVI/anycast) on the VLAN; requires a security zone (L3);
          - 'virtual_gateway_ipv4': gateway address (default the
            first usable address of the subnet);
          - 'dhcp_relay': enable DHCP relay on the VLAN.

        On a leaf in an ESI pair (redundancy group), or if an L3/L2VNI option
        is requested, the VN automatically becomes 'vxlan' (security zone + VNI)
        while remaining limited to this logical leaf. A 'vxlan' VN REQUIRES a
        Routing Zone (VRF): if 'security_zone_id' is not provided, an error
        is raised with the list of available VRFs (the assistant must then
        ask the user for the VRF). 'security_zone_id' accepts a node
        id OR a VRF/label name.

        If 'port' is unused (no interface in the graph, facing
        no system) and 'instantiate_port' is true (default), the port is
        first instantiated (single-port generic system), which makes it
        assignable. 'gs_label': label of the created generic system.

        'leaf': node id or switch label. 'vlan_id': 1-4094. 'port': interface
        name (e.g. 'xe-0/0/0'). 'vn_type': 'vlan' (mono-leaf) or 'vxlan'.
        'security_zone_id': VRF (Routing Zone) for a vxlan VN (id or name).
        'vni': explicit VNI for a vxlan VN. 'commit': False by default.
        """
        if tagging is not None:
            tagging = tagging.lower()
            if tagging in ("tag", "tagged", "vlan_tagged"):
                tagging = "tagged"
            elif tagging in ("untag", "untagged"):
                tagging = "untagged"
            else:
                raise ValueError(
                    "'tagging' must be 'tagged', 'untagged' or None.")

        # Resolve the leaf (label -> node id)
        rows = self._qe(
            blueprint_id,
            f"node('system', system_type='switch', id='{leaf}', name='s')",
        )
        if not rows:
            rows = self._qe(
                blueprint_id,
                f"node('system', system_type='switch', label='{leaf}', name='s')",
            )
        if not rows:
            raise ValueError(f"Leaf '{leaf}' not found in the blueprint.")
        leaf_node = rows[0]["s"]
        leaf_id = leaf_node["id"]
        leaf_label = leaf_node.get("label", leaf)

        # If the leaf is in an ESI pair, bind the VN to the redundancy group (the two
        # ESI members = a single logical leaf, with no impact on the other leafs).
        bound_id = self._resolve_system_id_for_bound_to(blueprint_id, leaf_id)
        esi_pair = bound_id != leaf_id

        vlan_id = int(vlan_id)
        # Options requiring a 'vxlan' VN (L3 or L2VNI) or ESI pair.
        want_l3 = bool(ipv4_subnet) or dhcp_relay
        if l2_vni is not None or want_l3 or (esi_pair and vn_type == "vlan"):
            vn_type = "vxlan"
        vn_label = label or f"VLAN-{vlan_id}-{leaf_label}"
        payload = {
            "label": vn_label,
            "vn_type": vn_type,
            "bound_to": [{
                "system_id": bound_id,
                "vlan_id": vlan_id,
                "access_switch_node_ids": [],
            }],
        }
        if vn_type == "vlan":
            # For a pure VLAN (mono-leaf), the VN ID must equal the VLAN ID.
            payload["vn_id"] = str(vlan_id)
        else:  # vxlan: requires a security zone (VRF/Routing Zone) and a VNI
            # List of routing zones (excluding 'default', forbidden in VXLAN).
            raw = self._get(f"/blueprints/{blueprint_id}/security-zones")
            items = raw.get("items", raw) if isinstance(raw, dict) else raw
            zones = list(items.values()) if isinstance(items, dict) else items
            selectable = [z for z in zones
                          if (z.get("vrf_name") or "").lower() != "default"]

            sz = None
            if security_zone_id:
                # Accept a node id OR a VRF/label name.
                for z in zones:
                    if (z.get("id") == security_zone_id
                            or z.get("vrf_name") == security_zone_id
                            or z.get("label") == security_zone_id):
                        sz = z.get("id")
                        break
                if not sz:
                    available = ", ".join(
                        z.get("vrf_name") or z.get("label") or z.get("id")
                        for z in selectable) or "(none)"
                    raise ValueError(
                        f"Routing Zone (VRF) '{security_zone_id}' not found. "
                        f"Available VRFs: {available}.")
            else:
                # VRF not specified: DO NOT choose automatically. Ask
                # the user to choose among the available routing zones.
                available = [{
                    "id": z.get("id"),
                    "vrf_name": z.get("vrf_name"),
                    "label": z.get("label"),
                } for z in selectable]
                raise ValueError(
                    "This VN requires a Routing Zone (VRF) because it is of type "
                    "vxlan (leaf in an ESI pair, or L3/L2VNI option requested). "
                    "No VRF was specified: ASK the user "
                    "which Routing Zone to use, then retry with "
                    "'security_zone_id'. Available VRFs: "
                    f"{available}")
            payload["security_zone_id"] = sz
            chosen_vni = l2_vni if l2_vni is not None else (
                vni if vni is not None else 10000 + vlan_id)
            payload["vn_id"] = str(chosen_vni)

        # Option: IP gateway (SVI / anycast) on the VLAN.
        if ipv4_subnet:
            gw = virtual_gateway_ipv4
            if not gw:
                import ipaddress
                try:
                    net = ipaddress.ip_network(ipv4_subnet, strict=False)
                    gw = str(next(net.hosts()))
                except (ValueError, StopIteration):
                    gw = None
            payload["ipv4_subnet"] = ipv4_subnet
            payload["ipv4_enabled"] = True
            if gw:
                payload["virtual_gateway_ipv4"] = gw
                payload["virtual_gateway_ipv4_enabled"] = True

        # Option: DHCP relay on the VLAN.
        payload["dhcp_service"] = (
            "dhcpServiceEnabled" if dhcp_relay else "dhcpServiceDisabled")

        steps = []
        port_assignment = None
        instantiation = None
        gen_iface_id = None
        switch_iface_id = None

        # Port resolution BEFORE the VN creation: if a 'tagging' is
        # requested, the interface of the generic system facing the port is added
        # to the VN's 'endpoints', which makes Apstra AUTO-CREATE the Connectivity
        # Template (the server never creates a CT manually).
        if port:
            ifs = self._qe(
                blueprint_id,
                f"node('system', id='{leaf_id}')"
                f".out('hosted_interfaces')"
                f".node('interface', if_name='{port}', name='i')",
            )
            switch_iface_id = ifs[0]["i"]["id"] if ifs else None
            # Unused port (no interface node): instantiate it by creating
            # a minimal generic system on it, otherwise it stays unassignable.
            if not switch_iface_id and instantiate_port:
                try:
                    instantiation = self.instantiate_port(
                        blueprint_id, leaf_id, leaf_label, port,
                        gs_label=gs_label)
                    switch_iface_id = instantiation.get("interface_id")
                    steps.append({
                        "step": "instantiate_port",
                        "status": "applied",
                        "port": port,
                        "generic_system": instantiation.get("gs_label"),
                        "reason": (
                            "Unused port: single-port generic system "
                            "created to make the port assignable."
                        ),
                    })
                except Exception as exc:  # noqa: BLE001
                    steps.append({
                        "step": "instantiate_port",
                        "status": "failed",
                        "port": port,
                        "reason": f"Port instantiation failed: {exc}",
                    })

            if switch_iface_id and tagging:
                gen_iface_id = self._generic_side_interface(
                    blueprint_id, switch_iface_id)
                if gen_iface_id:
                    tag_type = ("vlan_tagged" if tagging == "tagged"
                                else "untagged")
                    payload["endpoints"] = [{
                        "interface_id": gen_iface_id,
                        "tag_type": tag_type,
                    }]

        vn = self.create_virtual_network(blueprint_id, payload)
        vn_id = vn.get("id") if isinstance(vn, dict) else None

        steps.insert(0, {
            "step": "create_vlan",
            "vn_label": vn_label,
            "vn_id": vn_id,
            "vlan_id": vlan_id,
            "leaf": leaf_label,
            "scope": ((("esi_pair vxlan (redundancy group, one logical leaf)"
                       if esi_pair else "single_leaf vlan")
                      + " — no impact on the other leafs")),
            "vn_type": vn_type,
            "options": {
                "l2_vni": payload.get("vn_id") if vn_type == "vxlan" else None,
                "ipv4_subnet": payload.get("ipv4_subnet"),
                "virtual_gateway_ipv4": payload.get("virtual_gateway_ipv4"),
                "dhcp_relay": dhcp_relay,
            },
        })

        # Summary of the port assignment.
        if port:
            if not switch_iface_id:
                steps.append({
                    "step": "assign_port",
                    "status": "skipped",
                    "reason": (
                        f"Interface '{port}' not found on {leaf_label}"
                        + ("." if instantiate_port
                           else " (instantiate_port=False).")),
                })
            elif not tagging:
                # No tagging => no CT created: the user must be
                # informed (VN created but port not connected to the VLAN).
                port_assignment = {
                    "port": port, "interface_id": switch_iface_id,
                    "tagging": None, "ct_created": False,
                    "instantiation": instantiation,
                }
                steps.append({
                    "step": "assign_port",
                    "status": "no_ct",
                    "port": port,
                    "reason": (
                        "No 'tagged'/'untagged' mode chosen: NO "
                        "Connectivity Template was created and the port is "
                        "NOT connected to this VLAN. The VN exists alone. Retry "
                        "with tagging='tagged' or 'untagged' to assign the "
                        "port."
                    ),
                })
            elif not gen_iface_id:
                steps.append({
                    "step": "assign_port",
                    "status": "failed",
                    "port": port,
                    "reason": (
                        "Interface of the generic system facing the port "
                        "not found: CT not auto-created. Check that the port "
                        "indeed faces a system."
                    ),
                })
            else:
                port_assignment = {
                    "port": port, "interface_id": switch_iface_id,
                    "tagging": tagging, "ct_created": True,
                    "generic_interface_id": gen_iface_id,
                    "instantiation": instantiation,
                }
                steps.append({
                    "step": "assign_port",
                    "status": "applied",
                    "port": port,
                    "tagging": tagging,
                    "reason": (
                        "VN assigned to the port as '%s'; Connectivity Template "
                        "auto-created by Apstra." % tagging),
                })

        commit_result = None
        commit_done = False
        if commit and not commit_confirmed:
            # Safety lock: a commit was requested but NOT confirmed.
            # We DO NOT commit. The assistant MUST ask the
            # confirmation question to the user, then call again with commit_confirmed=True.
            steps.append({
                "step": "commit",
                "status": "confirmation_required",
                "reason": (
                    "Commit requested but not confirmed. Changes in staging, "
                    "NOT deployed."),
                "question_to_ask": (
                    "The change is about to be committed — are you sure?"),
                "if_yes": (
                    "call add_vlan_to_port again with the same parameters + "
                    "commit=True AND commit_confirmed=True."),
                "if_no": (
                    "DO NOT commit. Then ask the question: 'Do you want to "
                    "cancel the change and trigger a revert?'. If YES -> "
                    "call revert_staging(confirmed=True). If NO -> do "
                    "nothing (the VN stays in staging) and provide a short summary."),
            })
        elif commit and commit_confirmed:
            commit_result = self.commit_blueprint(
                blueprint_id, description=f"Add {vn_label} on {leaf_label}")
            commit_done = True
            steps.append({"step": "commit", "status": "deployed"})
        else:
            steps.append({
                "step": "commit", "status": "staged",
                "reason": "commit=False: changes in staging, not deployed.",
            })

        return {
            "blueprint_id": blueprint_id,
            "vn_id": vn_id,
            "vn_label": vn_label,
            "vlan_id": vlan_id,
            "leaf": leaf_label,
            "tagging": tagging,
            "port_assignment": port_assignment,
            "commit_requested": bool(commit),
            "commit_confirmed": bool(commit_confirmed),
            "committed": commit_done,
            "commit_result": commit_result,
            "steps": steps,
            "note": (
                "VLAN of type '{vt}' local to the leaf: the commit only touches this "
                "leaf (no impact on the others). Minimal convergence time "
                "handled by the Apstra deployer.".format(vt=vn_type)
            ),
        }

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
        except Exception:
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
                except Exception:
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

    # ── Design ────────────────────────────────────────────────────────────

    def list_logical_devices(self) -> list[dict]:
        items = self._get("/design/logical-devices").get("items", [])
        return self._slim(items, "id", "label", "display_name")

    def list_interface_maps(self) -> list[dict]:
        items = self._get("/design/interface-maps").get("items", [])
        return self._slim(items, "id", "label", "logical_device_id")

    def list_rack_types(self) -> list[dict]:
        items = self._get("/design/rack-types").get("items", [])
        return self._slim(items, "id", "label", "description")

    def list_templates(self) -> list[dict]:
        items = self._get("/design/templates").get("items", [])
        return self._slim(items, "id", "label", "type")

    # ── Resources ─────────────────────────────────────────────────────────

    def list_asn_pools(self) -> list[dict]:
        items = self._get("/resources/asn-pools").get("items", [])
        return self._slim(items, "id", "label", "status", "used_count", "total")

    def list_ip_pools(self) -> list[dict]:
        items = self._get("/resources/ip-pools").get("items", [])
        return self._slim(items, "id", "label", "status", "used_count", "total")

    def list_vni_pools(self) -> list[dict]:
        items = self._get("/resources/vni-pools").get("items", [])
        return self._slim(items, "id", "label", "status", "used_count", "total")

    # ── Configlets ─────────────────────────────────────────────────────────

    def list_configlets(self) -> list[dict]:
        """List the global configlets (design catalog)."""
        items = self._get("/design/configlets").get("items", [])
        return self._slim(items, "id", "label", "description")

    def get_configlet(self, configlet_id: str) -> dict:
        """Get the detail of a global configlet (Jinja content, generators, etc.)."""
        return self._get(f"/design/configlets/{configlet_id}")

    def list_blueprint_configlets(self, blueprint_id: str) -> list[dict]:
        """List the configlets imported/assigned in a blueprint."""
        data = self._get(f"/blueprints/{blueprint_id}/configlets")
        if isinstance(data, dict) and "items" in data:
            raw = data["items"]
        else:
            raw = list(data.values()) if isinstance(data, dict) else data
        return self._slim(raw, "id", "label", "description")

    def get_blueprint_configlet(self, blueprint_id: str, configlet_id: str) -> dict:
        """Get the detail of a configlet in a blueprint (rendered content, conditions)."""
        return self._get(f"/blueprints/{blueprint_id}/configlets/{configlet_id}")

    # ── Property Sets ──────────────────────────────────────────────────────

    def list_property_sets(self) -> list[dict]:
        """List the global property sets (design catalog)."""
        items = self._get("/property-sets").get("items", [])
        return self._slim(items, "id", "label", "keys")

    def get_property_set(self, property_set_id: str) -> dict:
        """Get the detail of a global property set (keys/values)."""
        return self._get(f"/property-sets/{property_set_id}")

    def list_blueprint_property_sets(self, blueprint_id: str) -> list[dict]:
        """List the property sets imported in a blueprint."""
        data = self._get(f"/blueprints/{blueprint_id}/property-sets")
        if isinstance(data, dict) and "items" in data:
            raw = data["items"]
        else:
            raw = list(data.values()) if isinstance(data, dict) else data
        return self._slim(raw, "id", "label")

    def get_blueprint_property_set(self, blueprint_id: str, property_set_id: str) -> dict:
        """Get the detail of a property set in a blueprint."""
        return self._get(f"/blueprints/{blueprint_id}/property-sets/{property_set_id}")

    # ── Tasks ──────────────────────────────────────────────────────────────

    def list_tasks(self, blueprint_id: str | None = None) -> list[dict]:
        path = f"/blueprints/{blueprint_id}/tasks" if blueprint_id else "/tasks"
        items = self._get(path).get("items", [])
        return self._slim(items, "id", "status", "type", "submitted_at", "last_updated_at")

    def get_task(self, task_id: str, blueprint_id: str | None = None) -> dict:
        path = f"/blueprints/{blueprint_id}/tasks/{task_id}" if blueprint_id else f"/tasks/{task_id}"
        return self._get(path)

    # ── Version ────────────────────────────────────────────────────────────

    def get_version(self) -> dict:
        return self._get("/version")
