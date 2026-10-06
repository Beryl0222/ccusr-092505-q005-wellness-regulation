"""内存存储：实体表、事件日志、受理号索引与不可篡改表守护。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .errors import ImmutableRecordError, NotFoundError


class InMemoryStore:
    """按聚合分表的内存库。

    IMMUTABLE_TABLES 中的表（已发生的服务记录）只能追加，任何覆盖写入
    都会被拒绝，保证历史服务不被篡改。
    """

    IMMUTABLE_TABLES = frozenset({"service_records"})

    def __init__(self) -> None:
        self.entities: dict[str, Any] = {}
        self.licenses: dict[str, Any] = {}
        self.categories: dict[str, Any] = {}
        self.negative_items: dict[str, Any] = {}
        self.offers: dict[str, Any] = {}
        self.practitioners: dict[str, Any] = {}
        self.publicity_versions: dict[str, Any] = {}
        self.determinations: dict[str, Any] = {}
        self.service_records: dict[str, Any] = {}
        self.evidences: dict[str, Any] = {}
        self.complaints: dict[str, Any] = {}
        self.cases: dict[str, Any] = {}
        self.inspection_tasks: dict[str, Any] = {}
        self.remediation_plans: dict[str, Any] = {}
        self.restrictions: dict[str, Any] = {}
        self.penalties: dict[str, Any] = {}
        self.regulators: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []
        self._counters: dict[str, int] = defaultdict(int)
        # (source_type, source_key) -> evidence_id，重复取证保持原受理号
        self._acceptance_index: dict[tuple[str, str], str] = {}

    def next_id(self, prefix: str) -> str:
        self._counters[prefix] += 1
        return f"{prefix}-{self._counters[prefix]:04d}"

    def next_acceptance_no(self) -> str:
        self._counters["ACC"] += 1
        return f"SL{self._counters['ACC']:06d}"

    def acceptance_lookup(self, source_type: str, source_key: str) -> str | None:
        return self._acceptance_index.get((source_type, source_key))

    def acceptance_register(self, source_type: str, source_key: str, evidence_id: str) -> None:
        self._acceptance_index[(source_type, source_key)] = evidence_id

    def put(self, table: str, key: str, obj: Any) -> None:
        mapping = getattr(self, table)
        if table in self.IMMUTABLE_TABLES and key in mapping:
            raise ImmutableRecordError(f"{table} 中的记录 {key} 不可篡改")
        mapping[key] = obj

    def get(self, table: str, key: str) -> Any:
        try:
            return getattr(self, table)[key]
        except KeyError:
            raise NotFoundError(f"{table} 中不存在 {key}") from None

    def event_version(self, aggregate_type: str, aggregate_id: str) -> int:
        """该聚合已发生的事件数，用于生成递增版本。"""
        return sum(
            1
            for event in self.events
            if event["aggregate_type"] == aggregate_type and event["aggregate_id"] == aggregate_id
        )
