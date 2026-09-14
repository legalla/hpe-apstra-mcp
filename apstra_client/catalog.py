"""Design catalog and reference data: resources, configlets, property sets, tasks, version."""

import ipaddress
import requests
from typing import Any, Optional


class CatalogMixin:
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
