from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator
from uuid import UUID
from uuid import uuid4

from qanorm.db.types import JobStatus
from qanorm.models import (
    Document,
    DocumentNode,
    DocumentReference,
    DocumentSource,
    DocumentVersion,
    IngestionJob,
    RawArtifact,
    UpdateEvent,
)


@dataclass(slots=True)
class InMemoryStore:
    documents: list[Document] = field(default_factory=list)
    versions: list[DocumentVersion] = field(default_factory=list)
    sources: list[DocumentSource] = field(default_factory=list)
    artifacts: list[RawArtifact] = field(default_factory=list)
    nodes: list[DocumentNode] = field(default_factory=list)
    references: list[DocumentReference] = field(default_factory=list)
    jobs: list[IngestionJob] = field(default_factory=list)
    events: list[UpdateEvent] = field(default_factory=list)


class FakeSession:
    def __init__(self, store: InMemoryStore | None = None) -> None:
        self.store = store or InMemoryStore()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class FakeDocumentRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def add(self, document: Document) -> Document:
        document.id = document.id or uuid4()
        self.session.store.documents.append(document)
        return document

    def get(self, document_id: UUID) -> Document | None:
        return next((item for item in self.session.store.documents if item.id == document_id), None)

    def get_by_normalized_code(self, normalized_code: str) -> Document | None:
        return next((item for item in self.session.store.documents if item.normalized_code == normalized_code), None)

    def list_all(self) -> list[Document]:
        return list(self.session.store.documents)


class FakeDocumentVersionRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def add(self, version: DocumentVersion) -> DocumentVersion:
        version.id = version.id or uuid4()
        self.session.store.versions.append(version)
        return version

    def add_many(self, versions: list[DocumentVersion]) -> list[DocumentVersion]:
        for version in versions:
            version.id = version.id or uuid4()
        self.session.store.versions.extend(versions)
        return list(versions)

    def get(self, version_id: UUID) -> DocumentVersion | None:
        return next((item for item in self.session.store.versions if item.id == version_id), None)

    def get_active_for_document(self, document_id: UUID) -> DocumentVersion | None:
        candidates = [item for item in self.session.store.versions if item.document_id == document_id and item.is_active]
        return candidates[-1] if candidates else None

    def list_for_document(self, document_id: UUID) -> list[DocumentVersion]:
        return [item for item in self.session.store.versions if item.document_id == document_id]


class FakeDocumentSourceRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def add(self, source: DocumentSource) -> DocumentSource:
        source.id = source.id or uuid4()
        self.session.store.sources.append(source)
        return source

    def add_many(self, sources: list[DocumentSource]) -> list[DocumentSource]:
        for source in sources:
            source.id = source.id or uuid4()
        self.session.store.sources.extend(sources)
        return list(sources)

    def list_for_document_version(self, document_version_id: UUID) -> list[DocumentSource]:
        return [item for item in self.session.store.sources if item.document_version_id == document_version_id]


class FakeRawArtifactRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def add(self, artifact: RawArtifact) -> RawArtifact:
        artifact.id = artifact.id or uuid4()
        self.session.store.artifacts.append(artifact)
        return artifact

    def add_many(self, artifacts: list[RawArtifact]) -> list[RawArtifact]:
        for artifact in artifacts:
            artifact.id = artifact.id or uuid4()
        self.session.store.artifacts.extend(artifacts)
        return list(artifacts)

    def get_by_version_and_relative_path(self, document_version_id: UUID, relative_path: str) -> RawArtifact | None:
        return next(
            (
                item
                for item in self.session.store.artifacts
                if item.document_version_id == document_version_id and item.relative_path == relative_path
            ),
            None,
        )

    def list_for_document_version(self, document_version_id: UUID) -> list[RawArtifact]:
        return [item for item in self.session.store.artifacts if item.document_version_id == document_version_id]


class FakeDocumentNodeRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def add(self, node: DocumentNode) -> DocumentNode:
        node.id = node.id or uuid4()
        self.session.store.nodes.append(node)
        return node

    def add_many(self, nodes: list[DocumentNode]) -> list[DocumentNode]:
        for node in nodes:
            node.id = node.id or uuid4()
        self.session.store.nodes.extend(nodes)
        return list(nodes)

    def list_for_document_version(self, document_version_id: UUID) -> list[DocumentNode]:
        nodes = [item for item in self.session.store.nodes if item.document_version_id == document_version_id]
        return sorted(nodes, key=lambda item: item.order_index)


class FakeDocumentReferenceRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def add(self, reference: DocumentReference) -> DocumentReference:
        reference.id = reference.id or uuid4()
        self.session.store.references.append(reference)
        return reference

    def add_many(self, references: list[DocumentReference]) -> list[DocumentReference]:
        for reference in references:
            reference.id = reference.id or uuid4()
        self.session.store.references.extend(references)
        return list(references)

    def list_for_document_version(self, document_version_id: UUID) -> list[DocumentReference]:
        return [item for item in self.session.store.references if item.document_version_id == document_version_id]


class FakeIngestionJobRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def add(self, job: IngestionJob) -> IngestionJob:
        now = datetime.now(timezone.utc)
        job.id = job.id or uuid4()
        job.status = job.status or JobStatus.PENDING
        job.attempt_count = int(job.attempt_count or 0)
        job.max_attempts = int(job.max_attempts or 3)
        job.scheduled_at = job.scheduled_at or now
        self.session.store.jobs.append(job)
        return job

    def get(self, job_id: UUID) -> IngestionJob | None:
        return next((item for item in self.session.store.jobs if item.id == job_id), None)

    def get_duplicate_pending_or_running(self, job_type, dedup_key: str) -> IngestionJob | None:
        for item in self.session.store.jobs:
            if item.job_type != job_type:
                continue
            if item.status not in (JobStatus.PENDING, JobStatus.RUNNING):
                continue
            if item.payload.get("dedup_key") == dedup_key:
                return item
        return None

    def get_next_ready_job(self, now: datetime | None = None) -> IngestionJob | None:
        claimed_at = now or datetime.now(timezone.utc)
        ready = [
            item
            for item in self.session.store.jobs
            if item.status == JobStatus.PENDING and (item.scheduled_at or claimed_at) <= claimed_at
        ]
        if not ready:
            return None
        return sorted(ready, key=lambda item: (item.scheduled_at, item.id))[0]

    def mark_running(self, job: IngestionJob, started_at: datetime | None = None) -> IngestionJob:
        job.status = JobStatus.RUNNING
        job.started_at = started_at or datetime.now(timezone.utc)
        job.finished_at = None
        return job

    def claim_next_ready_job(self, now: datetime | None = None) -> IngestionJob | None:
        job = self.get_next_ready_job(now=now)
        if job is None:
            return None
        return self.mark_running(job, started_at=now or datetime.now(timezone.utc))

    def mark_completed(self, job: IngestionJob, finished_at: datetime | None = None) -> IngestionJob:
        job.status = JobStatus.COMPLETED
        job.last_error = None
        job.finished_at = finished_at or datetime.now(timezone.utc)
        return job

    def mark_failed(self, job: IngestionJob, error_message: str, finished_at: datetime | None = None) -> IngestionJob:
        job.status = JobStatus.FAILED
        job.last_error = error_message
        job.attempt_count += 1
        job.finished_at = finished_at or datetime.now(timezone.utc)
        return job

    def retry_after_temporary_error(
        self,
        job: IngestionJob,
        error_message: str,
        *,
        retry_delay_seconds: int = 60,
        now: datetime | None = None,
    ) -> IngestionJob:
        retried_at = now or datetime.now(timezone.utc)
        job.attempt_count += 1
        job.last_error = error_message
        if job.attempt_count >= job.max_attempts:
            job.status = JobStatus.FAILED
            job.finished_at = retried_at
        else:
            job.status = JobStatus.PENDING
            job.scheduled_at = retried_at.replace(microsecond=0) + timedelta(seconds=retry_delay_seconds)
            job.started_at = None
            job.finished_at = None
        return job


class FakeUpdateEventRepository:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def add(self, event: UpdateEvent) -> UpdateEvent:
        event.id = event.id or uuid4()
        self.session.store.events.append(event)
        return event

    def list_for_document(self, document_id: UUID) -> list[UpdateEvent]:
        return [item for item in self.session.store.events if item.document_id == document_id]


@contextmanager
def patched_in_memory_repositories(session: FakeSession) -> Iterator[FakeSession]:
    from unittest.mock import patch

    replacements = {
        "qanorm.services.document_pipeline.DocumentRepository": FakeDocumentRepository,
        "qanorm.services.document_pipeline.DocumentVersionRepository": FakeDocumentVersionRepository,
        "qanorm.services.document_pipeline.DocumentSourceRepository": FakeDocumentSourceRepository,
        "qanorm.services.document_pipeline.RawArtifactRepository": FakeRawArtifactRepository,
        "qanorm.services.document_pipeline.DocumentNodeRepository": FakeDocumentNodeRepository,
        "qanorm.services.document_pipeline.DocumentReferenceRepository": FakeDocumentReferenceRepository,
        "qanorm.services.document_pipeline.IngestionJobRepository": FakeIngestionJobRepository,
        "qanorm.services.ingestion.IngestionJobRepository": FakeIngestionJobRepository,
        "qanorm.services.refresh_service.DocumentRepository": FakeDocumentRepository,
        "qanorm.services.refresh_service.DocumentVersionRepository": FakeDocumentVersionRepository,
        "qanorm.services.refresh_service.DocumentSourceRepository": FakeDocumentSourceRepository,
        "qanorm.services.refresh_service.IngestionJobRepository": FakeIngestionJobRepository,
        "qanorm.services.refresh_service.UpdateEventRepository": FakeUpdateEventRepository,
        "qanorm.services.versioning.DocumentRepository": FakeDocumentRepository,
        "qanorm.services.versioning.DocumentVersionRepository": FakeDocumentVersionRepository,
        "qanorm.services.versioning.UpdateEventRepository": FakeUpdateEventRepository,
        "qanorm.indexing.indexer.DocumentRepository": FakeDocumentRepository,
        "qanorm.indexing.indexer.DocumentVersionRepository": FakeDocumentVersionRepository,
        "qanorm.indexing.indexer.DocumentNodeRepository": FakeDocumentNodeRepository,
        "qanorm.jobs.worker.IngestionJobRepository": FakeIngestionJobRepository,
    }

    with ExitStack() as stack:
        for target, replacement in replacements.items():
            stack.enter_context(patch(target, replacement))
        yield session
