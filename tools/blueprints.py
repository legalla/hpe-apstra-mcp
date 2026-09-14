"""Tools: blueprint lifecycle (create, inspect, diff, commit)."""

from core import mcp, _client, _require_write


@mcp.tool()
def list_blueprints() -> list:
    """List blueprints."""
    return _client().list_blueprints()

@mcp.tool()
@_require_write
def create_blueprint(label: str, template_id: str, init_type: str = "template_reference") -> dict:
    """Create blueprint."""
    return _client().create_blueprint(label, template_id, init_type)

@mcp.tool()
def get_blueprint_anomalies(blueprint_id: str) -> dict:
    """Blueprint anomalies."""
    return _client().get_blueprint_anomalies(blueprint_id)

@mcp.tool()
def get_blueprint_build_errors(blueprint_id: str) -> dict:
    """Build (staging) errors of a blueprint = Uncommitted > Build Errors tab.

    DO NOT confuse with get_blueprint_anomalies (runtime telemetry).
    Returns errors_count/warnings_count and the list of errors (message,
    error_type, category, severity, suggested resolutions).
    """
    return _client().get_blueprint_build_errors(blueprint_id)

@mcp.tool()
def get_blueprint_logical_diff(blueprint_id: str) -> dict:
    """Logical diff (staging) of a blueprint = Uncommitted > Logical Diff tab.

    Lists the uncommitted changes (type, action added/removed/changed, name)
    and a digest (number of nodes/relationships added/removed/changed).
    """
    return _client().get_blueprint_logical_diff(blueprint_id)

@mcp.tool()
def get_blueprint_nodes(blueprint_id: str, node_type: str = None) -> dict:
    """Blueprint nodes."""
    return _client().get_blueprint_nodes(blueprint_id, node_type)

@mcp.tool()
def check_blueprint_commit(blueprint_id: str) -> dict:
    """Check commit."""
    return _client().check_blueprint_commit(blueprint_id)

@mcp.tool()
@_require_write
def commit_blueprint(blueprint_id: str, description: str = "") -> dict:
    """Commit (deploy) the staging changes of a blueprint.

    Automatically reads the current staging version and triggers the
    deployment (PUT /deploy). Deployment is asynchronous: convergence toward
    the devices is handled by Apstra.

    MANDATORY CONFIRMATION: before calling this tool, ASK the user
    "The change is about to be committed — are you sure?". If NO, do not commit
    and provide a summary of the changes left in staging.
    """
    return _client().commit_blueprint(blueprint_id, description)
