"""Disposable run directories with an ownership marker, removed exactly and without following links."""

from ._config import (
    DEFAULT_VARIABLE,
    PLATFORMS,
    ConfigError,
    ScratchConfig,
    find_config,
    load_config,
    load_table,
    platform_keys,
)
from ._runs import DEFAULT_MARKER, check_marker, check_root, clean, create, is_link

__all__ = [
    "DEFAULT_MARKER",
    "DEFAULT_VARIABLE",
    "PLATFORMS",
    "ConfigError",
    "ScratchConfig",
    "check_marker",
    "check_root",
    "clean",
    "create",
    "find_config",
    "is_link",
    "load_config",
    "load_table",
    "platform_keys",
]
__version__ = "1.0.0"
