"""疗愈服务合规巡检库的领域对象与枚举。

所有带时间的字段一律使用带时区的 datetime，与交换契约保持一致。
金额字段使用 Decimal，避免浮点误差进入处罚与退款依据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Boundary(str, Enum):
    """服务的医疗诊疗边界。"""

    MEDICAL = "MEDICAL"  # 医疗诊疗
    EXPERIENCE = "EXPERIENCE"  # 一般体验


class DeterminationResult(str, Enum):
    """边界判定结论。"""

    GENERAL_EXPERIENCE = "GENERAL_EXPERIENCE"  # 一般体验，未越界
    MEDICAL_TREATMENT = "MEDICAL_TREATMENT"  # 医疗诊疗且资质齐备
    VIOLATION = "VIOLATION"  # 越界宣传或触碰禁项


class LicenseStatus(str, Enum):
    VALID = "VALID"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class EntityStatus(str, Enum):
    NORMAL = "NORMAL"
    RESTRICTED = "RESTRICTED"  # 已限制新订单


class RestrictionReason(str, Enum):
    LICENSE_EXPIRED = "LICENSE_EXPIRED"  # 资质过期
    NEGATIVE_LIST_HIT = "NEGATIVE_LIST_HIT"  # 触碰负面清单


class Channel(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


class ComplaintStatus(str, Enum):
    RECEIVED = "RECEIVED"  # 已登记
    EVIDENCE_SEALED = "EVIDENCE_SEALED"  # 证据已封存
    CASE_OPENED = "CASE_OPENED"  # 已立案分派


class CaseStatus(str, Enum):
    ASSIGNED = "ASSIGNED"
    INVESTIGATING = "INVESTIGATING"
    REMEDIATION = "REMEDIATION"
    REVIEW = "REVIEW"
    CLOSED = "CLOSED"


#: 正在处理中的案件状态（宣传变更需要通知这些案件）
OPEN_CASE_STATUSES = frozenset(
    {CaseStatus.ASSIGNED, CaseStatus.INVESTIGATING, CaseStatus.REMEDIATION, CaseStatus.REVIEW}
)


class RemediationStatus(str, Enum):
    PENDING = "PENDING"
    EXTENDED = "EXTENDED"  # 已延期
    REVIEWED = "REVIEWED"  # 已有复核结论


class PenaltyStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"  # 申诉撤销


@dataclass
class BusinessEntity:
    """经营主体。"""

    entity_id: str
    name: str
    credit_code: str  # 统一社会信用代码
    registered_region: str  # 登记地
    operating_regions: tuple[str, ...]  # 实际经营地
    status: EntityStatus = EntityStatus.NORMAL


@dataclass
class License:
    """经营主体资质，含有效期。"""

    license_id: str
    entity_id: str
    license_type: str  # 营业执照 / 医疗机构执业许可证 等
    license_no: str
    valid_from: datetime
    valid_until: datetime
    status: LicenseStatus = LicenseStatus.VALID
    verified: bool = False


@dataclass
class ServiceCategory:
    """服务类别，标记医疗诊疗边界。"""

    category_id: str
    name: str
    boundary: Boundary


@dataclass
class NegativeListItem:
    """负面清单条目。

    kind 取 MEDICAL_CLAIM（医疗宣称用语）或 PROHIBITED_SERVICE（禁止项目）；
    constrains 为空表示对所有类别生效，否则只约束指定边界。
    """

    item_id: str
    kind: str
    keyword: str
    description: str
    constrains: Boundary | None = None


@dataclass(frozen=True)
class PriceComponent:
    """价格构成；refundable=False 即不可退款的附加消费。"""

    name: str
    amount: Decimal
    refundable: bool


@dataclass(frozen=True)
class RefundPolicy:
    """退款条款。"""

    refundable_within_days: int
    non_refundable_items: tuple[str, ...]
    terms: str


@dataclass
class ServiceOffer:
    """上架服务，含价格与退款条款。"""

    offer_id: str
    entity_id: str
    category_id: str
    title: str
    summary: str
    prices: tuple[PriceComponent, ...]
    refund_policy: RefundPolicy
    online: bool = True
    last_captured_at: datetime | None = None


@dataclass
class Practitioner:
    """从业人员及其证书有效期。"""

    practitioner_id: str
    entity_id: str
    name: str
    cert_type: str
    cert_no: str
    cert_valid_until: datetime


@dataclass
class PublicityVersion:
    """宣传内容版本；同一来源每次内容变更形成新版本，旧版本保留。"""

    version_id: str
    entity_id: str
    source_url: str
    content: str
    version_no: int
    captured_at: datetime
    offer_id: str | None = None
    supersedes: str | None = None  # 被替代的旧版本 version_id


@dataclass
class Determination:
    """医疗诊疗边界判定记录。"""

    determination_id: str
    target_type: str  # offer / publicity
    target_id: str
    result: DeterminationResult
    matched_item_ids: tuple[str, ...]  # 命中的负面清单条目
    basis: str  # 判定依据
    decided_by: str
    decided_at: datetime


@dataclass(frozen=True)
class ServiceRecord:
    """已经发生的服务记录，冻结且存入不可篡改表。"""

    record_id: str
    offer_id: str
    entity_id: str
    order_no: str
    consumer_name: str
    served_at: datetime
    amount: Decimal


@dataclass
class Evidence:
    """消费者授权证据；按（来源类型, 来源标识）去重，共享受理号。"""

    evidence_id: str
    acceptance_no: str  # 受理号
    source_type: str  # WEBPAGE / ORDER / CONTRACT / PAYMENT ...
    source_key: str  # 网页地址或订单号等来源标识
    payload: str
    consumer_authorization: str  # 消费者授权凭证
    captured_by: str
    captured_at: datetime
    sealed: bool = False
    sealed_at: datetime | None = None
    digest: str | None = None  # 封存时计算的内容摘要


@dataclass(frozen=True)
class ConsumerProfile:
    """消费者个人信息，按监管员职责范围脱敏。"""

    name: str
    phone: str
    id_number: str


@dataclass
class Complaint:
    """消费者投诉。"""

    complaint_id: str
    channel: Channel
    entity_id: str
    occurrence_region: str  # 发生地
    description: str
    consumer: ConsumerProfile
    evidence_ids: tuple[str, ...]
    status: ComplaintStatus
    filed_at: datetime


@dataclass
class Notification:
    """发给在办案件的通知（如宣传内容变更）。"""

    notification_id: str
    case_id: str
    message: str
    created_at: datetime


@dataclass
class InspectionCase:
    """巡查/投诉案件。"""

    case_id: str
    entity_id: str
    occurrence_region: str
    responsible_region: str
    coordinating_regions: tuple[str, ...]  # 跨区域时含登记地与发生地
    assigned_to: str
    status: CaseStatus
    opened_at: datetime
    complaint_id: str | None = None
    notifications: list[Notification] = field(default_factory=list)


@dataclass
class InspectionTask:
    """巡查任务。"""

    task_id: str
    entity_id: str
    region: str
    assigned_to: str
    due_at: datetime
    status: str = "SCHEDULED"
    findings: str | None = None
    finished_at: datetime | None = None


@dataclass
class RemediationExtension:
    """整改延期记录，必须留下决定依据。"""

    new_deadline: datetime
    rationale: str
    decided_by: str
    decided_at: datetime


@dataclass
class ReviewConclusion:
    """复核结论，必须留下决定依据。"""

    review_id: str
    plan_id: str
    passed: bool
    basis: str
    concluded_by: str
    concluded_at: datetime


@dataclass
class RemediationPlan:
    """整改要求与期限。"""

    plan_id: str
    case_id: str
    entity_id: str
    requirements: str
    deadline: datetime
    status: RemediationStatus = RemediationStatus.PENDING
    extensions: list[RemediationExtension] = field(default_factory=list)
    conclusion: ReviewConclusion | None = None


@dataclass
class OrderRestriction:
    """新订单限制；解除时只写 lifted_at，不删除历史。"""

    restriction_id: str
    entity_id: str
    reason: RestrictionReason
    detail: str
    imposed_at: datetime
    lifted_at: datetime | None = None


@dataclass
class Penalty:
    """处罚决定，关联宣传版本、服务记录与有效资质，可解释、可申诉。"""

    penalty_id: str
    case_id: str
    entity_id: str
    publicity_version_id: str
    service_record_id: str
    license_id: str
    rationale: str
    amount: Decimal
    status: PenaltyStatus
    decided_at: datetime
    revoked_rationale: str | None = None
    revoked_by: str | None = None
    revoked_at: datetime | None = None


@dataclass
class Regulator:
    """监管员，regions 为其职责区域。"""

    regulator_id: str
    name: str
    regions: tuple[str, ...]
