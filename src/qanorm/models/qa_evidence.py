"""Evidence ORM model for normalized answer support blocks."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import EvidenceSourceKind, FreshnessStatus


class QAEvidence(Base):
    """Unified normative and non-normative evidence block."""

    __tablename__ = "qa_evidence"
    __table_args__ = (
        Index("ix_qa_evidence_query_id", "query_id"),
        Index("ix_qa_evidence_subtask_id", "subtask_id"),
        Index("ix_qa_evidence_source_kind", "source_kind"),
        Index("ix_qa_evidence_chunk_id", "chunk_id"),
        Index("ix_qa_evidence_document_id", "document_id"),
        Index("ix_qa_evidence_document_version_id", "document_version_id"),
        Index("ix_qa_evidence_node_id", "node_id"),
        Index("ix_qa_evidence_start_node_id", "start_node_id"),
        Index("ix_qa_evidence_end_node_id", "end_node_id"),
        Index("ix_qa_evidence_source_domain", "source_domain"),
        Index("ix_qa_evidence_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_queries.id", ondelete="CASCADE"),
        nullable=False,
    )
    subtask_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_subtasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("retrieval_chunks.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_kind: Mapped[EvidenceSourceKind] = mapped_column(
        Enum(
            EvidenceSourceKind,
            name="evidence_source_kind_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    start_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    end_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    edition_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locator: Mapped[str | None] = mapped_column(Text, nullable=True)
    locator_end: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    selection_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    freshness_status: Mapped[FreshnessStatus] = mapped_column(
        Enum(
            FreshnessStatus,
            name="freshness_status_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=FreshnessStatus.UNKNOWN,
        server_default=FreshnessStatus.UNKNOWN.value,
    )
    is_normative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    requires_verification: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    query: Mapped["QAQuery"] = relationship("QAQuery", back_populates="evidence_blocks")
    subtask: Mapped["QASubtask | None"] = relationship("QASubtask", back_populates="evidence_blocks")
    chunk: Mapped["RetrievalChunk | None"] = relationship(
        "RetrievalChunk",
        back_populates="evidence_blocks",
        foreign_keys=[chunk_id],
    )
    document: Mapped["Document | None"] = relationship("Document")
    document_version: Mapped["DocumentVersion | None"] = relationship("DocumentVersion")
    node: Mapped["DocumentNode | None"] = relationship("DocumentNode", foreign_keys=[node_id])
    start_node: Mapped["DocumentNode | None"] = relationship("DocumentNode", foreign_keys=[start_node_id])
    end_node: Mapped["DocumentNode | None"] = relationship("DocumentNode", foreign_keys=[end_node_id])
