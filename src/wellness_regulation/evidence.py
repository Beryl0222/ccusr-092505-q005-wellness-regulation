"""消费者授权与证据封存。

- 监管员访问个人信息以消费者授权 + 辖区职责双重门控（门控在 facade 执行）。
- 线上投诉先封存、后分派：EVIDENCE_SEALED 早于 CASE_ASSIGNED。
- 同一网页/订单指纹重复取证沿用首个受理号，作为同受理号下的追加封存。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .clock import Clock
from .engine import Aggregate, StoredEvent
from .errors import DomainError


class ConsumerAuthorization(Aggregate):
    aggregate_type = "consumer_authorization"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.consumer_ref: str | None = None
        self.scope: str | None = None
        self.valid_until: str | None = None
        self.active = False
        self.granted_at: str | None = None
        self.revoked_at: str | None = None
        self.revoke_basis: str | None = None

    def grant(
        self, clock: Clock, consumer_ref: str, scope: str, valid_until: str
    ) -> None:
        if self.active:
            raise DomainError("授权已存在，重复授权请先走撤销或续期")
        self._record(
            "CONSUMER_AUTHORIZATION_GRANTED",
            f"消费者 {consumer_ref} 授权使用个人信息（{scope}）",
            clock.now,
            clock.new_id,
            consumer_ref=consumer_ref,
            scope=scope,
            valid_until=valid_until,
        )

    def revoke(self, clock: Clock, basis: str) -> None:
        if not basis.strip():
            raise DomainError("撤销授权必须记录依据")
        if not self.active:
            raise DomainError("授权不处于有效状态")
        self._record(
            "CONSUMER_AUTHORIZATION_REVOKED",
            f"撤销消费者 {self.consumer_ref} 的授权",
            clock.now,
            clock.new_id,
            basis=basis,
        )

    def valid_on(self, day: datetime) -> bool:
        if not self.active or self.valid_until is None:
            return False
        return day <= datetime.fromisoformat(self.valid_until)

    def apply_CONSUMER_AUTHORIZATION_GRANTED(self, event: StoredEvent) -> None:
        self.consumer_ref = event.get("consumer_ref")
        self.scope = event.get("scope")
        self.valid_until = event.get("valid_until")
        self.active = True
        self.granted_at = event.occurred_at

    def apply_CONSUMER_AUTHORIZATION_REVOKED(self, event: StoredEvent) -> None:
        self.active = False
        self.revoked_at = event.occurred_at
        self.revoke_basis = event.get("basis")


@dataclass(frozen=True)
class EvidenceRevision:
    revision_no: int
    fingerprint: str
    captured_at: str
    version: int  # 事件版本


class EvidenceRecord(Aggregate):
    """以受理号为聚合标识的封存证据。

    受理号按来源（网页 URL/订单号）稳定：
    - 同来源、同指纹重复取证：duplicate，沿用受理号，不形成修订版；
    - 同来源、不同指纹：revised，沿用受理号，形成新修订版（调用方据此
      形成宣传/价格新版本并通知在办案件）。
    """

    aggregate_type = "evidence_record"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.intake_no = aggregate_id
        self.kind: str | None = None  # webpage | order
        self.source_ref: str | None = None
        self.revisions: list[EvidenceRevision] = []
        self.linked_cases: tuple[str, ...] = ()

    @property
    def latest_fingerprint(self) -> str | None:
        return self.revisions[-1].fingerprint if self.revisions else None

    def seal(
        self,
        clock: Clock,
        fingerprint: str,
        kind: str,
        source_ref: str,
        captured_at: str,
        linked_case: str | None = None,
    ) -> tuple[str, int]:
        """封存一次取证。返回 (new|duplicate|revised, 修订号)。"""
        if kind not in ("webpage", "order"):
            raise ValueError("证据种类只能是 webpage 或 order")
        if self.kind is None:
            self.kind = kind
            self.source_ref = source_ref
        elif source_ref != self.source_ref:
            raise DomainError(
                f"受理号 {self.intake_no} 已绑定来源 {self.source_ref}，不能封存其他来源"
            )
        latest = self.revisions[-1] if self.revisions else None
        if latest is not None and fingerprint == latest.fingerprint:
            status = "duplicate"
        else:
            status = "new" if latest is None else "revised"
        revision_no = 1 if latest is None or status == "new" else (
            latest.revision_no if status == "duplicate" else latest.revision_no + 1
        )
        cases = tuple(dict.fromkeys(self.linked_cases + ((linked_case,) if linked_case else ())))
        self._record(
            "EVIDENCE_SEALED",
            f"{'首次' if status == 'new' else ('重复' if status == 'duplicate' else '内容变更')}"
            f"封存证据，受理号 {self.intake_no}",
            clock.now,
            clock.new_id,
            intake_no=self.intake_no,
            fingerprint=fingerprint,
            kind=kind,
            source_ref=source_ref,
            captured_at=captured_at,
            status=status,
            revision_no=revision_no,
            linked_cases=list(cases),
        )
        return status, revision_no

    def link_case(self, case_id: str) -> None:
        if case_id not in self.linked_cases:
            self.linked_cases = tuple(dict.fromkeys(self.linked_cases + (case_id,)))

    def apply_EVIDENCE_SEALED(self, event: StoredEvent) -> None:
        status = event.get("status")
        if status in ("new", "revised"):
            self.revisions.append(
                EvidenceRevision(
                    revision_no=event.get("revision_no"),
                    fingerprint=event.get("fingerprint"),
                    captured_at=event.get("captured_at"),
                    version=event.version,
                )
            )
        self.kind = event.get("kind")
        self.source_ref = event.get("source_ref")
        self.linked_cases = tuple(event.get("linked_cases", []))
