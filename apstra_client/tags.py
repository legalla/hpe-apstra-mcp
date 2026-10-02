"""Tags: catalog (design / blueprint), assignment to graph nodes, and tag-based selection."""

import time
import requests
from typing import Optional

# Node types that can be addressed by label when tagging.
_LABELLED_TYPES = ("system", "virtual_network", "security_zone", "redundancy_group")


class TagsMixin:
    @staticmethod
    def _q(value: str) -> str:
        """Value safe to embed in a Query Engine string literal."""
        value = str(value)
        if "'" in value or "\\" in value:
            raise ValueError(f"Unsupported character in {value!r}.")
        return value

    @staticmethod
    def _as_list(value) -> list:
        if value is None:
            return []
        return [value] if isinstance(value, (str, dict)) else list(value)

    # ── Catalog ──────────────────────────────────────────────────────

    @staticmethod
    def _tags_path(blueprint_id: Optional[str]) -> str:
        return f"/blueprints/{blueprint_id}/tags" if blueprint_id else "/design/tags"

    def list_tags(self, blueprint_id: str | None = None) -> list[dict]:
        """Tags of a blueprint, or of the design catalog when blueprint_id is None."""
        data = self._get(self._tags_path(blueprint_id))
        items = data.get("items", []) if isinstance(data, dict) else data
        return self._slim(self._dict_values(items), "id", "label", "description")

    def create_tag(self, label: str, description: str = "", blueprint_id: str | None = None) -> dict:
        return self._post(self._tags_path(blueprint_id),
                          {"label": label, "description": description or ""})

    def update_tag(
        self, tag_id: str, label: str | None = None, description: str | None = None,
        blueprint_id: str | None = None,
    ) -> dict:
        current = next((t for t in self.list_tags(blueprint_id) if t["id"] == tag_id), None)
        if current is None:
            raise ValueError(f"Tag '{tag_id}' not found.")
        body = {"label": label if label is not None else current["label"],
                "description": description if description is not None else (current.get("description") or "")}
        self._put(f"{self._tags_path(blueprint_id)}/{tag_id}", body)
        return {"id": tag_id, **body}

    def delete_tag(self, tag_id: str, blueprint_id: str | None = None) -> dict:
        self._delete(f"{self._tags_path(blueprint_id)}/{tag_id}")
        return {"deleted_tag": tag_id}

    # ── Resolution of what can be tagged ─────────────────────────────

    def _resolve_tag_target(self, blueprint_id: str, target) -> dict:
        """target: node id, node label (system / VN / routing zone), or
        {"system": leaf, "port": "ge-0/0/3"} for a switch interface."""
        if isinstance(target, dict):
            sysref, port = target.get("system"), target.get("port")
            if not sysref or not port:
                raise ValueError(f"Port target needs 'system' and 'port': {target!r}")
            rows = self.resolve_port_interfaces_any(blueprint_id, sysref, port)
            if not rows:
                raise ValueError(f"Port '{port}' not found on '{sysref}'.")
            return rows[0]
        ref = self._q(target)
        found = self._qe(blueprint_id, f"node(id='{ref}', name='n')")
        if not found:
            for t in _LABELLED_TYPES:
                found += self._qe(blueprint_id, f"node('{t}', label='{ref}', name='n')")
        if not found:
            raise ValueError(f"No node with id or label '{ref}'.")
        if len(found) > 1:
            raise ValueError(
                f"'{ref}' is ambiguous: {[(r['n']['type'], r['n']['id']) for r in found]}. Use the node id.")
        n = found[0]["n"]
        return {"id": n["id"], "type": n["type"], "label": n.get("label")}

    def resolve_port_interfaces_any(self, blueprint_id: str, system: str, port: str) -> list[dict]:
        """Switch interface node of (system id/label, port), facing a system or not."""
        s, p = self._q(system), self._q(port)
        rows = self._qe(
            blueprint_id,
            f"node('system', id='{s}', name='s').out('hosted_interfaces')"
            f".node('interface', if_name='{p}', name='i')") or self._qe(
            blueprint_id,
            f"node('system', label='{s}', name='s').out('hosted_interfaces')"
            f".node('interface', if_name='{p}', name='i')")
        return [{"id": r["i"]["id"], "type": "interface", "label": None,
                 "system": r["s"].get("label"), "port": p} for r in rows]

    def _blueprint_tag_labels(self, blueprint_id: str) -> set[str]:
        return {t["label"] for t in self.list_tags(blueprint_id)}

    # ── Assignment ───────────────────────────────────────────────────

    def set_node_tags(
        self, blueprint_id: str, targets, add=None, remove=None,
        create_missing: bool = False,
    ) -> dict:
        """Add and/or remove tags (by label) on nodes. Unknown tag labels are
        refused unless `create_missing` (the controller would silently create them)."""
        add, remove = self._as_list(add), self._as_list(remove)
        if not add and not remove:
            raise ValueError("Nothing to do: provide 'add' and/or 'remove'.")
        resolved = [self._resolve_tag_target(blueprint_id, t) for t in self._as_list(targets)]
        if not resolved:
            raise ValueError("No target given.")
        existing = self._blueprint_tag_labels(blueprint_id)
        missing = [t for t in add if t not in existing]
        if missing and not create_missing:
            raise ValueError(
                f"Tags not found in the blueprint: {missing}. Create them first "
                "(tag create) or pass create_missing=true.")
        for label in missing:
            self.create_tag(label, blueprint_id=blueprint_id)
        ids = [r["id"] for r in resolved]
        self._post(f"/blueprints/{blueprint_id}/tagging",
                   {"nodes": ids, "add": add, "remove": remove})

        # Graph updates are asynchronous: poll briefly before judging.
        warnings: list[str] = []
        for attempt in range(6):
            warnings = []
            for r in resolved:
                have = set(self.node_tags(blueprint_id, r["id"]))
                warnings += [f"{r.get('label') or r['id']}: tag '{t}' not applied" for t in add if t not in have]
                warnings += [f"{r.get('label') or r['id']}: tag '{t}' still present" for t in remove if t in have]
            if not warnings:
                break
            time.sleep(0.5)
        out = {"nodes": resolved, "added": add, "removed": remove, "created_tags": missing}
        if warnings:
            out["warnings"] = warnings
        return out

    def node_tags(self, blueprint_id: str, node_id: str) -> list[str]:
        rows = self._qe(
            blueprint_id,
            f"node(id='{self._q(node_id)}').in_('tag').node('tag', name='t')")
        return sorted(r["t"]["label"] for r in rows)

    def get_node_tags(self, blueprint_id: str, node: str | dict) -> dict:
        r = self._resolve_tag_target(blueprint_id, node)
        return {**r, "tags": self.node_tags(blueprint_id, r["id"])}

    # ── Selection by tag ─────────────────────────────────────────────

    def find_tagged(
        self, blueprint_id: str, tags, node_type: str | None = None, match: str = "all",
    ) -> list[dict]:
        """Nodes carrying the given tags (`match`: 'all' = every tag, 'any' = at least one).
        Interfaces are enriched with their `system` and `port`."""
        labels = self._as_list(tags)
        if not labels:
            raise ValueError("At least one tag is required.")
        if match not in ("all", "any"):
            raise ValueError("'match' must be 'all' or 'any'.")
        known = self._blueprint_tag_labels(blueprint_id)
        unknown = [t for t in labels if t not in known]
        if unknown:
            raise ValueError(f"Unknown tag(s) in the blueprint: {unknown}. Existing: {sorted(known)}.")
        per_tag: list[dict[str, dict]] = []
        for label in labels:
            rows = self._qe(
                blueprint_id,
                f"node('tag', label='{self._q(label)}', name='t').out('tag').node(name='x')")
            per_tag.append({r["x"]["id"]: r["x"] for r in rows})
        ids = set.intersection(*(set(d) for d in per_tag)) if match == "all" else set().union(*per_tag)
        nodes = {i: d[i] for d in per_tag for i in d if i in ids}
        out = []
        for n in nodes.values():
            if node_type and n.get("type") != node_type:
                continue
            item = {"id": n["id"], "type": n["type"], "label": n.get("label")}
            if n["type"] == "system":
                item.update(system_type=n.get("system_type"), role=n.get("role"))
            if n["type"] == "interface":
                item.update(self._interface_owner(blueprint_id, n))
            out.append(item)
        return sorted(out, key=lambda x: (x["type"], x.get("system") or "", x.get("label") or "", x.get("port") or ""))

    def _interface_owner(self, blueprint_id: str, iface: dict) -> dict:
        rows = self._qe(
            blueprint_id,
            f"node('interface', id='{iface['id']}').in_('hosted_interfaces').node('system', name='s')")
        return {"system": rows[0]["s"].get("label") if rows else None, "port": iface.get("if_name")}

    def resolve_tagged_systems(self, blueprint_id: str, tags, match: str = "all") -> list[dict]:
        """Leaf/switch systems carrying the tags. Raises if none match."""
        systems = [n for n in self.find_tagged(blueprint_id, tags, "system", match)
                   if n.get("system_type") == "switch"]
        if not systems:
            raise ValueError(f"No switch carries tag(s) {self._as_list(tags)} (match={match}).")
        return systems

    def resolve_tagged_ports(
        self, blueprint_id: str, port_tags=None, system_tags=None, match: str = "all",
    ) -> tuple[list[dict], dict]:
        """Leaf ports facing a generic system, selected by tags:
        - `port_tags`: interfaces carrying the tags;
        - `system_tags`: all ports of the switches carrying the tags;
        both given -> intersection. Returns (port rows of resolve_port_interfaces, info)."""
        if not port_tags and not system_tags:
            raise ValueError("'port_tags' and/or 'system_tags' is required.")
        rows = self.resolve_port_interfaces(blueprint_id)
        info: dict = {}
        if port_tags:
            tagged = self.find_tagged(blueprint_id, port_tags, "interface", match)
            ids = {t["id"] for t in tagged}
            matched = [r for r in rows if r["switch_interface_id"] in ids or r["ct_interface_id"] in ids]
            seen = {r["switch_interface_id"] for r in matched} | {r["ct_interface_id"] for r in matched}
            info["tagged_ports_without_generic_system"] = [
                {"system": t.get("system"), "port": t.get("port")} for t in tagged if t["id"] not in seen]
            rows = matched
        if system_tags:
            sys_ids = {s["id"] for s in self.resolve_tagged_systems(blueprint_id, system_tags, match)}
            rows = [r for r in rows if r["switch_id"] in sys_ids]
        info["matched_ports"] = [{"system": r["switch"], "port": r["port"]} for r in rows]
        return rows, info
