"""仅追加的事件存储与聚合重放机制。

业务状态只由领域事件重放得到：任何决定都可追溯到具体事件，
已发生的事实不会被 UPDATE/DELETE 覆盖。
"""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .contracts import validate_event
from .errors import ConcurrencyError, ContractViolation

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "domain.schema.json"


def load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


IdGenerator = Callable[[], str]


def uuid_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class StoredEvent:
    """已落库事件。business 中保留事件全部字段（含信封字段）。"""

    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    occurred_at: str
    version: int
    summary: str
    data: Mapping[str, Any]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "StoredEvent":
        return cls(
            event_id=payload["event_id"],
            event_type=payload["event_type"],
            aggregate_type=payload["aggregate_type"],
            aggregate_id=payload["aggregate_id"],
            occurred_at=payload["occurred_at"],
            version=payload["version"],
            summary=payload["summary"],
            data=dict(payload),
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


Listener = Callable[[StoredEvent], None]


class EventStore:
    """内存事件库；可整体导出/导入，版本在每个聚合内严格连续。"""

    def __init__(self, schema: Mapping[str, Any] | None = None) -> None:
        self._events: list[StoredEvent] = []
        self._schema = schema or load_schema()
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    # -- 写入 ---------------------------------------------------------------

    def append(self, payload: Mapping[str, Any]) -> StoredEvent:
        issues = validate_event(payload, self._schema)
        if issues:
            raise ContractViolation([f"{i.field}:{i.code}" for i in issues])
        aggregate_id = payload["aggregate_id"]
        expected_version = payload["version"]
        current = self.version_of(aggregate_id)
        if expected_version != current + 1:
            raise ConcurrencyError(
                f"聚合 {aggregate_id} 期望版本 {current + 1}，收到 {expected_version}"
            )
        stored = StoredEvent.from_payload(payload)
        self._events.append(stored)
        for listener in list(self._listeners):
            listener(stored)
        return stored

    # -- 读取 ---------------------------------------------------------------

    def version_of(self, aggregate_id: str) -> int:
        version = 0
        for event in self._events:
            if event.aggregate_id == aggregate_id:
                version = event.version
        return version

    def events_for(self, aggregate_id: str) -> list[StoredEvent]:
        return [e for e in self._events if e.aggregate_id == aggregate_id]

    def all_events(self) -> list[StoredEvent]:
        return list(self._events)

    def export_json(self) -> str:
        return json.dumps([dict(e.data) for e in self._events], ensure_ascii=False, indent=2)

    def load_json(self, raw: str) -> None:
        for payload in json.loads(raw):
            self.append(payload)


class Aggregate:
    """事件溯源聚合基类：状态只能通过 _record + apply 改变。"""

    aggregate_type: str = ""

    def __init__(self, aggregate_id: str) -> None:
        self.id = aggregate_id
        self.version = 0
        self._pending: list[dict[str, Any]] = []

    # -- 产生事件 -----------------------------------------------------------

    def _record(
        self,
        event_type: str,
        summary: str,
        clock: Callable[[], datetime],
        id_generator: IdGenerator,
        **data: Any,
    ) -> None:
        reserved = {"event_id", "event_type", "aggregate_type", "aggregate_id",
                    "occurred_at", "version", "summary"}
        collision = reserved.intersection(data)
        if collision:
            raise ValueError(f"事件数据字段不得占用信封保留字：{sorted(collision)}")
        self.version += 1
        payload = {
            "event_id": id_generator(),
            "event_type": event_type,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.id,
            "occurred_at": clock().isoformat(),
            "version": self.version,
            "summary": summary,
        }
        payload.update(copy.deepcopy(data))
        self._pending.append(payload)
        self.apply(StoredEvent.from_payload(payload))

    def pending_events(self) -> list[dict[str, Any]]:
        return list(self._pending)

    def clear_pending(self) -> None:
        self._pending.clear()

    # -- 重放 ---------------------------------------------------------------

    @classmethod
    def replay(cls, aggregate_id: str, events: Iterable[StoredEvent]) -> "Aggregate":
        aggregate = cls(aggregate_id)
        for event in events:
            aggregate.apply(event)
            aggregate.version = event.version
        aggregate._pending.clear()
        return aggregate

    def apply(self, event: StoredEvent) -> None:
        handler = getattr(self, f"apply_{event.event_type}", None)
        if handler is not None:
            handler(event)
