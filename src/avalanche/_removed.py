"""The one error raised by the removed tables and streams API."""

REMOVED_MESSAGE = (
    "avalanche.iceberg, avalanche.lance and ava.Stream (with its cursors and "
    "AppendResult) were removed. Tables and streams are being rebuilt in the "
    "Linear project 'Tables and streams in Avalanche and Delta'."
)


class TablesAndStreamsRemovedError(ImportError):
    """Raised on any use of the removed tables and streams API."""

    def __init__(self) -> None:
        super().__init__(REMOVED_MESSAGE)
