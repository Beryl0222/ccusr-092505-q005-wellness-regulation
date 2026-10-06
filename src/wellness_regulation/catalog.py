"""服务类别、负面清单与医疗诊疗边界判定。

判定只产出"事实 + 命中项 + 依据"，是否限单、是否处罚由监管流程决定，
边界分析函数是纯函数，可在巡检、受理、复核各环节复用且结论可复核。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .clock import Clock
from .engine import Aggregate, StoredEvent

# 一般体验项目一旦出现这些表述，即被判定为越界宣称医疗诊疗。
DEFAULT_MEDICAL_TERMS: tuple[str, ...] = (
    "治疗",
    "治愈",
    "疗效",
    "诊疗",
    "确诊",
    "处方",
    "医治",
    "根治",
    "包治",
    "去病根",
    "替代医疗",
    "临床治愈",
)

KIND_GENERAL = "general_experience"      # 一般体验（音声、禅修、线上课程等）
KIND_MEDICAL = "medical_diagnosis"       # 医疗诊疗活动，须持医疗机构/执业资质


@dataclass(frozen=True)
class TermHit:
    term: str
    kind: str  # medical_claim | prohibited


@dataclass(frozen=True)
class ClaimAnalysis:
    text: str
    hits: tuple[TermHit, ...]

    @property
    def crosses_medical_boundary(self) -> bool:
        return any(h.kind == "medical_claim" for h in self.hits)

    @property
    def hits_negative_list(self) -> bool:
        return any(h.kind == "prohibited" for h in self.hits)

    @property
    def compliant(self) -> bool:
        return not self.hits


def analyze_claim_text(
    text: str,
    prohibited_terms: tuple[str, ...] = (),
    medical_terms: tuple[str, ...] = DEFAULT_MEDICAL_TERMS,
) -> ClaimAnalysis:
    """在一段宣传文案中定位越界词；同一词不重复计数。"""
    hits: list[TermHit] = []
    for term in medical_terms:
        if term and term in text:
            hits.append(TermHit(term, "medical_claim"))
    for term in prohibited_terms:
        if term and term in text and term not in medical_terms:
            hits.append(TermHit(term, "prohibited"))
    return ClaimAnalysis(text=text, hits=tuple(sorted(hits, key=lambda h: (h.kind, h.term))))


@dataclass
class NegativeList:
    prohibited_terms: tuple[str, ...] = ()
    prohibited_practices: tuple[str, ...] = ()
    basis: str = ""
    updated_at: str | None = None
    version: int = 0


class ServiceCategory(Aggregate):
    """服务类别（音声/禅修/线上课程/医疗诊疗等）及其负面清单。"""

    aggregate_type = "service_category"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.code: str | None = None
        self.name: str | None = None
        self.kind: str | None = None
        self.description: str = ""
        self.negative_list = NegativeList()

    def define(
        self,
        clock: Clock,
        code: str,
        name: str,
        kind: str,
        description: str = "",
    ) -> None:
        if kind not in (KIND_GENERAL, KIND_MEDICAL):
            raise ValueError(f"未知服务类别性质：{kind}")
        self._record(
            "SERVICE_CATEGORY_DEFINED",
            f"定义服务类别 {name}",
            clock.now,
            clock.new_id,
            code=code,
            name=name,
            kind=kind,
            description=description,
        )

    def update_negative_list(
        self,
        clock: Clock,
        prohibited_terms: list[str],
        prohibited_practices: list[str],
        basis: str,
    ) -> None:
        if not basis.strip():
            raise ValueError("负面清单调整必须注明依据")
        self._record(
            "NEGATIVE_LIST_UPDATED",
            f"更新 {self.code} 负面清单（v{self.version + 1}）",
            clock.now,
            clock.new_id,
            prohibited_terms=list(prohibited_terms),
            prohibited_practices=list(prohibited_practices),
            basis=basis,
        )

    def apply_SERVICE_CATEGORY_DEFINED(self, event: StoredEvent) -> None:
        self.code = event.get("code")
        self.name = event.get("name")
        self.kind = event.get("kind")
        self.description = event.get("description", "")

    def apply_NEGATIVE_LIST_UPDATED(self, event: StoredEvent) -> None:
        self.negative_list = NegativeList(
            prohibited_terms=tuple(event.get("prohibited_terms", [])),
            prohibited_practices=tuple(event.get("prohibited_practices", [])),
            basis=event.get("basis", ""),
            updated_at=event.occurred_at,
            version=event.version,
        )

    def analyze(self, text: str) -> ClaimAnalysis:
        # 医疗诊疗类别的固有诊疗用语不构成越界；仅一般体验类别才检测越界宣称。
        medical_terms = () if self.kind == KIND_MEDICAL else DEFAULT_MEDICAL_TERMS
        return analyze_claim_text(
            text,
            prohibited_terms=self.negative_list.prohibited_terms,
            medical_terms=medical_terms,
        )


# -- 价格与退款条款 ---------------------------------------------------------

ADDON_NON_REFUNDABLE = "ADDON_NON_REFUNDABLE"
ADDON_REFUND_WINDOW_SHORTER = "ADDON_REFUND_WINDOW_SHORTER"


@dataclass(frozen=True)
class PricingFinding:
    code: str
    item: str
    detail: str


@dataclass(frozen=True)
class PricingAnalysis:
    findings: tuple[PricingFinding, ...]

    @property
    def has_refund_trap(self) -> bool:
        return bool(self.findings)


def analyze_pricing(terms: dict[str, Any]) -> PricingAnalysis:
    """识别套餐拆分的附加消费退款陷阱。

    规则：附加项（addon）不得概不退款；其退款窗口不得短于所属套餐。
    """
    items = terms.get("items", [])
    package_window: int | None = None
    for item in items:
        if item.get("component") == "package":
            package_window = item.get("refund_window_days")
            break
    findings: list[PricingFinding] = []
    for item in items:
        if item.get("component") != "addon":
            continue
        name = item.get("name", "未命名附加项")
        if item.get("refundable") is False:
            findings.append(
                PricingFinding(ADDON_NON_REFUNDABLE, name, "套餐拆分的附加消费设置为概不退款")
            )
        window = item.get("refund_window_days")
        if (
            package_window is not None
            and isinstance(window, int)
            and window < package_window
        ):
            findings.append(
                PricingFinding(
                    ADDON_REFUND_WINDOW_SHORTER,
                    name,
                    f"附加项退款窗口 {window} 天短于套餐 {package_window} 天",
                )
            )
    return PricingAnalysis(tuple(findings))
