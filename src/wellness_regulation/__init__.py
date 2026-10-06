"""疗愈服务合规巡检库。

公开入口是 RegulationSystem；领域类型与判定函数可直接用于只读分析。
"""

from __future__ import annotations

from .catalog import (
    ADDON_NON_REFUNDABLE,
    ADDON_REFUND_WINDOW_SHORTER,
    KIND_GENERAL,
    KIND_MEDICAL,
    ClaimAnalysis,
    NegativeList,
    PricingAnalysis,
    ServiceCategory,
    TermHit,
    analyze_claim_text,
    analyze_pricing,
)
from .clock import Clock
from .engine import Aggregate, EventStore, StoredEvent
from .errors import (
    AccessDenied,
    ConcurrencyError,
    ContractViolation,
    DomainError,
    ImmutableFactError,
)
from .evidence import ConsumerAuthorization, EvidenceRecord
from .inspection import InspectionCase, Penalty, PenaltyExplanation, RemediationPlan
from .offer import PricingVersion, PromotionVersion, ServiceOffer
from .people import Practitioner, RenderedService, ServiceOccurrence
from .projections import LicenseDue, PendingReview, ProjectionSet
from .provider import LicenseView, Provider
from .repository import Repository
from .security import AccessPolicy, Inspector
from .system import RegulationSystem

__all__ = [
    "RegulationSystem",
    "Clock",
    "EventStore",
    "StoredEvent",
    "Aggregate",
    "Repository",
    "ProjectionSet",
    "Inspector",
    "AccessPolicy",
    # 目录与判定
    "ServiceCategory",
    "NegativeList",
    "ClaimAnalysis",
    "PricingAnalysis",
    "TermHit",
    "analyze_claim_text",
    "analyze_pricing",
    "KIND_GENERAL",
    "KIND_MEDICAL",
    "ADDON_NON_REFUNDABLE",
    "ADDON_REFUND_WINDOW_SHORTER",
    # 聚合
    "Provider",
    "LicenseView",
    "ServiceOffer",
    "PromotionVersion",
    "PricingVersion",
    "Practitioner",
    "ServiceOccurrence",
    "RenderedService",
    "ConsumerAuthorization",
    "EvidenceRecord",
    "InspectionCase",
    "RemediationPlan",
    "Penalty",
    "PenaltyExplanation",
    # 投影
    "LicenseDue",
    "PendingReview",
    # 错误
    "DomainError",
    "ContractViolation",
    "ConcurrencyError",
    "AccessDenied",
    "ImmutableFactError",
]
