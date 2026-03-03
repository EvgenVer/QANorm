"""Storage path helpers."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

from qanorm.settings import get_settings
from qanorm.utils.text import normalize_whitespace


_INVALID_PATH_CHARS_RE = re.compile(r'[<>:"/\\|?*]+')


def get_raw_storage_root() -> Path:
    """Return the configured root path for raw storage."""

    return Path(get_settings().env.raw_storage_path).expanduser()


def sanitize_path_component(value: str) -> str:
    """Normalize a string so it can safely be used inside a path."""

    cleaned = normalize_whitespace(value)
    cleaned = _INVALID_PATH_CHARS_RE.sub("_", cleaned)
    cleaned = cleaned.replace(" ", "_")
    cleaned = cleaned.strip("._")
    return cleaned or "unnamed"


def build_artifact_filename(
    *,
    document_code: str,
    version_id: UUID | str,
    artifact_type: str,
    extension: str,
) -> str:
    """Build a deterministic artifact filename."""

    ext = extension if extension.startswith(".") else f".{extension}"
    safe_code = sanitize_path_component(document_code)
    safe_type = sanitize_path_component(artifact_type)
    safe_version = sanitize_path_component(str(version_id))
    return f"{safe_code}__{safe_version}__{safe_type}{ext.lower()}"


def build_artifact_relative_path(
    *,
    document_code: str,
    version_id: UUID | str,
    artifact_type: str,
    extension: str,
) -> Path:
    """Build the logical relative path inside raw storage."""

    safe_code = sanitize_path_component(document_code)
    filename = build_artifact_filename(
        document_code=document_code,
        version_id=version_id,
        artifact_type=artifact_type,
        extension=extension,
    )
    return Path(safe_code) / sanitize_path_component(str(version_id)) / filename


def resolve_storage_path(relative_path: str | Path, base_path: Path | None = None) -> Path:
    """Resolve a relative raw-storage path against the configured root."""

    root = (base_path or get_raw_storage_root()).expanduser().resolve(strict=False)
    requested = Path(relative_path)
    if requested.is_absolute():
        raise ValueError("Raw storage paths must be relative to the storage root")

    resolved = (root / requested).resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("Resolved raw storage path escapes the storage root")

    return resolved


def ensure_parent_directory(file_path: Path) -> Path:
    """Create parent directories for the provided file path."""

    file_path.parent.mkdir(parents=True, exist_ok=True)
    return file_path
