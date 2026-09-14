"""Blueprint lifecycle: create, inspect, diff, build errors, commit."""

import ipaddress
import requests
from typing import Any, Optional


class BlueprintsMixin:
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

