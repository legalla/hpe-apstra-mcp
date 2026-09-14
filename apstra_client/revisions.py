"""Blueprint revisions, rollback and staging revert."""

import ipaddress
import requests
from typing import Any, Optional


class RevisionsMixin:
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

