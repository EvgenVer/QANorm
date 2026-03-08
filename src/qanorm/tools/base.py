"""Base contracts and audited execution registry for Stage 2 tools."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from hashlib import sha256
from time import perf_counter
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from qanorm.db.types import ToolInvocationStatus
from qanorm.models import ToolInvocation
from qanorm.repositories import ToolInvocationRepository


ToolScope = Literal[
    "normative",
    "document",
    "freshness",
    "refresh",
    "trusted_web",
    "open_web",
    "source_extract",
    "answer_format",
]


DEFAULT_ALLOWED_SCOPES: frozenset[ToolScope] = frozenset(
    {
        "normative",
        "document",
        "freshness",
        "refresh",
        "trusted_web",
        "open_web",
        "source_extract",
        "answer_format",
    }
)


class ToolError(RuntimeError):
    """Base error raised by the tool layer."""


class ToolPolicyError(ToolError):
    """Raised when a tool call violates the configured scope policy."""


class ToolInputError(ToolError):
    """Raised when a tool payload is malformed."""


@dataclass(slots=True, frozen=True)
class ToolDefinition:
    """Static metadata describing one registered tool."""

    name: str
    scope: ToolScope
    description: str
    mutates_state: bool = False


@dataclass(slots=True)
class ToolExecutionContext:
    """Context passed to every tool invocation."""

    session: Session
    query_id: UUID
    subtask_id: UUID | None = None
    allowed_scopes: frozenset[ToolScope] = DEFAULT_ALLOWED_SCOPES
    metadata: dict[str, Any] = field(default_factory=dict)

    def ensure_scope_allowed(self, scope: ToolScope) -> None:
        """Fail fast when orchestration policy does not allow the requested tool scope."""

        if scope not in self.allowed_scopes:
            allowed = ", ".join(sorted(self.allowed_scopes))
            raise ToolPolicyError(f"Tool scope '{scope}' is not allowed. Allowed scopes: {allowed}.")


@dataclass(slots=True, frozen=True)
class ToolResult:
    """Normalized result returned by all tools."""

    payload: dict[str, Any]
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)
    invocation_id: UUID | None = None


class Tool(ABC):
    """Base class implemented by all Stage 2 tools."""

    definition: ToolDefinition

    @abstractmethod
    async def execute(self, context: ToolExecutionContext, payload: dict[str, Any]) -> ToolResult:
        """Execute the tool and return a normalized result."""


class ToolRegistry:
    """Registry that enforces scope policy and audits every tool call."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register one tool implementation by its canonical name."""

        self._tools[tool.definition.name] = tool

    def get(self, name: str) -> Tool:
        """Return one registered tool by name."""

        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolInputError(f"Tool '{name}' is not registered.") from exc

    def list_registered(self) -> dict[str, ToolDefinition]:
        """Return a snapshot of registered tools for diagnostics and tests."""

        return {name: tool.definition for name, tool in self._tools.items()}

    async def invoke(
        self,
        name: str,
        *,
        context: ToolExecutionContext,
        payload: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute one tool while recording a durable audit row."""

        tool = self.get(name)
        context.ensure_scope_allowed(tool.definition.scope)

        payload_dict = payload or {}
        invocation = self._create_invocation(tool=tool, context=context, payload=payload_dict)
        started_at = perf_counter()
        try:
            result = await tool.execute(context, payload_dict)
        except Exception as exc:
            invocation.status = ToolInvocationStatus.FAILED
            invocation.duration_ms = self._duration_ms(started_at)
            invocation.result_summary = str(exc)[:1000]
            context.session.flush()
            raise

        invocation.status = ToolInvocationStatus.COMPLETED
        invocation.duration_ms = self._duration_ms(started_at)
        invocation.result_summary = result.summary[:1000]
        context.session.flush()
        return ToolResult(
            payload=result.payload,
            summary=result.summary,
            metadata=result.metadata,
            invocation_id=invocation.id,
        )

    def _create_invocation(
        self,
        *,
        tool: Tool,
        context: ToolExecutionContext,
        payload: dict[str, Any],
    ) -> ToolInvocation:
        """Insert a running invocation row before execution starts."""

        repository = ToolInvocationRepository(context.session)
        invocation = ToolInvocation(
            query_id=context.query_id,
            subtask_id=context.subtask_id,
            tool_name=tool.definition.name,
            tool_scope=tool.definition.scope,
            input_hash=self._hash_payload(payload),
            status=ToolInvocationStatus.RUNNING,
        )
        return repository.add(invocation)

    def _hash_payload(self, payload: dict[str, Any]) -> str:
        """Build a stable payload fingerprint for idempotence and audit."""

        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return sha256(encoded.encode("utf-8")).hexdigest()

    def _duration_ms(self, started_at: float) -> int:
        """Convert a monotonic interval into integer milliseconds."""

        return max(0, round((perf_counter() - started_at) * 1000))
