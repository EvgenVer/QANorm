"""Storage path helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from uuid import UUID

from qanorm.settings import get_settings
from qanorm.utils.text import normalize_whitespace


_INVALID_PATH_CHARS_RE = re.compile(r'[<>:"/\\|?*]+')
_DOCUMENT_KEY_PREFIX_LENGTH = 24
_DOCUMENT_KEY_HASH_LENGTH = 12
_MAX_ARTIFACT_TYPE_LENGTH = 32


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


def build_document_storage_key(document_code: str) -> str:
    """Build a short deterministic storage key for a document code."""

    safe_code = sanitize_path_component(document_code)
    prefix = safe_code[:_DOCUMENT_KEY_PREFIX_LENGTH].rstrip("._-") or "doc"
    digest = hashlib.sha1(safe_code.encode("utf-8")).hexdigest()[:_DOCUMENT_KEY_HASH_LENGTH]
    return f"{prefix}-{digest}"


def build_artifact_filename(
    *,
    document_code: str,
    version_id: UUID | str,
    artifact_type: str,
    extension: str,
) -> str:
    """Build a deterministic artifact filename."""

    ext = extension if extension.startswith(".") else f".{extension}"
    safe_type = sanitize_path_component(artifact_type)[:_MAX_ARTIFACT_TYPE_LENGTH].rstrip("._-") or "artifact"
    return f"{safe_type}{ext.lower()}"


def build_artifact_relative_path(
    *,
    document_code: str,
    version_id: UUID | str,
    artifact_type: str,
    extension: str,
) -> Path:
    """Build the logical relative path inside raw storage."""

    document_key = build_document_storage_key(document_code)
    safe_version = sanitize_path_component(str(version_id))
    filename = build_artifact_filename(
        document_code=document_code,
        version_id=version_id,
        artifact_type=artifact_type,
        extension=extension,
    )
    return Path(document_key) / safe_version / filename


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
