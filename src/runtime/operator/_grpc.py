"""Shared private gRPC transport configuration."""

from __future__ import annotations

MAX_GRPC_MESSAGE_BYTES = 48 * 1024 * 1024
# A refused rerun lists every problem in its trailing metadata.
_MAX_GRPC_METADATA_BYTES = 1024 * 1024

_BOUNDED_MESSAGE_OPTIONS = (
    ("grpc.max_send_message_length", MAX_GRPC_MESSAGE_BYTES),
    ("grpc.max_receive_message_length", MAX_GRPC_MESSAGE_BYTES),
    ("grpc.max_metadata_size", _MAX_GRPC_METADATA_BYTES),
    ("grpc.absolute_max_metadata_size", _MAX_GRPC_METADATA_BYTES),
)
