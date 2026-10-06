"""聚合仓储：从仅追加事件库装载、保存聚合。"""

from __future__ import annotations

from typing import TypeVar

from .engine import Aggregate, EventStore

T = TypeVar("T", bound=Aggregate)


class Repository:
    def __init__(self, store: EventStore) -> None:
        self.store = store

    def load(self, cls: type[T], aggregate_id: str) -> T:
        events = self.store.events_for(aggregate_id)
        return cls.replay(aggregate_id, events)  # type: ignore[return-value]

    def try_load(self, cls: type[T], aggregate_id: str) -> T | None:
        if not self.store.events_for(aggregate_id):
            return None
        return self.load(cls, aggregate_id)

    def save(self, aggregate: Aggregate) -> None:
        for payload in aggregate.pending_events():
            self.store.append(payload)
        aggregate.clear_pending()

    def save_many(self, *aggregates: Aggregate) -> None:
        for aggregate in aggregates:
            self.save(aggregate)
