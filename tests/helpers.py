"""测试共享的 fixtures：构造基础经营主体、目录、服务与案件。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wellness_regulation.models import Boundary, PriceComponent, RefundPolicy  # noqa: E402
from wellness_regulation.system import ComplianceSystem  # noqa: E402

CN = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=CN)


def new_system() -> ComplianceSystem:
    return ComplianceSystem()


def base_entity(system: ComplianceSystem, region: str = "杭州市"):
    return system.register_entity(
        name="澄心音声疗愈馆",
        credit_code="91330100MA27XU8A3B",
        registered_region=region,
        operating_regions=(region,),
        now=NOW,
    )


def base_category(system: ComplianceSystem, name: str = "音疗体验", boundary=Boundary.EXPERIENCE):
    return system.register_category(name=name, boundary=boundary)


def base_offer(
    system: ComplianceSystem,
    entity,
    category,
    title: str = "颂钵音疗体验课",
    summary: str = "放松身心的一般体验项目",
):
    prices = (
        PriceComponent("体验课", Decimal("199.00"), True),
        PriceComponent("能量套餐附加服务", Decimal("80.00"), False),
    )
    policy = RefundPolicy(
        refundable_within_days=7,
        non_refundable_items=("能量套餐附加服务",),
        terms="开课后附加消费不退",
    )
    return system.publish_offer(
        entity_id=entity.entity_id,
        category_id=category.category_id,
        title=title,
        summary=summary,
        prices=prices,
        refund_policy=policy,
        now=NOW,
    )


def base_license(
    system: ComplianceSystem,
    entity,
    license_type: str = "营业执照",
    valid_from: datetime = datetime(2026, 1, 1, tzinfo=CN),
    valid_until: datetime = datetime(2027, 1, 1, tzinfo=CN),
):
    return system.register_license(
        entity_id=entity.entity_id,
        license_type=license_type,
        license_no="LICENSE-001",
        valid_from=valid_from,
        valid_until=valid_until,
        now=NOW,
    )


def open_case_shortcut(system: ComplianceSystem, entity, region: str | None = None):
    """线下投诉立案的快捷路径（线上流程在 test_evidence 中单独覆盖）。"""
    complaint = system.file_complaint(
        channel="OFFLINE",
        entity_id=entity.entity_id,
        occurrence_region=region or entity.registered_region,
        description="消费者反映宣传与退款问题",
        consumer_name="王女士",
        consumer_phone="13800000000",
        consumer_id_number="330102199001011234",
        evidence_ids=(),
        now=NOW,
    )
    return system.open_case(
        complaint_id=complaint.complaint_id, assigned_to="监管员甲", now=NOW
    )


def penalty_chain(system: ComplianceSystem) -> SimpleNamespace:
    """构造一条完整的处罚链路：主体-资质-服务-宣传-订单-案件-处罚。"""
    entity = base_entity(system)
    category = base_category(system)
    offer = base_offer(system, entity, category)
    license_ = base_license(system, entity)
    published_at = datetime(2026, 3, 1, 10, 0, tzinfo=CN)
    version = system.publish_publicity(
        entity_id=entity.entity_id,
        source_url="https://example.com/promo",
        content="颂钵音疗体验课 199元",
        offer_id=offer.offer_id,
        now=published_at,
    )
    record = system.place_order(
        offer_id=offer.offer_id,
        order_no="ORDER-001",
        consumer_name="王女士",
        amount="199.00",
        served_at=datetime(2026, 3, 5, 15, 0, tzinfo=CN),
    )
    case = open_case_shortcut(system, entity)
    penalty = system.issue_penalty(
        case_id=case.case_id,
        publicity_version_id=version.version_id,
        service_record_id=record.record_id,
        license_id=license_.license_id,
        rationale="套餐拆分为不可退款的附加消费",
        amount="5000.00",
        now=datetime(2026, 4, 1, 9, 0, tzinfo=CN),
    )
    return SimpleNamespace(
        entity=entity,
        category=category,
        offer=offer,
        license=license_,
        publicity=version,
        record=record,
        case=case,
        penalty=penalty,
    )
