"""疗愈服务合规巡检库的领域服务门面。

所有写操作都通过 ComplianceSystem 完成：既维护业务不变量，又把业务事实
以契约事件的形式追加到事件日志，供交换层复核。核心规则：

- 医疗诊疗边界与一般体验分开判定；触碰禁项或资质过期立即限制新订单，
  但已发生的服务记录不可篡改。
- 同一网页或订单重复取证保持原受理号；宣传内容变更形成新版本并通知在办案件。
- 线上投诉先封存证据再分派；跨区域案件按经营主体登记地与发生地协同。
- 整改延期、复核结论、申诉撤销都必须留下决定依据。
- 处罚可解释到具体宣传版本、具体服务和当时有效的资质。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from . import access
from .errors import (
    DomainError,
    OrderRestrictedError,
    RationaleRequiredError,
    StateError,
)
from .models import (
    OPEN_CASE_STATUSES,
    Boundary,
    BusinessEntity,
    CaseStatus,
    Channel,
    Complaint,
    ComplaintStatus,
    ConsumerProfile,
    Determination,
    DeterminationResult,
    EntityStatus,
    Evidence,
    InspectionCase,
    InspectionTask,
    License,
    LicenseStatus,
    NegativeListItem,
    Notification,
    OrderRestriction,
    Penalty,
    PenaltyStatus,
    Practitioner,
    PriceComponent,
    PublicityVersion,
    RefundPolicy,
    Regulator,
    RemediationExtension,
    RemediationPlan,
    RemediationStatus,
    RestrictionReason,
    ReviewConclusion,
    ServiceCategory,
    ServiceOffer,
    ServiceRecord,
)
from .store import InMemoryStore

#: 医疗诊疗类服务必须持有的资质类型
MEDICAL_LICENSE_TYPE = "医疗机构执业许可证"


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainError(f"{field} 必须携带时区")


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"{field} 不能为空")
    return value.strip()


def _require_rationale(value: str, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RationaleRequiredError(message)
    return value.strip()


class ComplianceSystem:
    """疗愈服务合规巡检库的领域服务门面。"""

    def __init__(self, store: InMemoryStore | None = None) -> None:
        self.store = store or InMemoryStore()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _emit(
        self,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        summary: str,
        occurred_at: datetime,
        **extra: Any,
    ) -> dict[str, Any]:
        """按交换契约追加一条领域事件，版本按聚合递增。"""
        _require_aware(occurred_at, "occurred_at")
        event = {
            "event_id": self.store.next_id("EVT"),
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "occurred_at": occurred_at.isoformat(),
            "version": self.store.event_version(aggregate_type, aggregate_id) + 1,
            "summary": summary,
            **extra,
        }
        self.store.events.append(event)
        return event

    def _entity(self, entity_id: str) -> BusinessEntity:
        return self.store.get("entities", entity_id)

    # ------------------------------------------------------------------
    # 经营主体与资质
    # ------------------------------------------------------------------

    def register_entity(
        self,
        *,
        name: str,
        credit_code: str,
        registered_region: str,
        operating_regions: tuple[str, ...],
        now: datetime,
    ) -> BusinessEntity:
        _require_aware(now, "now")
        entity = BusinessEntity(
            entity_id=self.store.next_id("ENT"),
            name=_require_text(name, "name"),
            credit_code=_require_text(credit_code, "credit_code"),
            registered_region=_require_text(registered_region, "registered_region"),
            operating_regions=tuple(operating_regions),
        )
        self.store.put("entities", entity.entity_id, entity)
        self._emit(
            "business_entity", entity.entity_id, "ENTITY_REGISTERED",
            f"经营主体{entity.name}登记", now,
        )
        return entity

    def register_license(
        self,
        *,
        entity_id: str,
        license_type: str,
        license_no: str,
        valid_from: datetime,
        valid_until: datetime,
        now: datetime,
    ) -> License:
        self._entity(entity_id)
        _require_aware(valid_from, "valid_from")
        _require_aware(valid_until, "valid_until")
        if valid_from >= valid_until:
            raise DomainError("资质有效期起点必须早于终点")
        license_ = License(
            license_id=self.store.next_id("LIC"),
            entity_id=entity_id,
            license_type=_require_text(license_type, "license_type"),
            license_no=_require_text(license_no, "license_no"),
            valid_from=valid_from,
            valid_until=valid_until,
        )
        self.store.put("licenses", license_.license_id, license_)
        self._emit(
            "provider_license", license_.license_id, "LICENSE_REGISTERED",
            f"登记资质{license_.license_no}", now,
        )
        return license_

    def verify_license(self, *, license_id: str, now: datetime) -> License:
        license_ = self.store.get("licenses", license_id)
        license_.verified = True
        self._emit(
            "provider_license", license_.license_id, "LICENSE_VERIFIED",
            f"资质{license_.license_no}核验通过", now,
        )
        return license_

    def sweep_license_expiry(self, *, now: datetime) -> list[License]:
        """把已过期的资质登记为 EXPIRED，并立即限制对应主体的新订单。"""
        _require_aware(now, "now")
        expired = []
        for license_ in self.store.licenses.values():
            if license_.status is LicenseStatus.VALID and license_.valid_until < now:
                license_.status = LicenseStatus.EXPIRED
                expired.append(license_)
                self._emit(
                    "provider_license", license_.license_id, "LICENSE_EXPIRED",
                    f"资质{license_.license_no}已过期", now,
                )
                self.impose_restriction(
                    entity_id=license_.entity_id,
                    reason=RestrictionReason.LICENSE_EXPIRED,
                    detail=f"资质{license_.license_no}已于{license_.valid_until.isoformat()}过期",
                    now=now,
                )
        return expired

    def expiry_reminders(self, *, now: datetime, within_days: int = 30) -> list[License]:
        """仍在有效期内但即将到期的资质，按到期日升序。服务重启后照常可用。"""
        _require_aware(now, "now")
        horizon = now + timedelta(days=within_days)
        due = [
            license_
            for license_ in self.store.licenses.values()
            if license_.status is LicenseStatus.VALID and now <= license_.valid_until <= horizon
        ]
        return sorted(due, key=lambda license_: license_.valid_until)

    # ------------------------------------------------------------------
    # 服务类别与负面清单
    # ------------------------------------------------------------------

    def register_category(self, *, name: str, boundary: Boundary | str) -> ServiceCategory:
        category = ServiceCategory(
            category_id=self.store.next_id("CAT"),
            name=_require_text(name, "name"),
            boundary=Boundary(boundary),
        )
        self.store.put("categories", category.category_id, category)
        return category

    def register_negative_item(
        self,
        *,
        kind: str,
        keyword: str,
        description: str,
        constrains: Boundary | str | None = None,
    ) -> NegativeListItem:
        item = NegativeListItem(
            item_id=self.store.next_id("NEG"),
            kind=_require_text(kind, "kind"),
            keyword=_require_text(keyword, "keyword"),
            description=_require_text(description, "description"),
            constrains=Boundary(constrains) if constrains else None,
        )
        self.store.put("negative_items", item.item_id, item)
        return item

    # ------------------------------------------------------------------
    # 服务、价格与退款条款
    # ------------------------------------------------------------------

    def publish_offer(
        self,
        *,
        entity_id: str,
        category_id: str,
        title: str,
        summary: str,
        prices: tuple[PriceComponent, ...],
        refund_policy: RefundPolicy,
        now: datetime,
    ) -> ServiceOffer:
        self._entity(entity_id)
        self.store.get("categories", category_id)
        _require_aware(now, "now")
        if not prices:
            raise DomainError("服务必须登记价格构成")
        offer = ServiceOffer(
            offer_id=self.store.next_id("OFF"),
            entity_id=entity_id,
            category_id=category_id,
            title=_require_text(title, "title"),
            summary=_require_text(summary, "summary"),
            prices=tuple(prices),
            refund_policy=refund_policy,
            last_captured_at=now,
        )
        self.store.put("offers", offer.offer_id, offer)
        self._emit(
            "service_offer", offer.offer_id, "OFFER_PUBLISHED",
            f"上架服务{offer.title}", now,
        )
        self._emit(
            "service_offer", offer.offer_id, "OFFER_CAPTURED",
            f"留存服务{offer.title}的价格与退款条款", now,
            prices=[{"name": p.name, "amount": str(p.amount), "refundable": p.refundable} for p in offer.prices],
            refund_policy={
                "refundable_within_days": refund_policy.refundable_within_days,
                "non_refundable_items": list(refund_policy.non_refundable_items),
                "terms": refund_policy.terms,
            },
        )
        return offer

    def update_offer_terms(
        self,
        *,
        offer_id: str,
        prices: tuple[PriceComponent, ...],
        refund_policy: RefundPolicy,
        now: datetime,
    ) -> ServiceOffer:
        """更新价格与退款条款并重新留存快照；不影响已发生的服务。"""
        offer = self.store.get("offers", offer_id)
        _require_aware(now, "now")
        if not prices:
            raise DomainError("服务必须登记价格构成")
        offer.prices = tuple(prices)
        offer.refund_policy = refund_policy
        offer.last_captured_at = now
        self._emit(
            "service_offer", offer.offer_id, "OFFER_CAPTURED",
            f"更新服务{offer.title}的价格与退款条款", now,
            prices=[{"name": p.name, "amount": str(p.amount), "refundable": p.refundable} for p in offer.prices],
            refund_policy={
                "refundable_within_days": refund_policy.refundable_within_days,
                "non_refundable_items": list(refund_policy.non_refundable_items),
                "terms": refund_policy.terms,
            },
        )
        return offer

    # ------------------------------------------------------------------
    # 从业人员
    # ------------------------------------------------------------------

    def register_practitioner(
        self,
        *,
        entity_id: str,
        name: str,
        cert_type: str,
        cert_no: str,
        cert_valid_until: datetime,
        now: datetime,
    ) -> Practitioner:
        self._entity(entity_id)
        _require_aware(cert_valid_until, "cert_valid_until")
        practitioner = Practitioner(
            practitioner_id=self.store.next_id("PRA"),
            entity_id=entity_id,
            name=_require_text(name, "name"),
            cert_type=_require_text(cert_type, "cert_type"),
            cert_no=_require_text(cert_no, "cert_no"),
            cert_valid_until=cert_valid_until,
        )
        self.store.put("practitioners", practitioner.practitioner_id, practitioner)
        self._emit(
            "practitioner", practitioner.practitioner_id, "PRACTITIONER_REGISTERED",
            f"登记从业人员{practitioner.name}", now,
        )
        return practitioner

    def practitioner_cert_reminders(self, *, now: datetime, within_days: int = 30) -> list[Practitioner]:
        """证书即将到期的从业人员，按到期日升序。"""
        _require_aware(now, "now")
        horizon = now + timedelta(days=within_days)
        due = [
            p
            for p in self.store.practitioners.values()
            if now <= p.cert_valid_until <= horizon
        ]
        return sorted(due, key=lambda p: p.cert_valid_until)

    # ------------------------------------------------------------------
    # 宣传版本
    # ------------------------------------------------------------------

    def publish_publicity(
        self,
        *,
        entity_id: str,
        source_url: str,
        content: str,
        now: datetime,
        offer_id: str | None = None,
    ) -> PublicityVersion:
        """发布宣传内容。

        同一来源内容不变时不产生新版本；内容变更形成新版本并保留旧版本，
        同时通知该主体所有正在处理的案件。
        """
        entity = self._entity(entity_id)
        _require_aware(now, "now")
        source_url = _require_text(source_url, "source_url")
        content = _require_text(content, "content")
        if offer_id is not None:
            offer = self.store.get("offers", offer_id)
            if offer.entity_id != entity_id:
                raise DomainError("宣传内容与服务不属于同一经营主体")
        versions = [
            v
            for v in self.store.publicity_versions.values()
            if v.entity_id == entity_id and v.source_url == source_url
        ]
        latest = max(versions, key=lambda v: v.version_no, default=None)
        if latest and latest.content == content:
            return latest
        version = PublicityVersion(
            version_id=self.store.next_id("PUB"),
            entity_id=entity_id,
            offer_id=offer_id,
            source_url=source_url,
            content=content,
            version_no=(latest.version_no + 1) if latest else 1,
            supersedes=latest.version_id if latest else None,
            captured_at=now,
        )
        self.store.put("publicity_versions", version.version_id, version)
        self._emit(
            "publicity_version", version.version_id, "PUBLICITY_VERSIONED",
            f"{source_url} 宣传内容第{version.version_no}版", now,
        )
        if latest:
            self._notify_open_cases(
                entity,
                f"宣传来源{source_url}内容变更：第{latest.version_no}版→第{version.version_no}版，旧版本已留存",
                now,
            )
        return version

    def _notify_open_cases(self, entity: BusinessEntity, message: str, now: datetime) -> None:
        for case in self.store.cases.values():
            if case.entity_id == entity.entity_id and case.status in OPEN_CASE_STATUSES:
                note = Notification(
                    notification_id=self.store.next_id("NOT"),
                    case_id=case.case_id,
                    message=message,
                    created_at=now,
                )
                case.notifications.append(note)
                self._emit("inspection_case", case.case_id, "CASE_NOTIFIED", message, now)

    # ------------------------------------------------------------------
    # 医疗诊疗边界判定
    # ------------------------------------------------------------------

    def determine_offer_boundary(
        self, *, offer_id: str, now: datetime, decided_by: str = "system"
    ) -> Determination:
        """把医疗诊疗边界和一般体验分开判定。

        一般体验项目出现医疗宣称、任何项目触碰禁止条目、医疗项目缺少有效
        医疗资质，均判定为越界并立即限制经营主体的新订单。
        """
        offer = self.store.get("offers", offer_id)
        category = self.store.get("categories", offer.category_id)
        _require_aware(now, "now")
        texts = [offer.title, offer.summary]
        texts.extend(
            v.content for v in self.store.publicity_versions.values() if v.offer_id == offer_id
        )
        blob = "\n".join(texts)
        items = list(self.store.negative_items.values())
        medical_hits = [i for i in items if i.kind == "MEDICAL_CLAIM" and i.keyword in blob]
        prohibited_hits = [
            i
            for i in items
            if i.kind == "PROHIBITED_SERVICE"
            and i.keyword in blob
            and (i.constrains is None or i.constrains is category.boundary)
        ]
        matched = tuple(sorted({i.item_id for i in medical_hits + prohibited_hits}))
        if category.boundary is Boundary.EXPERIENCE and medical_hits:
            result = DeterminationResult.VIOLATION
            basis = "一般体验项目宣称医疗疗效：" + "、".join(i.keyword for i in medical_hits)
        elif prohibited_hits:
            result = DeterminationResult.VIOLATION
            basis = "触碰负面清单：" + "、".join(i.keyword for i in prohibited_hits)
        elif category.boundary is Boundary.MEDICAL:
            if self._has_valid_medical_license(offer.entity_id, now):
                result = DeterminationResult.MEDICAL_TREATMENT
                basis = "医疗诊疗项目且医疗资质有效"
            else:
                result = DeterminationResult.VIOLATION
                basis = "医疗诊疗项目缺少有效医疗资质"
        else:
            result = DeterminationResult.GENERAL_EXPERIENCE
            basis = "未发现医疗宣称或禁项"
        determination = Determination(
            determination_id=self.store.next_id("DET"),
            target_type="offer",
            target_id=offer_id,
            result=result,
            matched_item_ids=matched,
            basis=basis,
            decided_by=decided_by,
            decided_at=now,
        )
        self.store.put("determinations", determination.determination_id, determination)
        self._emit(
            "service_offer", offer_id, "BOUNDARY_DETERMINED",
            f"边界判定：{result.value}", now, basis=basis,
        )
        if result is DeterminationResult.VIOLATION:
            self.impose_restriction(
                entity_id=offer.entity_id,
                reason=RestrictionReason.NEGATIVE_LIST_HIT,
                detail=f"边界判定{determination.determination_id}：{basis}",
                now=now,
            )
        return determination

    def _has_valid_medical_license(self, entity_id: str, now: datetime) -> bool:
        return any(
            lic.entity_id == entity_id
            and lic.license_type == MEDICAL_LICENSE_TYPE
            and lic.status is LicenseStatus.VALID
            and lic.valid_from <= now <= lic.valid_until
            for lic in self.store.licenses.values()
        )

    # ------------------------------------------------------------------
    # 订单与已发生的服务
    # ------------------------------------------------------------------

    def place_order(
        self,
        *,
        offer_id: str,
        order_no: str,
        consumer_name: str,
        amount: str | Decimal,
        served_at: datetime,
    ) -> ServiceRecord:
        """生成已发生的服务记录。

        经营主体被限制期间拒绝新订单；记录一旦写入不可篡改表，
        之后的任何限制、整改都不会改写历史服务。
        """
        offer = self.store.get("offers", offer_id)
        _require_aware(served_at, "served_at")
        if self.active_restriction(offer.entity_id) is not None:
            raise OrderRestrictedError(f"经营主体{offer.entity_id}已被限制新订单")
        record = ServiceRecord(
            record_id=self.store.next_id("SRV"),
            offer_id=offer_id,
            entity_id=offer.entity_id,
            order_no=_require_text(order_no, "order_no"),
            consumer_name=_require_text(consumer_name, "consumer_name"),
            served_at=served_at,
            amount=Decimal(str(amount)),
        )
        self.store.put("service_records", record.record_id, record)
        return record

    def active_restriction(self, entity_id: str) -> OrderRestriction | None:
        for restriction in self.store.restrictions.values():
            if restriction.entity_id == entity_id and restriction.lifted_at is None:
                return restriction
        return None

    # ------------------------------------------------------------------
    # 消费者授权证据
    # ------------------------------------------------------------------

    def capture_evidence(
        self,
        *,
        source_type: str,
        source_key: str,
        payload: str,
        captured_by: str,
        consumer_authorization: str,
        now: datetime,
    ) -> Evidence:
        """取证并分配受理号。

        同一（来源类型, 来源标识）重复取证时返回原证据、保持原受理号，
        防止同一网页或订单被重复立案。
        """
        _require_aware(now, "now")
        source_type = _require_text(source_type, "source_type")
        source_key = _require_text(source_key, "source_key")
        _require_text(consumer_authorization, "consumer_authorization")
        existing_id = self.store.acceptance_lookup(source_type, source_key)
        if existing_id is not None:
            return self.store.get("evidences", existing_id)
        evidence = Evidence(
            evidence_id=self.store.next_id("EVI"),
            acceptance_no=self.store.next_acceptance_no(),
            source_type=source_type,
            source_key=source_key,
            payload=_require_text(payload, "payload"),
            consumer_authorization=consumer_authorization.strip(),
            captured_by=_require_text(captured_by, "captured_by"),
            captured_at=now,
        )
        self.store.put("evidences", evidence.evidence_id, evidence)
        self.store.acceptance_register(source_type, source_key, evidence.evidence_id)
        self._emit(
            "evidence", evidence.evidence_id, "EVIDENCE_CAPTURED",
            f"取证{source_type}:{source_key}，受理号{evidence.acceptance_no}", now,
        )
        return evidence

    def seal_evidence(self, *, evidence_id: str, now: datetime) -> Evidence:
        """封存证据：计算内容摘要后不再提供改写入口。重复封存幂等。"""
        evidence = self.store.get("evidences", evidence_id)
        _require_aware(now, "now")
        if evidence.sealed:
            return evidence
        evidence.sealed = True
        evidence.sealed_at = now
        evidence.digest = hashlib.sha256(evidence.payload.encode("utf-8")).hexdigest()
        self._emit(
            "evidence", evidence.evidence_id, "EVIDENCE_SEALED",
            f"证据{evidence.acceptance_no}已封存", now,
        )
        return evidence

    # ------------------------------------------------------------------
    # 投诉与案件
    # ------------------------------------------------------------------

    def file_complaint(
        self,
        *,
        channel: Channel | str,
        entity_id: str,
        occurrence_region: str,
        description: str,
        consumer_name: str,
        consumer_phone: str,
        consumer_id_number: str,
        evidence_ids: tuple[str, ...] = (),
        now: datetime,
    ) -> Complaint:
        self._entity(entity_id)
        _require_aware(now, "now")
        for evidence_id in evidence_ids:
            self.store.get("evidences", evidence_id)
        complaint = Complaint(
            complaint_id=self.store.next_id("CMP"),
            channel=Channel(channel),
            entity_id=entity_id,
            occurrence_region=_require_text(occurrence_region, "occurrence_region"),
            description=_require_text(description, "description"),
            consumer=ConsumerProfile(
                name=_require_text(consumer_name, "consumer_name"),
                phone=_require_text(consumer_phone, "consumer_phone"),
                id_number=_require_text(consumer_id_number, "consumer_id_number"),
            ),
            evidence_ids=tuple(evidence_ids),
            status=ComplaintStatus.RECEIVED,
            filed_at=now,
        )
        self.store.put("complaints", complaint.complaint_id, complaint)
        self._emit(
            "complaint", complaint.complaint_id, "COMPLAINT_FILED",
            f"受理投诉{complaint.complaint_id}", now,
        )
        return complaint

    def seal_complaint_evidence(self, *, complaint_id: str, now: datetime) -> Complaint:
        """封存投诉关联的全部证据；线上投诉分派前必须完成这一步。"""
        complaint = self.store.get("complaints", complaint_id)
        if complaint.status is ComplaintStatus.CASE_OPENED:
            raise StateError("投诉已立案，不能再封存")
        for evidence_id in complaint.evidence_ids:
            self.seal_evidence(evidence_id=evidence_id, now=now)
        complaint.status = ComplaintStatus.EVIDENCE_SEALED
        return complaint

    def open_case(self, *, complaint_id: str, assigned_to: str, now: datetime) -> InspectionCase:
        """投诉立案分派。线上投诉须先封存证据；跨区域案件自动协同。"""
        complaint = self.store.get("complaints", complaint_id)
        if complaint.status is ComplaintStatus.CASE_OPENED:
            raise StateError("投诉已立案")
        if complaint.channel is Channel.ONLINE and complaint.status is not ComplaintStatus.EVIDENCE_SEALED:
            raise StateError("线上投诉须先封存证据再分派")
        entity = self._entity(complaint.entity_id)
        return self._open_case(
            entity=entity,
            occurrence_region=complaint.occurrence_region,
            assigned_to=assigned_to,
            now=now,
            complaint=complaint,
        )

    def open_case_from_inspection(
        self, *, task_id: str, assigned_to: str, now: datetime
    ) -> InspectionCase:
        """巡查发现的案件直接立案。"""
        task = self.store.get("inspection_tasks", task_id)
        entity = self._entity(task.entity_id)
        return self._open_case(
            entity=entity,
            occurrence_region=task.region,
            assigned_to=assigned_to,
            now=now,
            complaint=None,
        )

    def _open_case(
        self,
        *,
        entity: BusinessEntity,
        occurrence_region: str,
        assigned_to: str,
        now: datetime,
        complaint: Complaint | None,
    ) -> InspectionCase:
        regions = [occurrence_region]
        if entity.registered_region != occurrence_region:
            regions.append(entity.registered_region)
        case = InspectionCase(
            case_id=self.store.next_id("CAS"),
            complaint_id=complaint.complaint_id if complaint else None,
            entity_id=entity.entity_id,
            occurrence_region=occurrence_region,
            responsible_region=occurrence_region,
            coordinating_regions=tuple(regions),
            assigned_to=_require_text(assigned_to, "assigned_to"),
            status=CaseStatus.ASSIGNED,
            opened_at=now,
        )
        self.store.put("cases", case.case_id, case)
        if complaint is not None:
            complaint.status = ComplaintStatus.CASE_OPENED
        self._emit(
            "inspection_case", case.case_id, "CASE_ASSIGNED",
            f"案件{case.case_id}分派给{case.assigned_to}", now,
        )
        if len(regions) > 1:
            self._emit(
                "inspection_case", case.case_id, "CASE_COORDINATED",
                f"跨区域案件：发生地{occurrence_region}与登记地{entity.registered_region}协同",
                now,
            )
        return case

    def register_regulator(self, *, name: str, regions: tuple[str, ...]) -> Regulator:
        regulator = Regulator(
            regulator_id=self.store.next_id("REG"),
            name=_require_text(name, "name"),
            regions=tuple(regions),
        )
        self.store.put("regulators", regulator.regulator_id, regulator)
        return regulator

    def view_case(self, *, regulator_id: str, case_id: str) -> dict[str, Any]:
        """案件视图；消费者个人信息仅在监管员职责范围内可见。"""
        regulator = self.store.get("regulators", regulator_id)
        case = self.store.get("cases", case_id)
        view: dict[str, Any] = {
            "case_id": case.case_id,
            "entity_id": case.entity_id,
            "status": case.status.value,
            "responsible_region": case.responsible_region,
            "coordinating_regions": list(case.coordinating_regions),
            "assigned_to": case.assigned_to,
            "notifications": [n.message for n in case.notifications],
        }
        if case.complaint_id is not None:
            complaint = self.store.get("complaints", case.complaint_id)
            view["complaint"] = {
                "complaint_id": complaint.complaint_id,
                "channel": complaint.channel.value,
                "description": complaint.description,
                "consumer": access.consumer_view(regulator, case, complaint.consumer),
            }
        return view

    # ------------------------------------------------------------------
    # 巡查任务
    # ------------------------------------------------------------------

    def schedule_inspection(
        self,
        *,
        entity_id: str,
        region: str,
        assigned_to: str,
        due_at: datetime,
        now: datetime,
    ) -> InspectionTask:
        self._entity(entity_id)
        _require_aware(due_at, "due_at")
        task = InspectionTask(
            task_id=self.store.next_id("TSK"),
            entity_id=entity_id,
            region=_require_text(region, "region"),
            assigned_to=_require_text(assigned_to, "assigned_to"),
            due_at=due_at,
        )
        self.store.put("inspection_tasks", task.task_id, task)
        self._emit(
            "inspection_task", task.task_id, "INSPECTION_SCHEDULED",
            f"安排巡查任务{task.task_id}", now,
        )
        return task

    def record_inspection(self, *, task_id: str, findings: str, now: datetime) -> InspectionTask:
        task = self.store.get("inspection_tasks", task_id)
        _require_aware(now, "now")
        task.status = "DONE"
        task.findings = _require_text(findings, "findings")
        task.finished_at = now
        self._emit(
            "inspection_task", task.task_id, "INSPECTION_RECORDED",
            f"巡查任务{task.task_id}完成", now,
        )
        return task

    # ------------------------------------------------------------------
    # 整改与复核
    # ------------------------------------------------------------------

    def order_remediation(
        self,
        *,
        case_id: str,
        requirements: str,
        deadline: datetime,
        now: datetime,
    ) -> RemediationPlan:
        case = self.store.get("cases", case_id)
        if case.status is CaseStatus.CLOSED:
            raise StateError("案件已关闭，不能再下达整改")
        _require_aware(deadline, "deadline")
        plan = RemediationPlan(
            plan_id=self.store.next_id("RPL"),
            case_id=case_id,
            entity_id=case.entity_id,
            requirements=_require_text(requirements, "requirements"),
            deadline=deadline,
        )
        self.store.put("remediation_plans", plan.plan_id, plan)
        case.status = CaseStatus.REMEDIATION
        self._emit(
            "remediation_plan", plan.plan_id, "REMEDIATION_ORDERED",
            f"责令整改，期限{deadline.isoformat()}", now,
        )
        return plan

    def extend_remediation(
        self,
        *,
        plan_id: str,
        new_deadline: datetime,
        rationale: str,
        decided_by: str,
        now: datetime,
    ) -> RemediationPlan:
        """整改延期，必须留下决定依据。"""
        plan = self.store.get("remediation_plans", plan_id)
        if plan.conclusion is not None:
            raise StateError("已有复核结论，不得再延期")
        _require_aware(new_deadline, "new_deadline")
        rationale = _require_rationale(rationale, "整改延期必须留下决定依据")
        plan.extensions.append(
            RemediationExtension(
                new_deadline=new_deadline,
                rationale=rationale,
                decided_by=_require_text(decided_by, "decided_by"),
                decided_at=now,
            )
        )
        plan.deadline = new_deadline
        plan.status = RemediationStatus.EXTENDED
        self._emit(
            "remediation_plan", plan.plan_id, "REMEDIATION_EXTENDED",
            f"整改延期至{new_deadline.isoformat()}：{rationale}", now,
        )
        return plan

    def conclude_review(
        self,
        *,
        plan_id: str,
        passed: bool,
        basis: str,
        concluded_by: str,
        now: datetime,
    ) -> ReviewConclusion:
        """复核结论，必须留下决定依据；通过则关闭案件。"""
        plan = self.store.get("remediation_plans", plan_id)
        if plan.conclusion is not None:
            raise StateError("复核已有结论")
        basis = _require_rationale(basis, "复核结论必须留下决定依据")
        conclusion = ReviewConclusion(
            review_id=self.store.next_id("REV"),
            plan_id=plan_id,
            passed=bool(passed),
            basis=basis,
            concluded_by=_require_text(concluded_by, "concluded_by"),
            concluded_at=now,
        )
        plan.conclusion = conclusion
        plan.status = RemediationStatus.REVIEWED
        case = self.store.get("cases", plan.case_id)
        case.status = CaseStatus.CLOSED if conclusion.passed else CaseStatus.REMEDIATION
        self._emit(
            "remediation_plan", plan.plan_id, "REVIEW_CLOSED",
            f"复核{'通过' if conclusion.passed else '未通过'}：{basis}", now,
        )
        return conclusion

    def pending_reviews(self) -> list[RemediationPlan]:
        """尚未复核的整改计划；服务重启后依然保留。"""
        return [p for p in self.store.remediation_plans.values() if p.conclusion is None]

    # ------------------------------------------------------------------
    # 限制与重启
    # ------------------------------------------------------------------

    def impose_restriction(
        self,
        *,
        entity_id: str,
        reason: RestrictionReason,
        detail: str,
        now: datetime,
    ) -> OrderRestriction:
        """立即限制经营主体的新订单；同原因的限制不重复登记。"""
        entity = self._entity(entity_id)
        _require_aware(now, "now")
        for restriction in self.store.restrictions.values():
            if (
                restriction.entity_id == entity_id
                and restriction.reason is reason
                and restriction.lifted_at is None
            ):
                return restriction
        restriction = OrderRestriction(
            restriction_id=self.store.next_id("RST"),
            entity_id=entity_id,
            reason=reason,
            detail=_require_text(detail, "detail"),
            imposed_at=now,
        )
        self.store.put("restrictions", restriction.restriction_id, restriction)
        entity.status = EntityStatus.RESTRICTED
        self._emit(
            "business_entity", entity_id, "RESTRICTION_IMPOSED",
            f"限制新订单：{restriction.detail}", now,
        )
        return restriction

    def restart_service(self, *, entity_id: str, now: datetime) -> BusinessEntity:
        """解除新订单限制、恢复经营。

        只解除限制，不改写资质状态、到期提醒与未完成复核——这些在重启后
        照常继续。
        """
        entity = self._entity(entity_id)
        _require_aware(now, "now")
        active = [
            r
            for r in self.store.restrictions.values()
            if r.entity_id == entity_id and r.lifted_at is None
        ]
        if not active:
            raise StateError("经营主体当前没有生效中的限制")
        for restriction in active:
            restriction.lifted_at = now
        entity.status = EntityStatus.NORMAL
        self._emit(
            "business_entity", entity_id, "SERVICE_RESTARTED",
            f"经营主体{entity.name}恢复经营", now,
        )
        return entity

    # ------------------------------------------------------------------
    # 处罚与申诉
    # ------------------------------------------------------------------

    def issue_penalty(
        self,
        *,
        case_id: str,
        publicity_version_id: str,
        service_record_id: str,
        license_id: str,
        rationale: str,
        amount: str | Decimal,
        now: datetime,
    ) -> Penalty:
        """开立处罚。

        宣传版本、服务记录、资质必须属于案件同一经营主体，且宣传版本与
        资质在服务发生时有效——保证处罚始终能解释到具体的宣传、具体的
        服务和当时有效的资质。
        """
        case = self.store.get("cases", case_id)
        version = self.store.get("publicity_versions", publicity_version_id)
        record = self.store.get("service_records", service_record_id)
        license_ = self.store.get("licenses", license_id)
        rationale = _require_rationale(rationale, "处罚必须留下决定依据")
        _require_aware(now, "now")
        if not (
            version.entity_id == record.entity_id == license_.entity_id == case.entity_id
        ):
            raise DomainError("处罚要素不属于案件同一经营主体")
        if not (license_.valid_from <= record.served_at <= license_.valid_until):
            raise DomainError("资质在服务发生时不在有效期内")
        self._require_publicity_effective(version, record.served_at)
        penalty = Penalty(
            penalty_id=self.store.next_id("PEN"),
            case_id=case_id,
            entity_id=case.entity_id,
            publicity_version_id=publicity_version_id,
            service_record_id=service_record_id,
            license_id=license_id,
            rationale=rationale,
            amount=Decimal(str(amount)),
            status=PenaltyStatus.ACTIVE,
            decided_at=now,
        )
        self.store.put("penalties", penalty.penalty_id, penalty)
        self._emit(
            "penalty", penalty.penalty_id, "PENALTY_ISSUED",
            f"处罚{penalty.penalty_id}：{rationale}", now,
        )
        return penalty

    def _require_publicity_effective(
        self, version: PublicityVersion, served_at: datetime
    ) -> None:
        if version.captured_at > served_at:
            raise DomainError("该宣传版本在服务发生时尚未发布")
        successors = [
            v
            for v in self.store.publicity_versions.values()
            if v.entity_id == version.entity_id
            and v.source_url == version.source_url
            and v.version_no == version.version_no + 1
        ]
        if successors and successors[0].captured_at <= served_at:
            raise DomainError("该宣传版本在服务发生时已被新版本取代")

    def explain_penalty(self, *, penalty_id: str) -> dict[str, Any]:
        """解释一项处罚对应哪条宣传、哪次服务和哪份有效资质。"""
        penalty = self.store.get("penalties", penalty_id)
        version = self.store.get("publicity_versions", penalty.publicity_version_id)
        record = self.store.get("service_records", penalty.service_record_id)
        license_ = self.store.get("licenses", penalty.license_id)
        return {
            "penalty_id": penalty.penalty_id,
            "case_id": penalty.case_id,
            "entity_id": penalty.entity_id,
            "status": penalty.status.value,
            "rationale": penalty.rationale,
            "amount": str(penalty.amount),
            "publicity": {
                "version_id": version.version_id,
                "source_url": version.source_url,
                "version_no": version.version_no,
                "content": version.content,
                "captured_at": version.captured_at.isoformat(),
            },
            "service": {
                "record_id": record.record_id,
                "order_no": record.order_no,
                "served_at": record.served_at.isoformat(),
                "amount": str(record.amount),
            },
            "license": {
                "license_id": license_.license_id,
                "license_type": license_.license_type,
                "license_no": license_.license_no,
                "valid_from": license_.valid_from.isoformat(),
                "valid_until": license_.valid_until.isoformat(),
            },
        }

    def revoke_penalty(
        self,
        *,
        penalty_id: str,
        rationale: str,
        decided_by: str,
        now: datetime,
    ) -> Penalty:
        """申诉撤销处罚，必须留下决定依据。"""
        penalty = self.store.get("penalties", penalty_id)
        if penalty.status is PenaltyStatus.REVOKED:
            raise StateError("处罚已撤销")
        rationale = _require_rationale(rationale, "申诉撤销必须留下决定依据")
        _require_aware(now, "now")
        penalty.status = PenaltyStatus.REVOKED
        penalty.revoked_rationale = rationale
        penalty.revoked_by = _require_text(decided_by, "decided_by")
        penalty.revoked_at = now
        self._emit(
            "penalty", penalty.penalty_id, "APPEAL_REVOKED",
            f"处罚{penalty.penalty_id}经申诉撤销：{rationale}", now,
        )
        return penalty
