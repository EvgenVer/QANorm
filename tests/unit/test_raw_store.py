from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from qanorm.settings import get_settings
from qanorm.storage.checksums import sha256_bytes, sha256_file
from qanorm.storage.paths import (
    build_artifact_filename,
    build_artifact_relative_path,
    ensure_parent_directory,
    get_raw_storage_root,
    resolve_storage_path,
    sanitize_path_component,
)
from qanorm.storage.raw_store import RawFileStore


def test_get_raw_storage_root_uses_configuration(monkeypatch, tmp_path: Path) -> None:
    storage_root = tmp_path / "configured-raw"
    monkeypatch.setenv("QANORM_RAW_STORAGE_PATH", str(storage_root))
    get_settings.cache_clear()
    try:
        assert get_raw_storage_root() == storage_root
    finally:
        get_settings.cache_clear()


def test_sanitize_path_component_normalizes_unsafe_characters() -> None:
    assert sanitize_path_component("  SP 20.13330/2016:*  ") == "SP_20.13330_2016"


def test_build_artifact_filename_uses_code_version_and_type() -> None:
    filename = build_artifact_filename(
        document_code="SP 20.13330/2016",
        version_id=UUID("12345678-1234-5678-1234-567812345678"),
        artifact_type="pdf",
        extension="PDF",
    )

    assert filename == (
        "SP_20.13330_2016__12345678-1234-5678-1234-567812345678__pdf.pdf"
    )


def test_build_artifact_relative_path_uses_nested_logical_structure() -> None:
    relative_path = build_artifact_relative_path(
        document_code="SP 20.13330/2016",
        version_id="ver-001",
        artifact_type="html",
        extension=".html",
    )

    assert relative_path == Path(
        "SP_20.13330_2016/ver-001/SP_20.13330_2016__ver-001__html.html"
    )


def test_resolve_storage_path_rejects_escape_attempts(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_storage_path("../outside.txt", base_path=tmp_path)


def test_ensure_parent_directory_creates_missing_directories(tmp_path: Path) -> None:
    file_path = tmp_path / "a" / "b" / "artifact.bin"

    ensure_parent_directory(file_path)

    assert file_path.parent.is_dir()


def test_sha256_helpers_match_for_same_payload(tmp_path: Path) -> None:
    payload = b"example payload"
    file_path = tmp_path / "payload.bin"
    file_path.write_bytes(payload)

    assert sha256_bytes(payload) == sha256_file(file_path)


def test_raw_file_store_saves_and_reads_bytes(tmp_path: Path) -> None:
    store = RawFileStore(base_path=tmp_path)
    relative_path = Path("doc/ver/artifact.pdf")

    saved_path = store.save_bytes(relative_path, b"pdf-data")

    assert saved_path == tmp_path / relative_path
    assert store.exists(relative_path) is True
    assert store.read_bytes(relative_path) == b"pdf-data"


def test_raw_file_store_saves_and_reads_text(tmp_path: Path) -> None:
    store = RawFileStore(base_path=tmp_path)
    relative_path = Path("doc/ver/artifact.html")

    saved_path = store.save_text(relative_path, "hello", encoding="utf-8")

    assert saved_path == tmp_path / relative_path
    assert store.read_text(relative_path) == "hello"


def test_raw_file_store_refuses_overwrite_by_default(tmp_path: Path) -> None:
    store = RawFileStore(base_path=tmp_path)
    relative_path = Path("doc/ver/artifact.txt")
    store.save_text(relative_path, "first")

    with pytest.raises(FileExistsError):
        store.save_text(relative_path, "second")


def test_raw_file_store_allows_explicit_overwrite(tmp_path: Path) -> None:
    store = RawFileStore(base_path=tmp_path)
    relative_path = Path("doc/ver/artifact.txt")
    store.save_text(relative_path, "first")

    store.save_text(relative_path, "second", overwrite=True)

    assert store.read_text(relative_path) == "second"


def test_raw_file_store_cleans_up_temp_files(tmp_path: Path) -> None:
    store = RawFileStore(base_path=tmp_path)
    temp_root = tmp_path / "temp"
    (temp_root / "a").mkdir(parents=True)
    (temp_root / "a" / "one.tmp").write_text("1", encoding="utf-8")
    (temp_root / "a" / "two.tmp").write_text("2", encoding="utf-8")
    (temp_root / "a" / "keep.txt").write_text("3", encoding="utf-8")

    removed = store.cleanup_temp_files(temp_root, pattern="*.tmp")

    assert removed == 2
    assert (temp_root / "a" / "keep.txt").exists() is True
