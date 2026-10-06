"""事件投影：从全量事件重建的只读索引，重启后重放即可恢复。

- IntakeIndex：网页/订单指纹 -> 首个受理号，重复取证保持原受理号。
- CaseWatch：服务项目 -> 在办案件，宣传新版本据此通知。
- ReminderBoard：资质到期提醒 + 未完成/逾期复核，服务重启后继续生效。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .engine import StoredEvent


class IntakeIndex:
    def __init__(self) -> None:
        # 受理号按来源（网页 URL/订单号）稳定；source_key = kind:source_ref
        self._source_to_intake: dict[str, str] = {}
        # 受理号 -> 最新指纹，供处罚解释等场景反查
        self._latest_fingerprint: dict[str, str] = {}

    def handle(self, event: StoredEvent) -> None:
        if event.event_type == "EVIDENCE_SEALED":
            key = f"{event.get('kind')}:{event.get('source_ref')}"
            intake_no = event.get("intake_no")
            self._source_to_intake.setdefault(key, intake_no)
            self._latest_fingerprint[intake_no] = event.get("fingerprint")

    def intake_for_source(self, kind: str, source_ref: str) -> str | None:
        return self._source_to_intake.get(f"{kind}:{source_ref}")

    def latest_fingerprint(self, intake_no: str) -> str | None:
        return self._latest_fingerprint.get(intake_no)


class CaseWatch:
    def __init__(self) -> None:
        self._offer_cases: dict[str, set[str]] = {}
        self._case_offer: dict[str, str] = {}
        self._closed: set[str] = set()

    def handle(self, event: StoredEvent) -> None:
        if event.event_type == "COMPLAINT_FILED":
            offer_id = event.get("offer_id")
            self._offer_cases.setdefault(offer_id, set()).add(event.aggregate_id)
            self._case_offer[event.aggregate_id] = offer_id
        elif (
            event.event_type == "REVIEW_CLOSED"
            and event.aggregate_type == "inspection_case"
        ):
            self._closed.add(event.aggregate_id)
        elif event.event_type == "CASE_WITHDRAWN":
            # 申诉撤销不是案件撤销，案件仍在办，不移除监视
            return

    def open_cases_for_offer(self, offer_id: str) -> list[str]:
        return sorted(
            case_id
            for case_id in self._offer_cases.get(offer_id, set())
            if case_id not in self._closed
        )


@dataclass(frozen=True)
class LicenseDue:
    provider_id: str
    license_id: str
    scope: str
    valid_until: str
    days_left: int
    blocked: bool


@dataclass(frozen=True)
class PendingReview:
    plan_id: str
    case_id: str
    provider_id: str
    deadline: str
    overdue: bool
    status: str


class ReminderBoard:
    """到期提醒与未完成复核。限单/恢复不影响提醒，恢复后继续到期提醒。"""

    def __init__(self) -> None:
        self._providers: dict[str, dict[str, object]] = {}
        self._blocked: dict[str, bool] = {}
        self._plans: dict[str, dict[str, object]] = {}

    def handle(self, event: StoredEvent) -> None:
        et = event.event_type
        if et == "PROVIDER_REGISTERED":
            self._providers.setdefault(event.aggregate_id, {})
        elif et == "LICENSE_VERIFIED":
            provider = self._providers.setdefault(event.aggregate_id, {})
            licenses = provider.setdefault("licenses", {})  # type: ignore[union-attr]
            licenses[event.get("license_id")] = {  # type: ignore[index]
                "scope": event.get("scope"),
                "valid_until": event.get("valid_until"),
            }
        elif et == "LICENSE_RENEWED":
            provider = self._providers.setdefault(event.aggregate_id, {})
            licenses = provider.setdefault("licenses", {})  # type: ignore[union-attr]
            if event.get("license_id") in licenses:  # type: ignore[operator]
                licenses[event.get("license_id")]["valid_until"] = event.get(  # type: ignore[index]
                    "new_valid_until"
                )
        elif et == "PROVIDER_ORDER_BLOCKED":
            self._blocked[event.aggregate_id] = True
        elif et == "PROVIDER_ORDER_RESTORED":
            self._blocked[event.aggregate_id] = False
        elif et == "REMEDIATION_ORDERED":
            self._plans[event.aggregate_id] = {
                "case_id": event.get("case_id"),
                "provider_id": event.get("provider_id"),
                "deadline": event.get("deadline"),
                "status": "open",
            }
        elif et == "REMEDIATION_DEADLINE_EXTENDED":
            plan = self._plans.get(event.aggregate_id)
            if plan is not None:
                plan["deadline"] = event.get("new_deadline")
        elif et in ("REVIEW_PASSED", "REVIEW_FAILED"):
            plan = self._plans.get(event.aggregate_id)
            if plan is not None:
                plan["status"] = "passed" if et == "REVIEW_PASSED" else "failed"
        elif et == "REVIEW_CLOSED" and event.aggregate_type == "remediation_plan":
            plan = self._plans.get(event.aggregate_id)
            if plan is not None:
                plan["status"] = "closed"

    def license_reminders(self, day: datetime, within_days: int) -> list[LicenseDue]:
        due: list[LicenseDue] = []
        for provider_id, provider in self._providers.items():
            for license_id, lic in (provider.get("licenses") or {}).items():  # type: ignore[union-attr]
                end = datetime.fromisoformat(lic["valid_until"])  # type: ignore[index]
                if end < day:
                    days_left = -1
                else:
                    days_left = (end - day).days
                if days_left <= within_days:
                    due.append(
                        LicenseDue(
                            provider_id=provider_id,
                            license_id=license_id,
                            scope=lic["scope"],  # type: ignore[index]
                            valid_until=lic["valid_until"],  # type: ignore[index]
                            days_left=days_left,
                            blocked=bool(self._blocked.get(provider_id)),
                        )
                    )
        return sorted(due, key=lambda d: (d.valid_until, d.provider_id, d.license_id))

    def pending_reviews(self, day: datetime) -> list[PendingReview]:
        result: list[PendingReview] = []
        for plan_id, plan in self._plans.items():
            status = plan["status"]
            if status not in ("open", "failed"):
                continue
            deadline = str(plan["deadline"])
            overdue = status == "open" and day > datetime.fromisoformat(deadline)
            result.append(
                PendingReview(
                    plan_id=plan_id,
                    case_id=str(plan["case_id"]),
                    provider_id=str(plan["provider_id"]),
                    deadline=deadline,
                    overdue=overdue,
                    status=str(status),
                )
            )
        return sorted(result, key=lambda p: (p.deadline, p.plan_id))


class ProjectionSet:
    def __init__(self) -> None:
        self.intake = IntakeIndex()
        self.case_watch = CaseWatch()
        self.reminders = ReminderBoard()

    def handle(self, event: StoredEvent) -> None:
        self.intake.handle(event)
        self.case_watch.handle(event)
        self.reminders.handle(event)
