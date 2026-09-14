"""Tools: blueprint revisions, rollback and staging revert."""

from core import mcp, _client, _require_write


@mcp.tool()
def list_blueprint_revisions(blueprint_id: str, limit: int = 20) -> dict:
    """List the revisions (restore points) of a blueprint.

    Each revision is a committed config version to which you can roll back
    without CLI (see rollback_blueprint). Sorted from the most recent to the
    oldest; 'limit' bounds the result (0 = all).
    """
    return _client().list_blueprint_revisions(
        blueprint_id=blueprint_id, limit=limit)

@mcp.tool()
@_require_write
def rollback_blueprint(blueprint_id: str, revision_id: str) -> dict:
    """Restore the blueprint to a previous revision (without CLI).

    Triggers a configuration rollback via the Apstra API to the given
    'revision_id' (must be eligible: see list_blueprint_revisions).
    Convergence toward the devices is handled by the Apstra deployer
    (minimum duration set by the vendor). Impactful operation: use only
    after validation.
    """
    return _client().rollback_blueprint(
        blueprint_id=blueprint_id, revision_id=revision_id)

@mcp.tool()
@_require_write
def revert_staging(blueprint_id: str, confirmed: bool = False) -> dict:
    """Revert the UNCOMMITTED staging changes of a blueprint.

    Restores staging to the last committed/deployed version: all uncommitted
    changes are discarded (equivalent to the 'Revert' button of the Apstra UI).
    Can be triggered on simple user request.

    CONFIRMATION LOCK (DESTRUCTIVE operation): if confirmed=False, the tool
    does NOTHING and returns status 'confirmation_required' with the question
    "Do you want to discard the change and trigger a revert?". Ask the
    question, then:
      - if YES: call again with confirmed=True (the revert is executed);
      - if NO: do nothing; the changes remain in staging.
    If there are no staging changes, returns 'nothing_to_revert'.
    """
    return _client().revert_staging(
        blueprint_id=blueprint_id, confirmed=confirmed)
