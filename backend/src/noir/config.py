"""NOIR configuration facade.

Re-exports configuration classes and singletons from noir.domain.config.
"""

from __future__ import annotations

from noir.domain.config import (
    NoirConfig,
    get_config,
    reset_config,
)

__all__ = ["NoirConfig", "get_config", "reset_config"]
