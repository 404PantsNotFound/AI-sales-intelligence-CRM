from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from threading import RLock
from typing import Any

from app.core.config import settings
from app.core.exceptions import APIError
from app.schemas.agent import AgentActionName


@dataclass(frozen=True)
class PendingActionRecord:
    action_id: str
    action: AgentActionName
    payload: dict[str, Any]
    thread_id: str
    expires_at: datetime
    user_id: int = 0


class PendingActionStore:
    def __init__(self, max_capacity: int | None = None) -> None:
        self._pending: dict[str, PendingActionRecord] = {}
        self._claimed: set[str] = set()
        self._lock = RLock()
        self._max_capacity = max_capacity

    @property
    def max_capacity(self) -> int:
        if self._max_capacity is not None:
            return self._max_capacity
        return settings.pending_action_max_capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)

    def add(
        self,
        action: AgentActionName,
        payload: dict[str, Any],
        thread_id: str,
        *,
        user_id: int,
    ) -> PendingActionRecord:
        now = datetime.now(timezone.utc)
        action_id = token_urlsafe(32)
        record = PendingActionRecord(
            action_id=action_id,
            action=action,
            payload=payload,
            thread_id=thread_id,
            expires_at=now + timedelta(seconds=settings.agent_action_ttl_seconds),
            user_id=user_id,
        )
        with self._lock:
            self._purge_expired(now)
            self._evict_if_at_capacity()
            self._pending[action_id] = record
        return record

    def _evict_if_at_capacity(self) -> None:
        from app.agent.actions import action_checkpointer

        capacity = max(1, self.max_capacity)
        while len(self._pending) >= capacity:
            unclaimed = [
                record
                for action_id, record in self._pending.items()
                if action_id not in self._claimed
            ]
            if not unclaimed:
                raise APIError(
                    "Too many pending actions are currently in progress. Please try again shortly.",
                    429,
                    "pending_actions_full",
                )
            oldest = min(unclaimed, key=lambda item: item.expires_at)
            self._pending.pop(oldest.action_id, None)
            action_checkpointer.delete_thread(oldest.thread_id)

    def _purge_expired(self, now: datetime) -> None:
        from app.agent.actions import action_checkpointer

        expired = [
            action_id
            for action_id, record in self._pending.items()
            if record.expires_at <= now
        ]
        for action_id in expired:
            record = self._pending.pop(action_id)
            self._claimed.discard(action_id)
            action_checkpointer.delete_thread(record.thread_id)

    def claim(self, action_id: str, *, user_id: int) -> PendingActionRecord:
        with self._lock:
            record = self._pending.get(action_id)
            if record is None:
                self._purge_expired(datetime.now(timezone.utc))
                raise APIError("Pending action not found.", 404, "action_not_found")
            now = datetime.now(timezone.utc)
            if record.expires_at <= now:
                self._pending.pop(action_id, None)
                self._claimed.discard(action_id)
                from app.agent.actions import action_checkpointer

                action_checkpointer.delete_thread(record.thread_id)
                self._purge_expired(now)
                raise APIError("Pending action has expired.", 410, "action_expired")
            self._purge_expired(now)
            if record.user_id != user_id:
                raise APIError(
                    "You are not authorized to confirm or cancel this pending action.",
                    403,
                    "forbidden",
                )
            if action_id in self._claimed:
                raise APIError(
                    "Pending action is already being processed.",
                    409,
                    "action_already_processed",
                )
            self._claimed.add(action_id)
            return record

    def remove(self, action_id: str) -> None:
        with self._lock:
            self._pending.pop(action_id, None)
            self._claimed.discard(action_id)


pending_actions = PendingActionStore()


