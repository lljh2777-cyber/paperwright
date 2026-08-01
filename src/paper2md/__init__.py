"""Paper2MD public API."""

# Define this before importing the API: its layout writer records the version
# while the package is still initializing.
__version__ = "0.8.0a0"

from .api import ConversionResult, Paper2MD
from .config import Paper2MDConfig
from .models import BBox, Element, Page, PhysicalDocument, Provenance

__all__ = [
    "BBox",
    "Element",
    "Page",
    "Paper2MD",
    "Paper2MDConfig",
    "ConversionResult",
    "PhysicalDocument",
    "Provenance",
]
