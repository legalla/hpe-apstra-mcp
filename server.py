"""Apstra MCP server entry point.

The FastMCP instance, security/auth setup and the Apstra client accessor live
in core.py. Each domain's tools live in their own module under tools/, and
registration happens as an import side effect (the @mcp.tool() decorator).
"""

from core import mcp, run  # noqa: F401  (mcp re-exported for convenience)

from tools import (  # noqa: F401
    version_systems,
    blueprints,
    networks,
    systems,
    topology,
    endpoints,
    cabling,
    locate,
    telemetry,
    revisions,
    vlan,
    ports,
    catalog,
)
import prompts  # noqa: F401

from dispatch.flat_tools import install_flat_toolset

# No-op unless APSTRA_FLAT_TOOLSET=true (see dispatch/flat_tools.py).
install_flat_toolset(mcp)

if __name__ == "__main__":
    run()
