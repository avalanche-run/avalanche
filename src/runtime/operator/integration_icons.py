"""The image file an integration names as its icon, read for the operator UI.

``icon`` is a path inside the folder the operator scans for the workflow that
declares the integration. The file is returned as bytes with an image media
type; a client shows it as an image and never as markup, so an SVG file cannot
run script in the page.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

MAX_ICON_BYTES = 256 * 1024

# The image types a browser draws from an ``<img>`` element.
ICON_MEDIA_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


class IntegrationIconError(LookupError):
    """The integration has no icon file the operator can serve."""


class IntegrationIcon(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    media_type: str
    content: bytes


def read_integration_icon(root: Path, icon: str) -> IntegrationIcon:
    """Read ``icon`` from inside ``root``.

    Refused: an absolute path, a path that leaves ``root`` (directly or through
    a link), a file that is not one of the image types, and a file over
    ``MAX_ICON_BYTES``.
    """
    relative = PurePosixPath(icon)
    media_type = ICON_MEDIA_TYPES.get(relative.suffix.lower())
    if media_type is None:
        known = ", ".join(sorted(ICON_MEDIA_TYPES))
        raise IntegrationIconError(f"Icon {icon!r} is not an image file ({known}).")
    if relative.is_absolute():
        raise IntegrationIconError(f"Icon {icon!r} must be a path inside the project.")
    project = root.resolve()
    path = (project / relative).resolve()
    if not path.is_relative_to(project):
        raise IntegrationIconError(f"Icon {icon!r} must be a path inside the project.")
    if not path.is_file():
        raise IntegrationIconError(f"Icon {icon!r} was not found in {project}.")
    with path.open("rb") as file:
        content = file.read(MAX_ICON_BYTES + 1)
    if len(content) > MAX_ICON_BYTES:
        raise IntegrationIconError(
            f"Icon {icon!r} is larger than {MAX_ICON_BYTES // 1024} KiB."
        )
    return IntegrationIcon(media_type=media_type, content=content)
