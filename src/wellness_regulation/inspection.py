"""巡查案件、整改计划与处罚。

三个聚合共同保证：
- 线上投诉先封存证据再分派（案件只在已有受理号后建立）；
- 跨区域按经营主体与发生地协同，分派/协同均留依据；
- 整改延期、复查结论、申诉与撤销都必须写明决定依据；
- 处罚可回溯到具体宣传版本、服务事实与当时有效资质。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .clock import Clock
from .engine import Aggregate, StoredEvent
from .errors import DomainError

# 案件状态
ST_FILED = "filed"
ST_ASSIGNED = "assigned"
ST_REMEDIATING = "remediating"
ST_APPEALED = "appealed"
ST_WITHDRAWN = "withdrawn"
ST_CLOSED = "closed"

# 整改状态
RM_OPEN = "open"
RM_PASSED = "passed"
RM_FAILED = "failed"
RM_CLOSED = "closed"


@dataclass(frozen=True)
class VersionNotice:
    offer_id: str
    version: int
    source: str  # promotion | pricing
    at: str
    basis: str


@dataclass(frozen=True)
class AccessRecord:
    inspector_id: str
    purpose: str
    at: str
    basis: str


@dataclass(frozen=True)
class JurisdictionLink:
    region: str
    inspector_id: str
    at: str
    basis: str


class InspectionCase(Aggregate):
    aggregate_type = "inspection_case"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.intake_no: str | None = None
        self.provider_id: str | None = None
        self.consumer_ref: str | None = None
        self.authorization_id: str | None = None
        self.occurrence_region: str | None = None
        self.lead_region: str | None = None
        self.lead_inspector: str | None = None
        self.status: str | None = None
        self.subject_offer_id: str | None = None
        self.subject_promotion_version: int | None = None
        self.jurisdictions: list[JurisdictionLink] = []
        self.notices: list[VersionNotice] = []
        self.access_log: list[AccessRecord] = []
        self.remediation_id: str | None = None
        self.penalty_ids: tuple[str, ...] = ()
        self.appeal_basis: str | None = None
        self.withdraw_basis: str | None = None
        self.closed_at: str | None = None

    # -- 受理与分派 ----------------------------------------------------------

    def file_complaint(
        self,
        clock: Clock,
        intake_no: str,
        provider_id: str,
        occurrence_region: str,
        offer_id: str,
        promotion_version: int,
        summary: str,
        consumer_ref: str | None = None,
        authorization_id: str | None = None,
    ) -> None:
        if self.status is not None:
            raise DomainError("案件已存在，不能重复受理")
        if not summary.strip():
            raise DomainError("受理必须记录投诉摘要")
        self._record(
            "COMPLAINT_FILED",
            f"受理投诉（受理号 {intake_no}，发生地 {occurrence_region}）",
            clock.now,
            clock.new_id,
            intake_no=intake_no,
            provider_id=provider_id,
            occurrence_region=occurrence_region,
            offer_id=offer_id,
            promotion_version=promotion_version,
            complaint_summary=summary,
            consumer_ref=consumer_ref,
            authorization_id=authorization_id,
        )

    def assign(
        self, clock: Clock, inspector_id: str, lead_region: str, basis: str
    ) -> None:
        if self.status != ST_FILED:
            raise DomainError("只有已受理未分派的案件可以分派")
        if not basis.strip():
            raise DomainError("分派必须记录依据")
        self._record(
            "CASE_ASSIGNED",
            f"案件分派给 {inspector_id}（{lead_region}）",
            clock.now,
            clock.new_id,
            inspector_id=inspector_id,
            lead_region=lead_region,
            basis=basis,
        )

    def link_jurisdiction(
        self, clock: Clock, region: str, inspector_id: str, basis: str
    ) -> None:
        """跨区域协同：协同方按经营主体属地或发生地加入。"""
        if self.status not in (ST_ASSIGNED, ST_REMEDIATING, ST_APPEALED):
            raise DomainError("当前案件状态不允许追加协同辖区")
        if not basis.strip():
            raise DomainError("跨区域协同必须记录依据")
        if region == self.lead_region:
            raise DomainError("主办辖区无需作为协同辖区重复关联")
        if any(j.region == region for j in self.jurisdictions):
            raise DomainError(f"辖区 {region} 已在协同范围内")
        self._record(
            "CASE_JURISDICTION_LINKED",
            f"关联协同辖区 {region}（{inspector_id}）",
            clock.now,
            clock.new_id,
            region=region,
            inspector_id=inspector_id,
            basis=basis,
        )

    # -- 变更通知与个人信息访问 ----------------------------------------------

    def notify_version_change(
        self,
        clock: Clock,
        offer_id: str,
        version: int,
        basis: str,
        source: str = "promotion",
    ) -> None:
        if self.status in (ST_CLOSED, ST_WITHDRAWN):
            return
        self._record(
            "CASE_NOTIFIED_OF_VERSION",
            f"在办案件收到 {offer_id} 新版本 v{version} 通知",
            clock.now,
            clock.new_id,
            offer_id=offer_id,
            content_version=version,
            source=source,
            basis=basis,
        )

    def record_personal_info_access(
        self, clock: Clock, inspector_id: str, purpose: str, basis: str
    ) -> None:
        if not basis.strip() or not purpose.strip():
            raise DomainError("访问个人信息必须说明目的与依据")
        self._record(
            "CASE_PERSONAL_INFO_ACCESSED",
            f"{inspector_id} 因 {purpose} 查阅个人信息",
            clock.now,
            clock.new_id,
            inspector_id=inspector_id,
            purpose=purpose,
            basis=basis,
        )

    # -- 整改/申诉/结案 -------------------------------------------------------

    def attach_remediation(self, plan_id: str) -> None:
        self.remediation_id = plan_id

    def start_remediation(self, clock: Clock, plan_id: str, basis: str) -> None:
        """责令整改时案件进入整改阶段（与 REMEDIATION_ORDERED 同事务产生）。"""
        if self.status != ST_ASSIGNED:
            raise DomainError("只有已分派案件可以进入整改阶段")
        self._record(
            "CASE_REMEDIATION_STARTED",
            "案件进入整改阶段",
            clock.now,
            clock.new_id,
            plan_id=plan_id,
            basis=basis,
        )

    def attach_penalty(self, clock: Clock, penalty_id: str) -> None:
        if penalty_id in self.penalty_ids:
            return
        self._record(
            "CASE_PENALTY_ATTACHED",
            f"案件关联处罚 {penalty_id}",
            clock.now,
            clock.new_id,
            penalty_id=penalty_id,
        )

    def appeal(self, clock: Clock, basis: str) -> None:
        if self.status not in (ST_ASSIGNED, ST_REMEDIATING):
            raise DomainError("当前状态不允许申诉")
        if not basis.strip():
            raise DomainError("申诉必须写明依据")
        self._record(
            "CASE_APPEALED",
            "消费者提出申诉",
            clock.now,
            clock.new_id,
            basis=basis,
        )

    def withdraw_appeal(self, clock: Clock, basis: str, restored_status: str) -> None:
        if self.status != ST_APPEALED:
            raise DomainError("只有申诉中的案件可以撤销申诉")
        if not basis.strip():
            raise DomainError("撤销申诉必须写明决定依据")
        if restored_status not in (ST_ASSIGNED, ST_REMEDIATING):
            raise DomainError("撤销后恢复状态非法")
        self._record(
            "CASE_WITHDRAWN",
            "申诉撤销，案件回到原办理状态",
            clock.now,
            clock.new_id,
            basis=basis,
            restored_status=restored_status,
        )

    def close(self, clock: Clock, conclusion: str, basis: str) -> None:
        if self.status not in (ST_ASSIGNED, ST_REMEDIATING):
            raise DomainError("当前案件状态不允许结案")
        if not conclusion.strip() or not basis.strip():
            raise DomainError("结案必须写明复核结论与依据")
        self._record(
            "REVIEW_CLOSED",
            f"结案：{conclusion}",
            clock.now,
            clock.new_id,
            conclusion=conclusion,
            basis=basis,
        )

    @property
    def is_open(self) -> bool:
        return self.status not in (ST_CLOSED, ST_WITHDRAWN)

    @property
    def involved_regions(self) -> tuple[str, ...]:
        regions = [self.lead_region] if self.lead_region else []
        regions.extend(j.region for j in self.jurisdictions)
        return tuple(r for r in regions if r)

    # -- apply --------------------------------------------------------------

    def apply_COMPLAINT_FILED(self, event: StoredEvent) -> None:
        self.intake_no = event.get("intake_no")
        self.provider_id = event.get("provider_id")
        self.consumer_ref = event.get("consumer_ref")
        self.authorization_id = event.get("authorization_id")
        self.occurrence_region = event.get("occurrence_region")
        self.subject_offer_id = event.get("offer_id")
        self.subject_promotion_version = event.get("promotion_version")
        self.lead_region = event.get("occurrence_region")
        self.status = ST_FILED

    def apply_CASE_ASSIGNED(self, event: StoredEvent) -> None:
        self.lead_inspector = event.get("inspector_id")
        self.lead_region = event.get("lead_region")
        self.status = ST_ASSIGNED

    def apply_CASE_REMEDIATION_STARTED(self, event: StoredEvent) -> None:
        self.remediation_id = event.get("plan_id")
        self.status = ST_REMEDIATING

    def apply_CASE_JURISDICTION_LINKED(self, event: StoredEvent) -> None:
        self.jurisdictions.append(
            JurisdictionLink(
                region=event.get("region"),
                inspector_id=event.get("inspector_id"),
                at=event.occurred_at,
                basis=event.get("basis"),
            )
        )

    def apply_CASE_NOTIFIED_OF_VERSION(self, event: StoredEvent) -> None:
        self.notices.append(
            VersionNotice(
                offer_id=event.get("offer_id"),
                version=event.get("content_version"),
                source=event.get("source", "promotion"),
                at=event.occurred_at,
                basis=event.get("basis", ""),
            )
        )

    def apply_CASE_PERSONAL_INFO_ACCESSED(self, event: StoredEvent) -> None:
        self.access_log.append(
            AccessRecord(
                inspector_id=event.get("inspector_id"),
                purpose=event.get("purpose"),
                at=event.occurred_at,
                basis=event.get("basis", ""),
            )
        )

    def apply_CASE_APPEALED(self, event: StoredEvent) -> None:
        self.appeal_basis = event.get("basis")
        self.status = ST_APPEALED

    def apply_CASE_PENALTY_ATTACHED(self, event: StoredEvent) -> None:
        penalty_id = event.get("penalty_id")
        if penalty_id not in self.penalty_ids:
            self.penalty_ids = self.penalty_ids + (penalty_id,)

    def apply_CASE_WITHDRAWN(self, event: StoredEvent) -> None:
        self.withdraw_basis = event.get("basis")
        self.status = event.get("restored_status", ST_ASSIGNED)

    def apply_REVIEW_CLOSED(self, event: StoredEvent) -> None:
        self.status = ST_CLOSED
        self.closed_at = event.occurred_at


# -- 整改 -------------------------------------------------------------------


@dataclass(frozen=True)
class DeadlineExtension:
    previous_deadline: str
    new_deadline: str
    basis: str
    at: str


class RemediationPlan(Aggregate):
    aggregate_type = "remediation_plan"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.case_id: str | None = None
        self.provider_id: str | None = None
        self.required_actions: tuple[str, ...] = ()
        self.deadline: str | None = None
        self.ordered_basis: str | None = None
        self.status: str | None = None
        self.extensions: list[DeadlineExtension] = []
        self.review_basis: str | None = None
        self.reviewed_at: str | None = None

    def order(
        self,
        clock: Clock,
        case_id: str,
        provider_id: str,
        actions: list[str],
        deadline: str,
        basis: str,
    ) -> None:
        if self.status is not None:
            raise DomainError("整改要求已下达")
        if not actions:
            raise DomainError("整改必须列出要求事项")
        if not basis.strip():
            raise DomainError("责令整改必须记录依据")
        _require_date(deadline)
        self._record(
            "REMEDIATION_ORDERED",
            f"责令整改，期限至 {deadline}",
            clock.now,
            clock.new_id,
            case_id=case_id,
            provider_id=provider_id,
            actions=list(actions),
            deadline=deadline,
            basis=basis,
        )

    def extend(self, clock: Clock, new_deadline: str, basis: str) -> None:
        if self.status != RM_OPEN:
            raise DomainError("只有进行中的整改可以延期")
        if not basis.strip():
            raise DomainError("整改延期必须写明决定依据")
        _require_date(new_deadline)
        if new_deadline <= self.deadline:
            raise DomainError("延期后的期限必须晚于原期限")
        self._record(
            "REMEDIATION_DEADLINE_EXTENDED",
            f"整改期限延长至 {new_deadline}",
            clock.now,
            clock.new_id,
            previous_deadline=self.deadline,
            new_deadline=new_deadline,
            basis=basis,
        )

    def record_review(self, clock: Clock, passed: bool, basis: str) -> None:
        if self.status != RM_OPEN:
            raise DomainError("整改不在可复核状态")
        if not basis.strip():
            raise DomainError("复核必须写明结论依据")
        event_type = "REVIEW_PASSED" if passed else "REVIEW_FAILED"
        result = "复核通过" if passed else "复核未通过"
        self._record(
            event_type,
            result,
            clock.now,
            clock.new_id,
            basis=basis,
        )

    def close(self, clock: Clock, basis: str) -> None:
        if self.status != RM_PASSED:
            raise DomainError("只有复核通过的整改可以关闭")
        if not basis.strip():
            raise DomainError("关闭整改必须记录依据")
        self._record(
            "REVIEW_CLOSED",
            "整改复核通过并关闭",
            clock.now,
            clock.new_id,
            basis=basis,
            conclusion="整改完成",
        )

    def is_overdue_on(self, day: datetime) -> bool:
        if self.status != RM_OPEN or self.deadline is None:
            return False
        return day > datetime.fromisoformat(self.deadline)

    def apply_REMEDIATION_ORDERED(self, event: StoredEvent) -> None:
        self.case_id = event.get("case_id")
        self.provider_id = event.get("provider_id")
        self.required_actions = tuple(event.get("actions", []))
        self.deadline = event.get("deadline")
        self.ordered_basis = event.get("basis")
        self.status = RM_OPEN

    def apply_REMEDIATION_DEADLINE_EXTENDED(self, event: StoredEvent) -> None:
        self.extensions.append(
            DeadlineExtension(
                previous_deadline=event.get("previous_deadline"),
                new_deadline=event.get("new_deadline"),
                basis=event.get("basis", ""),
                at=event.occurred_at,
            )
        )
        self.deadline = event.get("new_deadline")

    def apply_REVIEW_PASSED(self, event: StoredEvent) -> None:
        self.status = RM_PASSED
        self.review_basis = event.get("basis")
        self.reviewed_at = event.occurred_at

    def apply_REVIEW_FAILED(self, event: StoredEvent) -> None:
        self.status = RM_FAILED
        self.review_basis = event.get("basis")
        self.reviewed_at = event.occurred_at

    def apply_REVIEW_CLOSED(self, event: StoredEvent) -> None:
        self.status = RM_CLOSED


# -- 处罚 -------------------------------------------------------------------


@dataclass(frozen=True)
class PenaltyExplanation:
    penalty_id: str
    promotion: dict[str, object]   # 哪条宣传：offer/版本/文案指纹/受理号
    occurrence: dict[str, object]  # 哪次服务：服务事实标识、订单、发生地
    license_snapshot: dict[str, object]  # 哪份有效资质：资质号、有效期、核验依据
    regulation_clause: str
    decision: str
    basis: str


class Penalty(Aggregate):
    aggregate_type = "penalty"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.explanation: PenaltyExplanation | None = None

    def issue(
        self,
        clock: Clock,
        case_id: str,
        provider_id: str,
        promotion_ref: dict[str, object],
        occurrence_ref: dict[str, object],
        license_snapshot: dict[str, object],
        regulation_clause: str,
        decision: str,
        basis: str,
    ) -> None:
        if self.explanation is not None:
            raise DomainError("处罚已作出，不得改写；如需变更应追加新决定")
        for label, ref in (
            ("宣传", promotion_ref),
            ("服务", occurrence_ref),
            ("资质", license_snapshot),
        ):
            if not ref:
                raise DomainError(f"处罚必须关联{label}要素")
        if not regulation_clause.strip() or not decision.strip() or not basis.strip():
            raise DomainError("处罚必须写明条款、决定与依据")
        self._record(
            "PENALTY_ISSUED",
            f"作出处罚：{decision}",
            clock.now,
            clock.new_id,
            case_id=case_id,
            provider_id=provider_id,
            promotion_ref=dict(promotion_ref),
            occurrence_ref=dict(occurrence_ref),
            license_snapshot=dict(license_snapshot),
            regulation_clause=regulation_clause,
            decision=decision,
            basis=basis,
        )

    def explain(self) -> PenaltyExplanation:
        if self.explanation is None:
            raise DomainError("处罚尚未作出")
        return self.explanation

    def apply_PENALTY_ISSUED(self, event: StoredEvent) -> None:
        self.explanation = PenaltyExplanation(
            penalty_id=self.id,
            promotion=dict(event.get("promotion_ref", {})),
            occurrence=dict(event.get("occurrence_ref", {})),
            license_snapshot=dict(event.get("license_snapshot", {})),
            regulation_clause=event.get("regulation_clause"),
            decision=event.get("decision"),
            basis=event.get("basis", ""),
        )


def _require_date(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("期限必须包含时区")
