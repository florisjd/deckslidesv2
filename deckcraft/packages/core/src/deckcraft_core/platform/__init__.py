"""Platform helpers (Windows EcoQoS opt-out isolated from generic pipeline code)."""
from .windows import unthrottle, unthrottle_tree

__all__ = ["unthrottle", "unthrottle_tree"]
