"""巡检库命令总装：把事件库、聚合、投影、访问策略组合成监管可用的用例。

所有写操作都经过：聚合校验规则 -> 事件契约校验 -> 仅追加落库 -> 投影更新。
重启时用 from_events 重放全部事件即可恢复状态与提醒。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .catalog import (
    KIND_GENERAL,
    ClaimAnalysis,
    PricingAnalysis,
    ServiceCategory,
    analyze_pricing,
)
from .clock import Clock
from .engine import EventStore
from .evidence import ConsumerAuthorization, EvidenceRecord
from .inspection import (
    InspectionCase,
    Penalty,
    RemediationPlan,
)
from .offer import ServiceOffer
from .people import Practitioner, ServiceOccurrence
from .projections import (
    LicenseDue,
    PendingReview,
    ProjectionSet,
)
from .provider import LicenseView, Provider
from .repository import Repository
from .security import AccessPolicy, Inspector
from .errors import AccessDenied, DomainError, ImmutableFactError

WEB = "webpage"
ORDER = "order"


class RegulationSystem:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or Clock()
        self.store = EventStore()
        self.repo = Repository(self.store)
        self.projections = ProjectionSet()
        self.store.subscribe(self.projections.handle)
        self.policy = AccessPolicy()

    # -- 重建 ----------------------------------------------------------------

    @classmethod
    def from_events(cls, events: list[dict[str, Any]], clock: Clock | None = None) -> "RegulationSystem":
        system = cls(clock=clock)
        system.store.load_json(json.dumps(events, ensure_ascii=False))
        return system

    def export_events(self) -> list[dict[str, Any]]:
        return [dict(e.data) for e in self.store.all_events()]

    # -- 服务类别与负面清单 ---------------------------------------------------

    def define_category(self, code: str, name: str, kind: str, description: str = "") -> None:
        category = self.repo.try_load(ServiceCategory, code)
        if category is not None and category.code is not None:
            raise DomainError(f"服务类别 {code} 已存在")
        category = ServiceCategory(code)
        category.define(self.clock, code, name, kind, description)
        self.repo.save(category)

    def update_negative_list(
        self, code: str, prohibited_terms: list[str], prohibited_practices: list[str], basis: str
    ) -> None:
        category = self._load_category(code)
        category.update_negative_list(self.clock, prohibited_terms, prohibited_practices, basis)
        self.repo.save(category)

    def analyze_claim(self, category_code: str, text: str) -> ClaimAnalysis:
        return self._load_category(category_code).analyze(text)

    def analyze_pricing(self, terms: dict[str, Any]) -> PricingAnalysis:
        return analyze_pricing(terms)

    # -- 经营主体与资质 -------------------------------------------------------

    def register_provider(self, name: str, home_region: str, provider_id: str | None = None) -> str:
        provider_id = provider_id or f"provider-{self.clock.new_id()[:12]}"
        provider = Provider(provider_id)
        provider.register(self.clock, name, home_region)
        self.repo.save(provider)
        return provider_id

    def verify_license(
        self,
        provider_id: str,
        license_id: str,
        scope: str,
        valid_from: str,
        valid_until: str,
        basis: str,
    ) -> None:
        provider = self._load_provider(provider_id)
        provider.verify_license(self.clock, license_id, scope, valid_from, valid_until, basis)
        self.repo.save(provider)
        self._enforce_provider_policy(provider_id, required_scopes=())

    def renew_license(self, provider_id: str, license_id: str, new_valid_until: str, basis: str) -> None:
        provider = self._load_provider(provider_id)
        provider.renew_license(self.clock, license_id, new_valid_until, basis)
        self.repo.save(provider)
        # 续期解决了资质问题，限单是否解除须经复查流程，不自动恢复。

    def enforce_provider_policy(
        self, provider_id: str, required_scopes: tuple[str, ...] = ()
    ) -> list[str]:
        """资质过期或触碰禁项的集中判定点；命中立即限单。返回本次触发原因。"""
        return self._enforce_provider_policy(provider_id, required_scopes)

    def restore_new_orders(self, provider_id: str, basis: str) -> None:
        provider = self._load_provider(provider_id)
        provider.restore_new_orders(self.clock, basis)
        self.repo.save(provider)

    def place_new_order(self, provider_id: str) -> None:
        """下单闸口：被限单主体不得产生新订单；已发生服务不受影响。"""
        provider = self._load_provider(provider_id)
        if provider.order_blocked:
            raise DomainError(f"主体已被限制新订单：{'、'.join(provider.block_reasons)}")

    def screen_offer(self, offer_id: str, category_code: str, text: str) -> ClaimAnalysis:
        """宣传巡检：区分一般体验与医疗诊疗边界，越界或触碰禁项立即限单。"""
        offer = self._load_offer(offer_id)
        category = self._load_category(category_code)
        analysis = category.analyze(text)
        boundary_crossed = (
            category.kind == KIND_GENERAL and analysis.crosses_medical_boundary
        )
        if boundary_crossed or analysis.hits_negative_list:
            provider = self._load_provider(offer.provider_id)
            terms = tuple(sorted({h.term for h in analysis.hits if h.kind == "prohibited"}))
            reasons = provider.evaluate_block_reasons(
                self.clock.now(),
                prohibited_hits=tuple(terms),
                medical_boundary_crossed=boundary_crossed,
            )
            if reasons and not provider.order_blocked:
                provider.block_new_orders(self.clock, reasons, basis="宣传巡检触碰医疗边界或负面清单禁项")
                self.repo.save(provider)
        return analysis

    # -- 服务项目、宣传与价格版本 ---------------------------------------------

    def register_offer(
        self, provider_id: str, category_code: str, title: str, offer_id: str | None = None
    ) -> str:
        self._load_category(category_code)
        offer_id = offer_id or f"offer-{self.clock.new_id()[:12]}"
        offer = ServiceOffer(offer_id)
        offer.register(self.clock, provider_id, category_code, title)
        self.repo.save(offer)
        return offer_id

    def seal_evidence(
        self,
        fingerprint: str,
        kind: str,
        source_ref: str,
        captured_at: str,
        intake_no: str | None = None,
        linked_case: str | None = None,
    ) -> tuple[str, str, int]:
        """封存证据。

        返回 (受理号, new|duplicate|revised, 修订号)。
        同一网页/订单（kind+source_ref）永远沿用其首个受理号；
        指纹重复为 duplicate，内容变化为 revised 并形成新修订版。
        """
        existing = self.projections.intake.intake_for_source(kind, source_ref)
        if existing is not None:
            record = self.repo.load(EvidenceRecord, existing)
            status, revision_no = record.seal(
                self.clock, fingerprint, kind, source_ref, captured_at, linked_case
            )
            self.repo.save(record)
            return existing, status, revision_no
        intake_no = intake_no or f"intake-{self.clock.new_id()[:12]}"
        record = EvidenceRecord(intake_no)
        status, revision_no = record.seal(
            self.clock, fingerprint, kind, source_ref, captured_at, linked_case
        )
        self.repo.save(record)
        return intake_no, status, revision_no

    def capture_promotion(
        self,
        offer_id: str,
        text: str,
        fingerprint: str,
        city: str | None,
        intake_no: str,
    ) -> tuple[str, int]:
        offer = self._load_offer(offer_id)
        outcome, version = offer.capture_promotion(
            self.clock, text, fingerprint, intake_no, city
        )
        self.repo.save(offer)
        if outcome == "new":
            self._notify_open_cases(offer_id, version, "promotion")
        return outcome, version

    def publish_pricing_terms(
        self, offer_id: str, terms: dict[str, Any], fingerprint: str, intake_no: str
    ) -> tuple[str, int]:
        offer = self._load_offer(offer_id)
        outcome, version = offer.publish_pricing_terms(self.clock, terms, fingerprint, intake_no)
        self.repo.save(offer)
        if outcome == "new":
            self._notify_open_cases(offer_id, version, "pricing")
        return outcome, version

    # -- 从业人员与服务事实 ---------------------------------------------------

    def register_practitioner(
        self,
        name: str,
        provider_id: str,
        credential_scope: str,
        credential_valid_until: str,
        practitioner_id: str | None = None,
    ) -> str:
        practitioner_id = practitioner_id or f"prac-{self.clock.new_id()[:12]}"
        practitioner = Practitioner(practitioner_id)
        practitioner.register(
            self.clock, name, provider_id, credential_scope, credential_valid_until
        )
        self.repo.save(practitioner)
        return practitioner_id

    def record_service_occurrence(
        self,
        provider_id: str,
        offer_id: str,
        practitioner_id: str,
        consumer_ref: str,
        service_region: str,
        order_ref: str,
        rendered_at: str,
        component: str = "package",
        occurrence_id: str | None = None,
    ) -> str:
        occurrence_id = occurrence_id or f"occ-{self.clock.new_id()[:12]}"
        existing = self.repo.try_load(ServiceOccurrence, occurrence_id)
        if existing is not None and existing.service is not None:
            raise ImmutableFactError("已经发生的服务记录不得篡改或重复登记")
        occurrence = existing or ServiceOccurrence(occurrence_id)
        occurrence.record(
            self.clock,
            provider_id=provider_id,
            offer_id=offer_id,
            practitioner_id=practitioner_id,
            consumer_ref=consumer_ref,
            service_region=service_region,
            order_ref=order_ref,
            rendered_at=rendered_at,
            component=component,
        )
        self.repo.save(occurrence)
        return occurrence_id

    # -- 消费者授权 -----------------------------------------------------------

    def grant_authorization(
        self, consumer_ref: str, scope: str, valid_until: str, auth_id: str | None = None
    ) -> str:
        auth_id = auth_id or f"auth-{self.clock.new_id()[:12]}"
        auth = ConsumerAuthorization(auth_id)
        auth.grant(self.clock, consumer_ref, scope, valid_until)
        self.repo.save(auth)
        return auth_id

    def revoke_authorization(self, auth_id: str, basis: str) -> None:
        auth = self.repo.load(ConsumerAuthorization, auth_id)
        auth.revoke(self.clock, basis)
        self.repo.save(auth)

    # -- 投诉、分派与协同 ------------------------------------------------------

    def file_online_complaint(
        self,
        provider_id: str,
        occurrence_region: str,
        offer_id: str,
        intake_no: str,
        summary: str,
        *,
        consumer_ref: str | None = None,
        authorization_id: str | None = None,
        case_id: str | None = None,
    ) -> str:
        # 先封存、后分派：受理时证据受理号必须已经存在
        if self.repo.try_load(EvidenceRecord, intake_no) is None:
            raise DomainError("线上投诉必须先封存证据再受理分派")
        offer = self._load_offer(offer_id)
        promotion_version = offer.promotions[-1].version if offer.promotions else 0
        case_id = case_id or f"case-{self.clock.new_id()[:12]}"
        case = InspectionCase(case_id)
        case.file_complaint(
            self.clock,
            intake_no=intake_no,
            provider_id=provider_id,
            occurrence_region=occurrence_region,
            offer_id=offer_id,
            promotion_version=promotion_version,
            summary=summary,
            consumer_ref=consumer_ref,
            authorization_id=authorization_id,
        )
        self.repo.save(case)
        record = self.repo.load(EvidenceRecord, intake_no)
        record.link_case(case_id)
        return case_id

    def assign_case(self, case_id: str, inspector: Inspector, basis: str) -> None:
        case = self._load_case(case_id)
        if case.lead_region not in inspector.regions:
            raise AccessDenied("监管员只能在职责辖区内主办案件")
        case.assign(self.clock, inspector.inspector_id, case.lead_region or "", basis)
        self.repo.save(case)

    def link_jurisdiction(
        self, case_id: str, inspector: Inspector, region: str, basis: str
    ) -> None:
        case = self._load_case(case_id)
        # 跨区域协同按经营主体属地与发生地开展：协同辖区必须落在二者之内
        provider = self._load_provider(case.provider_id)  # type: ignore[arg-type]
        allowed_regions = {case.occurrence_region, provider.home_region}
        if region not in allowed_regions:
            raise DomainError(
                "协同辖区只能是经营主体属地或服务发生地："
                + "、".join(sorted(r for r in allowed_regions if r))
            )
        if region not in inspector.regions:
            raise AccessDenied("监管员只能关联本人职责范围内的协同辖区")
        case.link_jurisdiction(self.clock, region, inspector.inspector_id, basis)
        self.repo.save(case)

    def access_personal_info(
        self, case_id: str, inspector: Inspector, purpose: str, basis: str
    ) -> None:
        case = self._load_case(case_id)
        authorization_active = False
        if case.authorization_id:
            auth = self.repo.load(ConsumerAuthorization, case.authorization_id)
            authorization_active = auth.valid_on(self.clock.now())
        self.policy.authorize_personal_info(
            inspector, case, authorization_active=authorization_active
        )
        case.record_personal_info_access(self.clock, inspector.inspector_id, purpose, basis)
        self.repo.save(case)

    # -- 整改、申诉、复核 ------------------------------------------------------

    def order_remediation(
        self, case_id: str, actions: list[str], deadline: str, basis: str, plan_id: str | None = None
    ) -> str:
        case = self._load_case(case_id)
        plan_id = plan_id or f"plan-{self.clock.new_id()[:12]}"
        plan = RemediationPlan(plan_id)
        plan.order(self.clock, case_id, case.provider_id or "", actions, deadline, basis)
        if case.status == "assigned":
            case.start_remediation(self.clock, plan_id, basis)
        else:
            case.attach_remediation(plan_id)
        self.repo.save(plan)
        self.repo.save(case)
        return plan_id

    def extend_remediation(self, plan_id: str, new_deadline: str, basis: str) -> None:
        plan = self._load_plan(plan_id)
        plan.extend(self.clock, new_deadline, basis)
        self.repo.save(plan)

    def review_remediation(self, plan_id: str, passed: bool, basis: str) -> None:
        plan = self._load_plan(plan_id)
        plan.record_review(self.clock, passed, basis)
        self.repo.save(plan)

    def close_remediation(self, plan_id: str, basis: str, case_conclusion: str) -> None:
        plan = self._load_plan(plan_id)
        plan.close(self.clock, basis)
        case = self._load_case(plan.case_id)  # type: ignore[arg-type]
        case.close(self.clock, case_conclusion, basis)
        self.repo.save(plan)
        self.repo.save(case)

    def appeal_case(self, case_id: str, basis: str) -> None:
        case = self._load_case(case_id)
        case.appeal(self.clock, basis)
        self.repo.save(case)

    def withdraw_appeal(self, case_id: str, basis: str) -> None:
        case = self._load_case(case_id)
        restored = "remediating" if case.remediation_id else "assigned"
        case.withdraw_appeal(self.clock, basis, restored)
        self.repo.save(case)

    # -- 处罚溯源 --------------------------------------------------------------

    def issue_penalty(
        self,
        case_id: str,
        occurrence_id: str,
        promotion_version: int,
        regulation_clause: str,
        decision: str,
        basis: str,
        penalty_id: str | None = None,
    ) -> dict[str, Any]:
        case = self._load_case(case_id)
        occurrence = self.repo.load(ServiceOccurrence, occurrence_id)
        if occurrence.service is None:
            raise DomainError("处罚必须对应一次已发生的服务")
        service = occurrence.service
        if service.provider_id != case.provider_id:
            raise DomainError("服务事实与案件经营主体不一致")
        offer = self._load_offer(service.offer_id)
        promotion = offer.promotion_at(promotion_version)
        rendered_day = datetime.fromisoformat(service.rendered_at)
        provider = self._load_provider(case.provider_id)  # type: ignore[arg-type]
        license_view = self._license_effective_on(provider, rendered_day)
        intake_no = promotion.evidence_ids[0] if promotion.evidence_ids else None

        penalty_id = penalty_id or f"penalty-{self.clock.new_id()[:12]}"
        penalty = Penalty(penalty_id)
        penalty.issue(
            self.clock,
            case_id=case_id,
            provider_id=case.provider_id or "",
            promotion_ref={
                "offer_id": offer.id,
                "promotion_version": promotion.version,
                "fingerprint": promotion.fingerprint,
                "city": promotion.city,
                "evidence_intake_no": intake_no,
            },
            occurrence_ref={
                "occurrence_id": occurrence_id,
                "order_ref": service.order_ref,
                "component": service.component,
                "service_region": service.service_region,
                "practitioner_id": service.practitioner_id,
                "rendered_at": service.rendered_at,
            },
            license_snapshot={
                "license_id": license_view.license_id,
                "scope": license_view.scope,
                "valid_from": license_view.valid_from,
                "valid_until": license_view.valid_until,
                "verification_basis": license_view.basis,
                "effective_at": service.rendered_at,
            },
            regulation_clause=regulation_clause,
            decision=decision,
            basis=basis,
        )
        case.attach_penalty(self.clock, penalty_id)
        self.repo.save(penalty)
        self.repo.save(case)
        return penalty.explain().__dict__  # type: ignore[no-any-return]

    def explain_penalty(self, penalty_id: str) -> dict[str, Any]:
        penalty = self.repo.load(Penalty, penalty_id)
        return penalty.explain().__dict__

    # -- 提醒 ------------------------------------------------------------------

    def license_reminders(self, within_days: int = 30) -> list[LicenseDue]:
        return self.projections.reminders.license_reminders(self.clock.now(), within_days)

    def pending_reviews(self) -> list[PendingReview]:
        return self.projections.reminders.pending_reviews(self.clock.now())

    # -- 内部 ------------------------------------------------------------------

    def _enforce_provider_policy(
        self, provider_id: str, required_scopes: tuple[str, ...]
    ) -> list[str]:
        provider = self._load_provider(provider_id)
        if provider.order_blocked:
            return list(provider.block_reasons)
        reasons = provider.evaluate_block_reasons(
            self.clock.now(), required_scopes=required_scopes
        )
        if reasons:
            provider.block_new_orders(self.clock, reasons, basis="资质过期或缺失的系统限单判定")
            self.repo.save(provider)
        return reasons

    def _notify_open_cases(self, offer_id: str, version: int, source: str) -> None:
        for case_id in self.projections.case_watch.open_cases_for_offer(offer_id):
            case = self.repo.load(InspectionCase, case_id)
            case.notify_version_change(
                self.clock,
                offer_id,
                version,
                basis=f"{('宣传' if source == 'promotion' else '价格退款条款')}内容变更形成新版本",
                source=source,
            )
            self.repo.save(case)

    @staticmethod
    def _license_effective_on(provider: Provider, day: datetime) -> LicenseView:
        candidates = [
            lic
            for lic in provider.licenses.values()
            if datetime.fromisoformat(lic.valid_from) <= day <= datetime.fromisoformat(lic.valid_until)
        ]
        if not candidates:
            raise DomainError("服务发生时不存在有效资质，不能形成资质关联快照")
        return sorted(candidates, key=lambda lic: lic.license_id)[0]

    def _load_category(self, code: str) -> ServiceCategory:
        category = self.repo.try_load(ServiceCategory, code)
        if category is None or category.code is None:
            raise DomainError(f"服务类别 {code} 未定义")
        return category

    def _load_provider(self, provider_id: str) -> Provider:
        provider = self.repo.try_load(Provider, provider_id)
        if provider is None or provider.name is None:
            raise DomainError(f"经营主体 {provider_id} 未登记")
        return provider

    def _load_offer(self, offer_id: str) -> ServiceOffer:
        offer = self.repo.try_load(ServiceOffer, offer_id)
        if offer is None or offer.title is None:
            raise DomainError(f"服务项目 {offer_id} 未登记")
        return offer

    def _load_case(self, case_id: str) -> InspectionCase:
        case = self.repo.try_load(InspectionCase, case_id)
        if case is None or case.status is None:
            raise DomainError(f"案件 {case_id} 不存在")
        return case

    def _load_plan(self, plan_id: str) -> RemediationPlan:
        plan = self.repo.try_load(RemediationPlan, plan_id)
        if plan is None or plan.status is None:
            raise DomainError(f"整改计划 {plan_id} 不存在")
        return plan
