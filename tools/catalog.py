"""Tools: resource pools, design catalog, configlets, property sets, tasks."""

from core import mcp, _client, _require_write


@mcp.tool()
def list_asn_pools() -> list:
    """List ASN pools."""
    return _client().list_asn_pools()

@mcp.tool()
def list_ip_pools() -> list:
    """List IP pools."""
    return _client().list_ip_pools()

@mcp.tool()
def list_vni_pools() -> list:
    """List VNI pools."""
    return _client().list_vni_pools()

@mcp.tool()
def list_logical_devices() -> list:
    """List logical devices."""
    return _client().list_logical_devices()

@mcp.tool()
def list_interface_maps() -> list:
    """List interface maps."""
    return _client().list_interface_maps()

@mcp.tool()
def list_rack_types() -> list:
    """List rack types."""
    return _client().list_rack_types()

@mcp.tool()
def list_templates() -> list:
    """List templates."""
    return _client().list_templates()

@mcp.tool()
def list_configlets() -> list:
    """List configlets."""
    return _client().list_configlets()

@mcp.tool()
def get_configlet(configlet_id: str) -> dict:
    """Configlet detail."""
    return _client().get_configlet(configlet_id)

@mcp.tool()
def list_blueprint_configlets(blueprint_id: str) -> list:
    """Blueprint configlets."""
    return _client().list_blueprint_configlets(blueprint_id)

@mcp.tool()
def get_blueprint_configlet(blueprint_id: str, configlet_id: str) -> dict:
    """Blueprint configlet detail."""
    return _client().get_blueprint_configlet(blueprint_id, configlet_id)

@mcp.tool()
def list_property_sets() -> list:
    """List property sets."""
    return _client().list_property_sets()

@mcp.tool()
def get_property_set(property_set_id: str) -> dict:
    """Property set detail."""
    return _client().get_property_set(property_set_id)

@mcp.tool()
def list_blueprint_property_sets(blueprint_id: str) -> list:
    """Blueprint property sets."""
    return _client().list_blueprint_property_sets(blueprint_id)

@mcp.tool()
def get_blueprint_property_set(blueprint_id: str, property_set_id: str) -> dict:
    """Blueprint property set detail."""
    return _client().get_blueprint_property_set(blueprint_id, property_set_id)

@mcp.tool()
def list_tasks(blueprint_id: str = None) -> list:
    """List tasks."""
    return _client().list_tasks(blueprint_id=blueprint_id)

@mcp.tool()
def get_task(task_id: str, blueprint_id: str = None) -> dict:
    """Task detail."""
    return _client().get_task(task_id=task_id, blueprint_id=blueprint_id)
