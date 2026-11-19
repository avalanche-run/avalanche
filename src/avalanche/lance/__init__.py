"""Lance backend for Avalanche."""

from .._optional import import_optional

try:
    from .namespace import (
        LanceNamespace,
        LanceNamespaceConfig,
        LanceNs,
        LanceNsConfig,
        LanceTableGroup,
    )
    from .schema import dataframely_to_lance_schema, normalize_schema
    from .table import LanceScan, LanceTable
except ModuleNotFoundError as exc:
    if exc.name in {"lance", "filelock", "pyarrow", "polars", "dataframely"}:
        import_optional(exc.name, extra="lance", feature="Lance")
    raise

__all__ = [
    "LanceNamespace",
    "LanceNamespaceConfig",
    "LanceNs",
    "LanceNsConfig",
    "LanceTable",
    "LanceTableGroup",
    "LanceScan",
    "dataframely_to_lance_schema",
    "normalize_schema",
]
