"""Add a VLAN on a leaf port (UC#1): VN creation, port assignment, CT."""

import ipaddress
import requests
import time
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
        vlan_id: int | None = None,
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
        vn_id: str | None = None,
        reuse_existing: bool = False,
    ) -> dict:
        """Create a VLAN (Virtual Network) on a leaf and assign it to a port.

        EXISTING VN: pass 'vn_id' (VN node id, label or VNI) to assign an
        already existing VN to the port without re-creating it; or
        'reuse_existing=True' to reuse the VN matching 'label' / VNI / VLAN on
        this leaf if there is one. The VN is bound to the leaf if needed and
        attached to the port through its Connectivity Template. Without either
        flag, a matching VN raises an explicit error (instead of a controller 422).

        Creates a Virtual Network local to the indicated leaf (no impact on the
        other leafs). If 'port' is provided WITH 'tagging' ('tagged' or
        'untagged'), the VN is attached to the port through its Connectivity
        Template (created with the VN via Apstra's `create_policy_*`, or built
        from Apstra's CT structure if the VN has none) applied to the port.
        The commit (push to the device) only happens if commit=True.

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

        vn_ref = vn_id
        if vlan_id is None and not (vn_ref or (reuse_existing and label)):
            raise ValueError(
                "'vlan_id' is required to create a VN (or pass 'vn_id' to "
                "assign an existing VN).")
        vlan_id = int(vlan_id) if vlan_id is not None else None
        # Options requiring a 'vxlan' VN (L3 or L2VNI) or ESI pair.
        want_l3 = bool(ipv4_subnet) or dhcp_relay
        if l2_vni is not None or want_l3 or (esi_pair and vn_type == "vlan"):
            vn_type = "vxlan"

        # Reuse an existing VN instead of re-creating it (a re-creation fails
        # with VN_NAME_VXLAN_OVERLAPS / VLAN_ID_NOT_UNIQUE_WITHIN_SYSTEM /
        # VNI_ALREADY_USED_IN_VXLAN).
        cand_vni = l2_vni if l2_vni is not None else (
            vni if vni is not None else (
                10000 + vlan_id if vlan_id is not None else None))
        existing = self._find_existing_vn(
            blueprint_id, bound_id, vn_ref, reuse_existing, label,
            vlan_id, vn_type, cand_vni)
        if existing is not None:
            return self._reuse_vn_on_port(
                blueprint_id, existing, leaf_id, leaf_label, bound_id,
                vlan_id, port, tagging, instantiate_port, gs_label,
                commit, commit_confirmed)

        if vlan_id is None:
            raise ValueError("'vlan_id' is required: no existing VN matches to reuse.")
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
        # A CT is only created by Apstra when asked at VN creation; endpoints
        # alone attach nothing. Ask for it when a port + tagging are requested.
        if port and tagging:
            payload["create_policy_tagged"] = tagging == "tagged"
            payload["create_policy_untagged"] = tagging == "untagged"

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

        port_assignment = self._assign_vn_to_port(
            blueprint_id, vn_id, vn_label, vn_type, leaf_id, leaf_label, port,
            tagging, instantiate_port, gs_label, steps, just_created=True)

        commit_result, commit_done = self._commit_step(
            blueprint_id, commit, commit_confirmed,
            f"Add {vn_label} on {leaf_label}", steps)

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


    # ── Helpers shared by add_vlan_to_port (create / reuse paths) ────

    def _prepare_port_for_vlan(
        self, blueprint_id, leaf_id, leaf_label, port, instantiate_port,
        gs_label, steps,
    ):
        """Return (switch_iface_id, instantiation) for a port, instantiating an
        unused port if allowed."""
        instantiation = None
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
                    blueprint_id, leaf_id, leaf_label, port, gs_label=gs_label)
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
        return switch_iface_id, instantiation

    def _assign_vn_to_port(
        self, blueprint_id, vn_node_id, vn_label, vn_type, leaf_id, leaf_label,
        port, tagging, instantiate_port, gs_label, steps, just_created=False,
    ):
        """Attach a VN to a leaf port through its Connectivity Template (the
        CT is created if the VN has none for this tagging). Appends an
        `assign_port` step; returns the port_assignment dict or None."""
        if not port:
            return None
        switch_iface_id, instantiation = self._prepare_port_for_vlan(
            blueprint_id, leaf_id, leaf_label, port, instantiate_port, gs_label, steps)
        if not switch_iface_id:
            steps.append({
                "step": "assign_port", "status": "skipped", "port": port,
                "reason": (f"Interface '{port}' not found on {leaf_label}"
                           + ("." if instantiate_port else " (instantiate_port=False)."))})
            return None
        if not tagging:
            steps.append({
                "step": "assign_port", "status": "no_ct", "port": port,
                "reason": ("No 'tagged'/'untagged' mode chosen: the port is NOT "
                           "connected to this VN. Retry with tagging='tagged' or "
                           "'untagged' to assign the port.")})
            return {"port": port, "interface_id": switch_iface_id, "tagging": None,
                    "ct_created": False, "instantiation": instantiation}

        tag_type = "vlan_tagged" if tagging == "tagged" else "untagged"
        row = None
        for _ in range(10 if instantiation else 1):
            rows = self.resolve_port_interfaces(blueprint_id, device=leaf_id, port=port)
            if rows:
                row = rows[0]
                break
            time.sleep(0.5)
        if not row:
            steps.append({
                "step": "assign_port", "status": "failed", "port": port,
                "reason": ("The port does not face a generic system: no "
                           "interface to attach the Connectivity Template to.")})
            return None

        cts = []
        for _ in range(6 if just_created else 1):
            cts = self.get_vn_connectivity_templates(blueprint_id, vn_node_id)
            if any(tag_type in c["tagging"] for c in cts):
                break
            time.sleep(0.5)
        ct = next((c for c in cts if tag_type in c["tagging"]), None)
        if ct is None:
            ct = self.create_vn_connectivity_template(
                blueprint_id, vn_node_id, vn_label, vn_type, tag_type)
            steps.append({
                "step": "create_ct", "status": "applied", "ct_id": ct["id"],
                "reason": f"VN had no {tag_type} Connectivity Template."})

        vn = self._get(f"/blueprints/{blueprint_id}/virtual-networks/{vn_node_id}")
        already = any(
            e.get("interface_id") == row["endpoint_interface_id"]
            and e.get("tag_type") == tag_type for e in vn.get("endpoints") or [])
        if not already:
            self.apply_ct_to_interfaces(blueprint_id, ct["id"], [row["ct_interface_id"]])
        steps.append({
            "step": "assign_port", "port": port, "tagging": tagging,
            "status": "already_assigned" if already else "applied",
            "ct_id": ct["id"],
            "reason": f"VN attached to the port as '{tagging}' via CT '{ct.get('label')}'."})
        return {"port": port, "interface_id": switch_iface_id, "tagging": tagging,
                "ct_id": ct["id"], "ct_interface_id": row["ct_interface_id"],
                "ct_created": True, "instantiation": instantiation}

    def _commit_step(self, blueprint_id, commit, commit_confirmed, description, steps):
        """Append the commit step (with the confirmation lock). Returns (result, done)."""
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
            return None, False
        if commit and commit_confirmed:
            result = self.commit_blueprint(blueprint_id, description=description)
            steps.append({"step": "commit", "status": "deployed"})
            return result, True
        steps.append({
            "step": "commit", "status": "staged",
            "reason": "commit=False: changes in staging, not deployed.",
        })
        return None, False

    def _find_existing_vn(
        self, blueprint_id, bound_id, vn_ref, reuse_existing, label,
        vlan_id, vn_type, cand_vni,
    ):
        """Return the existing VN to reuse, or None to create a new one.

        - `vn_ref` (node id, label or VNI) given: that VN must exist.
        - otherwise a VN colliding on label / VNI / VLAN-on-this-system is
          reused if `reuse_existing`, else an explicit error is raised (the
          controller would answer 422).
        """
        raw = self._get(f"/blueprints/{blueprint_id}/virtual-networks")
        vns = raw.get("virtual_networks", []) if isinstance(raw, dict) else raw
        vns = [v for v in (vns.values() if isinstance(vns, dict) else vns)
               if isinstance(v, dict)]

        def describe(v, reasons=()):
            return {"id": v.get("id"), "label": v.get("label"),
                    "vn_id": v.get("vn_id"), "matched_on": list(reasons)}

        if vn_ref:
            ref = str(vn_ref)
            found = ([v for v in vns if v.get("id") == ref]
                     or [v for v in vns if v.get("label") == ref]
                     or [v for v in vns if str(v.get("vn_id")) == ref])
            if not found:
                raise ValueError(
                    f"No VN matches vn_id='{ref}' (node id, label or VNI).")
            if len(found) > 1:
                raise ValueError(
                    f"vn_id='{ref}' is ambiguous: {[describe(v) for v in found]}. "
                    "Use the VN node id.")
            return found[0]

        conflicts = []
        for v in vns:
            reasons = []
            if label and v.get("label") == label:
                reasons.append("label")
            if (vn_type == "vxlan" and cand_vni is not None
                    and v.get("vn_type") == "vxlan"
                    and str(v.get("vn_id")) == str(cand_vni)):
                reasons.append("vni")
            if vlan_id is not None and any(
                    b.get("system_id") == bound_id and b.get("vlan_id") == vlan_id
                    for b in v.get("bound_to") or []):
                reasons.append("vlan_on_this_leaf")
            if reasons:
                conflicts.append((v, reasons))
        if not conflicts:
            return None
        if reuse_existing and len(conflicts) == 1:
            return conflicts[0][0]
        listing = [describe(v, r) for v, r in conflicts]
        if reuse_existing:
            raise ValueError(
                f"Several VNs match: {listing}. Pass 'vn_id' (node id) to choose.")
        raise ValueError(
            f"A VN already exists with the same label/VNI/VLAN: {listing}. "
            "Creating it again would fail (VN_NAME_VXLAN_OVERLAPS / "
            "VLAN_ID_NOT_UNIQUE_WITHIN_SYSTEM / VNI_ALREADY_USED_IN_VXLAN). "
            "Pass 'vn_id' (node id, label or VNI) or reuse_existing=True to "
            "assign the existing VN to the port.")

    def _reuse_vn_on_port(
        self, blueprint_id, existing, leaf_id, leaf_label, bound_id, vlan_id,
        port, tagging, instantiate_port, gs_label, commit, commit_confirmed,
    ):
        """Assign an EXISTING VN to a leaf port: bind the VN to the leaf if
        needed, then attach it to the port through its Connectivity Template."""
        path = f"/blueprints/{blueprint_id}/virtual-networks/{existing['id']}"
        vn = self._get(path)
        vn_node_id, vn_label = vn["id"], vn.get("label")
        steps = [{
            "step": "reuse_vn", "vn_id": vn_node_id, "vn_label": vn_label,
            "vn_type": vn.get("vn_type"), "vni": vn.get("vn_id"), "leaf": leaf_label,
        }]

        bound = [dict(b) for b in vn.get("bound_to") or []]
        mine = next((b for b in bound if b.get("system_id") == bound_id), None)
        if mine is None:
            vl = vlan_id if vlan_id is not None else next(
                (b["vlan_id"] for b in bound if b.get("vlan_id")), None)
            if vl is None:
                raise ValueError(
                    f"VN '{vn_label}' is not bound to {leaf_label}: 'vlan_id' is required.")
            bound.append({"system_id": bound_id, "vlan_id": vl, "access_switch_node_ids": []})
            self._patch(path, {"bound_to": bound})
            steps.append({
                "step": "bind_vn", "status": "applied", "system_id": bound_id,
                "vlan_id": vl,
                "reason": f"VN was not yet present on {leaf_label}."})
        elif vlan_id is not None and mine.get("vlan_id") not in (None, vlan_id):
            steps.append({
                "step": "bind_vn", "status": "warning",
                "reason": (f"VN already uses VLAN {mine.get('vlan_id')} on {leaf_label}; "
                           f"requested vlan_id={vlan_id} ignored.")})

        port_assignment = self._assign_vn_to_port(
            blueprint_id, vn_node_id, vn_label, vn.get("vn_type"), leaf_id,
            leaf_label, port, tagging, instantiate_port, gs_label, steps)

        cts = []
        try:
            cts = self.get_vn_connectivity_templates(blueprint_id, vn_node_id)
        except (requests.exceptions.RequestException, ValueError, KeyError, TypeError):
            pass
        commit_result, commit_done = self._commit_step(
            blueprint_id, commit, commit_confirmed,
            f"Assign {vn_label} on {leaf_label}", steps)
        return {
            "blueprint_id": blueprint_id,
            "reused_existing_vn": True,
            "vn_id": vn_node_id,
            "vn_label": vn_label,
            "vlan_id": (mine or bound[-1]).get("vlan_id"),
            "leaf": leaf_label,
            "tagging": tagging,
            "port_assignment": port_assignment,
            "connectivity_templates": cts,
            "ct_id": cts[0]["id"] if len(cts) == 1 else None,
            "commit_requested": bool(commit),
            "commit_confirmed": bool(commit_confirmed),
            "committed": commit_done,
            "commit_result": commit_result,
            "steps": steps,
        }
