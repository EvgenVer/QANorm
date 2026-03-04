from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from qanorm.models import Document, DocumentVersion, UpdateEvent
from qanorm.services.versioning import (
    activate_processed_version,
    compare_candidate_version_to_active,
    compute_version_content_hash,
    find_existing_document_by_normalized_code,
    skip_duplicate_version,
)


def _mock_session() -> MagicMock:
    return MagicMock()


def test_find_existing_document_by_normalized_code_uses_repository_lookup() -> None:
    session = _mock_session()
    expected = Document(normalized_code="SP 1.0", display_code="SP 1.0")
    session.execute.return_value.scalar_one_or_none.return_value = expected

    result = find_existing_document_by_normalized_code(session, normalized_code="SP 1.0")

    assert result is expected
    session.execute.assert_called_once()


def test_compare_candidate_version_to_active_detects_duplicate_hash() -> None:
    version_id = uuid4()
    document_id = uuid4()
    duplicate_hash = compute_version_content_hash("Same text")
    version = DocumentVersion(id=version_id, document_id=document_id)
    document = Document(id=document_id, normalized_code="SP 1.0", display_code="SP 1.0")
    active_version = DocumentVersion(id=uuid4(), document_id=document_id, content_hash=duplicate_hash, is_active=True)

    session = _mock_session()
    session.get.side_effect = [version, document]
    session.execute.return_value.scalar_one_or_none.return_value = active_version

    result = compare_candidate_version_to_active(
        session,
        document_version_id=version_id,
        content_text="Same text",
    )

    assert result.content_hash == duplicate_hash
    assert result.is_duplicate is True
    assert result.active_version_id == active_version.id


def test_skip_duplicate_version_marks_candidate_outdated_and_logs_event() -> None:
    version_id = uuid4()
    document_id = uuid4()
    active_version_id = uuid4()
    content_hash = compute_version_content_hash("Same text")
    version = DocumentVersion(id=version_id, document_id=document_id, is_active=False, is_outdated=False)
    document = Document(id=document_id, normalized_code="SP 1.0", display_code="SP 1.0")
    active_version = DocumentVersion(id=active_version_id, document_id=document_id, is_active=True, is_outdated=False)

    session = _mock_session()
    session.get.side_effect = [version, document]
    session.execute.return_value.scalar_one_or_none.return_value = active_version

    result = skip_duplicate_version(
        session,
        document_version_id=version_id,
        content_hash=content_hash,
        duplicate_of_version_id=active_version_id,
    )

    assert result.status == "skipped_duplicate"
    assert version.content_hash == content_hash
    assert version.is_active is False
    assert version.is_outdated is True
    assert document.current_version_id == active_version_id
    session.add.assert_called_once()
    update_event = session.add.call_args.args[0]
    assert isinstance(update_event, UpdateEvent)
    assert update_event.status == "skipped_duplicate"
    assert update_event.old_version_id == active_version_id
    assert update_event.new_version_id == version_id


def test_activate_processed_version_retires_old_active_and_updates_current_version() -> None:
    version_id = uuid4()
    document_id = uuid4()
    old_version_id = uuid4()
    content_hash = compute_version_content_hash("New text")
    version = DocumentVersion(id=version_id, document_id=document_id, is_active=False, is_outdated=False)
    document = Document(id=document_id, normalized_code="SP 1.0", display_code="SP 1.0")
    old_active = DocumentVersion(id=old_version_id, document_id=document_id, is_active=True, is_outdated=False)

    session = _mock_session()
    session.get.side_effect = [version, document]
    session.execute.return_value.scalar_one_or_none.return_value = old_active

    result = activate_processed_version(
        session,
        document_version_id=version_id,
        content_hash=content_hash,
    )

    assert result.status == "activated"
    assert result.old_version_id == old_version_id
    assert old_active.is_active is False
    assert old_active.is_outdated is True
    assert version.content_hash == content_hash
    assert version.is_active is True
    assert version.is_outdated is False
    assert document.current_version_id == version_id
    session.add.assert_called_once()
    update_event = session.add.call_args.args[0]
    assert isinstance(update_event, UpdateEvent)
    assert update_event.status == "activated"
    assert update_event.old_version_id == old_version_id
    assert update_event.new_version_id == version_id
