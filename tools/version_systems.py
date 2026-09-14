"""Tools: Apstra version and physical systems/agents inventory."""

from core import mcp, _client, _require_write


@mcp.tool()
def get_version() -> dict:
    """Apstra version."""
    return _client().get_version()

@mcp.tool()
def list_systems() -> list:
    """List devices."""
    return _client().list_systems()

@mcp.tool()
def get_system(system_id: str) -> dict:
    """Device detail."""
    return _client().get_system(system_id)

@mcp.tool()
def list_agents() -> list:
    """List agents."""
    return _client().list_agents()
