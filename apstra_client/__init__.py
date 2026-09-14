"""Client for the Juniper Apstra REST API.

The client is split into per-domain mixins (one module each) that are combined
here into a single ``ApstraClient`` class. This keeps each file focused on one
area of the Apstra API while preserving a single, unchanged public interface.
"""

from .base import BaseMixin
from .blueprints import BlueprintsMixin
from .networks import NetworksMixin
from .systems import SystemsMixin
from .topology import TopologyMixin
from .endpoints import EndpointsMixin
from .cabling import CablingMixin
from .locate import LocateMixin
from .revisions import RevisionsMixin
from .telemetry import TelemetryMixin
from .vlan import VlanMixin
from .ports import PortsMixin
from .catalog import CatalogMixin


class ApstraClient(
    BlueprintsMixin,
    NetworksMixin,
    SystemsMixin,
    TopologyMixin,
    EndpointsMixin,
    CablingMixin,
    LocateMixin,
    RevisionsMixin,
    TelemetryMixin,
    VlanMixin,
    PortsMixin,
    CatalogMixin,
    BaseMixin,
):
    """HTTP client for the Apstra API (see the mixins above for each domain)."""


__all__ = ["ApstraClient"]
