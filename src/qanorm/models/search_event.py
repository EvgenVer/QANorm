"""Search event ORM model for trusted and open web lookups."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from qanorm.db.base import Base
from qanorm.db.types import SearchScope, SearchStatus


class SearchEvent(Base):
    """Audited external search provider invocation."""

    __tablename__ = "search_events"
    __table_args__ = (
        Index("ix_search_events_query_id", "query_id"),
        Index("ix_search_events_subtask_id", "subtask_id"),
        Index("ix_search_events_provider_name", "provider_name"),
        Index("ix_search_events_search_scope", "search_scope"),
        Index("ix_search_events_status", "status"),
        Index("ix_search_events_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_queries.id", ondelete="SET NULL"),
        nullable=True,
    )
    subtask_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qa_subtasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    search_scope: Mapped[SearchScope] = mapped_column(
        Enum(
            SearchScope,
            name="search_scope_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_domains: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[SearchStatus] = mapped_column(
        Enum(
            SearchStatus,
            name="search_status_enum",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=SearchStatus.COMPLETED,
        server_default=SearchStatus.COMPLETED.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    query: Mapped["QAQuery | None"] = relationship("QAQuery", back_populates="search_events")
    subtask: Mapped["QASubtask | None"] = relationship("QASubtask", back_populates="search_events")
