"""Raw storage helpers."""

from __future__ import annotations

from pathlib import Path

from qanorm.storage.paths import ensure_parent_directory, get_raw_storage_root, resolve_storage_path


class RawFileStore:
    """Filesystem-backed raw artifact storage."""

    def __init__(self, base_path: Path | None = None) -> None:
        self.base_path = (base_path or get_raw_storage_root()).expanduser()

    def save_bytes(self, relative_path: str | Path, payload: bytes, *, overwrite: bool = False) -> Path:
        """Save bytes to raw storage and return the absolute file path."""

        target_path = resolve_storage_path(relative_path, base_path=self.base_path)
        if target_path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing file: {target_path}")

        ensure_parent_directory(target_path)
        target_path.write_bytes(payload)
        return target_path

    def save_text(
        self,
        relative_path: str | Path,
        payload: str,
        *,
        encoding: str = "utf-8",
        overwrite: bool = False,
    ) -> Path:
        """Save text to raw storage and return the absolute file path."""

        target_path = resolve_storage_path(relative_path, base_path=self.base_path)
        if target_path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing file: {target_path}")

        ensure_parent_directory(target_path)
        target_path.write_text(payload, encoding=encoding)
        return target_path

    def read_bytes(self, relative_path: str | Path) -> bytes:
        """Read bytes from raw storage."""

        return resolve_storage_path(relative_path, base_path=self.base_path).read_bytes()

    def read_text(self, relative_path: str | Path, *, encoding: str = "utf-8") -> str:
        """Read text from raw storage."""

        return resolve_storage_path(relative_path, base_path=self.base_path).read_text(encoding=encoding)

    def exists(self, relative_path: str | Path) -> bool:
        """Check whether a raw artifact exists."""

        return resolve_storage_path(relative_path, base_path=self.base_path).exists()

    def cleanup_temp_files(self, temp_root: Path, *, pattern: str = "*") -> int:
        """Remove files from a temp directory and return the number of deleted files."""

        temp_root = temp_root.expanduser()
        if not temp_root.exists():
            return 0

        removed = 0
        for candidate in temp_root.rglob(pattern):
            if candidate.is_file():
                candidate.unlink()
                removed += 1
        return removed
