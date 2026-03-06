"""Context loading and compaction helpers for Stage 2 prompts."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.models.qa_state import PromptRenderContext
from qanorm.repositories.qa_messages import QAMessageRepository
from qanorm.repositories.qa_sessions import QASessionRepository
from qanorm.settings import QAFileConfig, get_qa_config


class ContextService:
    """Load session history and compact it for prompt rendering."""

    def __init__(
        self,
        session: Session,
        *,
        qa_config: QAFileConfig | None = None,
        session_repository: QASessionRepository | None = None,
        message_repository: QAMessageRepository | None = None,
    ) -> None:
        self.session = session
        self.qa_config = qa_config or get_qa_config()
        self.session_repository = session_repository or QASessionRepository(session)
        self.message_repository = message_repository or QAMessageRepository(session)

    def load_prompt_context(
        self,
        *,
        session_id: UUID,
        query_text: str,
        query_id: UUID | None = None,
    ) -> PromptRenderContext | None:
        """Load message history and the current summary into a prompt context."""

        qa_session = self.session_repository.get(session_id)
        if qa_session is None:
            return None

        messages = self.message_repository.list_for_session(session_id)
        _, recent_messages = self.compact_history(messages)
        return PromptRenderContext(
            session_id=session_id,
            query_id=query_id,
            query_text=query_text,
            session_summary=qa_session.session_summary,
            recent_messages=recent_messages,
        )

    def should_compact_history(self, message_count: int) -> bool:
        """Return whether the session crossed the compaction threshold."""

        return message_count >= self.qa_config.session.summary_trigger_messages

    def compact_history(self, messages: list) -> tuple[list, list]:
        """Split history into summary-candidate and prompt-visible recent messages."""

        if not self.should_compact_history(len(messages)):
            return [], list(messages)

        keep_recent = self.qa_config.session.summary_keep_recent_messages
        if keep_recent <= 0:
            return list(messages), []
        return list(messages[:-keep_recent]), list(messages[-keep_recent:])
