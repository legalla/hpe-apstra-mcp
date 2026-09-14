"""Cabling matrices: graph-based and /cabling-map based."""

import ipaddress
import requests
from typing import Any, Optional


class CablingMixin:
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
        except requests.exceptions.RequestException:
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

