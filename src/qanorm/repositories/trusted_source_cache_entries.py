"""Repository helpers for bounded trusted-source cache entries."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from qanorm.models import TrustedSourceCacheEntry


class TrustedSourceCacheEntryRepository:
    """Data access helpers for search/page/extraction cache entries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_valid(self, *, cache_kind: str, cache_key: str, now: datetime | None = None) -> TrustedSourceCacheEntry | None:
        """Return one non-expired cache entry by logical key."""

        current_time = now or datetime.now(timezone.utc)
        stmt = (
            select(TrustedSourceCacheEntry)
            .where(
                TrustedSourceCacheEntry.cache_kind == cache_kind,
                TrustedSourceCacheEntry.cache_key == cache_key,
                TrustedSourceCacheEntry.expires_at > current_time,
            )
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def upsert(
        self,
        *,
        cache_kind: str,
        source_id: str,
        source_domain: str,
        cache_key: str,
        payload_json: dict[str, Any] | list[Any],
        expires_at: datetime,
        source_url: str | None = None,
        content_hash: str | None = None,
    ) -> TrustedSourceCacheEntry:
        """Insert or update a cache entry and flush it."""

        stmt = (
            select(TrustedSourceCacheEntry)
            .where(
                TrustedSourceCacheEntry.cache_kind == cache_kind,
                TrustedSourceCacheEntry.cache_key == cache_key,
            )
            .limit(1)
        )
        existing = self.session.execute(stmt).scalar_one_or_none()
        if existing is None:
            existing = TrustedSourceCacheEntry(
                cache_kind=cache_kind,
                source_id=source_id,
                source_domain=source_domain,
                cache_key=cache_key,
                payload_json=payload_json,
                expires_at=expires_at,
                source_url=source_url,
                content_hash=content_hash,
                last_accessed_at=datetime.now(timezone.utc),
            )
            self.session.add(existing)
        else:
            existing.source_id = source_id
            existing.source_domain = source_domain
            existing.payload_json = payload_json
            existing.expires_at = expires_at
            existing.source_url = source_url
            existing.content_hash = content_hash
            existing.last_accessed_at = datetime.now(timezone.utc)
        self.session.flush()
        return existing

    def touch(self, entry: TrustedSourceCacheEntry, *, now: datetime | None = None) -> TrustedSourceCacheEntry:
        """Refresh access timestamp for one cache entry."""

        entry.last_accessed_at = now or datetime.now(timezone.utc)
        self.session.flush()
        return entry

    def delete_expired(self, *, now: datetime | None = None) -> int:
        """Delete expired cache entries and return the affected row count."""

        current_time = now or datetime.now(timezone.utc)
        result = self.session.execute(
            delete(TrustedSourceCacheEntry).where(TrustedSourceCacheEntry.expires_at <= current_time)
        )
        self.session.flush()
        return int(result.rowcount or 0)

    def invalidate_source(self, source_id: str) -> int:
        """Delete all cache entries for one trusted source."""

        result = self.session.execute(delete(TrustedSourceCacheEntry).where(TrustedSourceCacheEntry.source_id == source_id))
        self.session.flush()
        return int(result.rowcount or 0)

    def list_by_keys(self, *, cache_kind: str, cache_keys: Iterable[str]) -> list[TrustedSourceCacheEntry]:
        """Return multiple cache entries for bulk cache lookup."""

        keys = [item for item in cache_keys if item]
        if not keys:
            return []
        stmt = select(TrustedSourceCacheEntry).where(
            TrustedSourceCacheEntry.cache_kind == cache_kind,
            TrustedSourceCacheEntry.cache_key.in_(keys),
        )
        return list(self.session.execute(stmt).scalars().all())
