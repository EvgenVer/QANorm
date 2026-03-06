"""Query creation services for Stage 2 orchestration runs."""

from __future__ import annotations

from uuid import UUID
from uuid import uuid4

from sqlalchemy.orm import Session

from qanorm.db.types import MessageRole, QueryStatus
from qanorm.models import QAMessage, QAQuery
from qanorm.repositories.qa_messages import QAMessageRepository
from qanorm.repositories.qa_queries import QAQueryRepository


class QueryService:
    """Create user messages and query-run rows in one place."""

    def __init__(
        self,
        session: Session,
        *,
        message_repository: QAMessageRepository | None = None,
        query_repository: QAQueryRepository | None = None,
    ) -> None:
        self.session = session
        self.message_repository = message_repository or QAMessageRepository(session)
        self.query_repository = query_repository or QAQueryRepository(session)

    def create_query_from_message(
        self,
        *,
        session_id: UUID,
        content: str,
        metadata_json: dict | None = None,
        query_type: str | None = None,
    ) -> tuple[QAMessage, QAQuery]:
        """Persist the user message and bind a new query run to it."""

        message = self.message_repository.add(
            QAMessage(
                id=uuid4(),
                session_id=session_id,
                role=MessageRole.USER,
                content=content,
                metadata_json=metadata_json,
            )
        )
        query = self.query_repository.add(
            QAQuery(
                id=uuid4(),
                session_id=session_id,
                message_id=message.id,
                query_text=content,
                query_type=query_type,
                status=QueryStatus.PENDING,
            )
        )
        return message, query
