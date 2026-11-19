"""
Iceberg backend for Avalanche.

Provides IcebergNamespace and IcebergTable for managing Apache Iceberg tables.
Supports DataFramely schemas with automatic conversion to PyIceberg format.
"""

from importlib import import_module

try:
    import_module("pyiceberg")
    import_module("pyarrow")
    import_module("polars")
    import_module("dataframely")
except ImportError as exc:
    raise ImportError(
        "Iceberg features require the optional Iceberg dependency. "
        "Install it with `uv add 'avalanche-ai[iceberg]'` or `uv sync --extra iceberg`."
    ) from exc

from .namespace import (
    IcebergAppendScan,
    IcebergNamespace,
    IcebergNs,
    IcebergNsConfig,
    IcebergTableGroup,
)
from .schema import dataframely_to_iceberg_schema, normalize_schema
from .table import IcebergTable

__all__ = [
    "IcebergNamespace",
    "IcebergNs",
    "IcebergNsConfig",
    "IcebergTable",
    "IcebergTableGroup",
    "IcebergAppendScan",
    # Schema utilities
    "dataframely_to_iceberg_schema",
    "normalize_schema",
]
