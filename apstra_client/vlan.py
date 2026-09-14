"""Add a VLAN on a leaf port (UC#1): VN creation, port assignment, CT."""

import ipaddress
import requests
from typing import Any, Optional


class VlanMixin:
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
                except (requests.exceptions.RequestException, ValueError, KeyError, TypeError) as exc:
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

