"""工作流测试：投诉封存分派、跨区域协同、整改复核、处罚溯源、提醒重放。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import wellness_regulation as wr  # noqa: E402
from wellness_regulation import (  # noqa: E402
    Clock,
    Inspector,
    InspectionCase,
    RegulationSystem,
)
from wellness_regulation.errors import AccessDenied, DomainError  # noqa: E402

CST = timezone(timedelta(hours=8))


class FixedClock(Clock):
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 1, 9, 0, tzinfo=CST)
        counter = iter(range(10_000))
        super().__init__(now_fn=lambda: self.current, id_fn=lambda: f"id-{next(counter)}")

    def advance(self, **delta: int) -> None:
        self.current += timedelta(**delta)


def iso(day: str, hour: str = "12:00:00") -> str:
    return f"{day}T{hour}+08:00"


class WorkflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FixedClock()
        self.sys = RegulationSystem(clock=self.clock)
        self.sys.define_category("sound", "音声放松", "general_experience")
        self.sys.update_negative_list("sound", ["包治失眠"], [], "负面清单")
        self.pid = self.sys.register_provider("静心文化", "杭州", provider_id="p1")
        self.sys.verify_license(
            self.pid, "L1", "体验服务", iso("2026-01-01"), iso("2026-12-31", "23:59:59"), "证照核验"
        )
        self.oid = self.sys.register_offer(self.pid, "sound", "音声课", offer_id="o1")
        self.prac = self.sys.register_practitioner(
            "王老师", self.pid, "音声引导", iso("2027-01-01"), practitioner_id="w1"
        )
        self.sh = Inspector("i-sh", "沪监管员", ("上海",))
        self.hz = Inspector("i-hz", "杭监管员", ("杭州",))
        self.bj = Inspector("i-bj", "京监管员", ("北京",))

    def seal_and_complaint(
        self,
        fingerprint: str = "fp-1",
        source: str = "https://x/p",
        region: str = "上海",
        case_id: str = "case1",
        consumer: str | None = "c1",
        auth: str | None = "a1",
    ) -> str:
        if auth:
            self.sys.grant_authorization("c1", "案件调查", iso("2027-12-31"), auth_id=auth)
        intake, _, _ = self.sys.seal_evidence(fingerprint, "webpage", source, iso("2026-09-01", "08:00:00"))
        self.sys.capture_promotion(self.oid, "包治失眠，可治疗", fingerprint, region, intake)
        return self.sys.file_online_complaint(
            self.pid, region, self.oid, intake, "页面宣称治疗",
            consumer_ref=consumer, authorization_id=auth, case_id=case_id,
        )


class ComplaintAndCollaborationTests(WorkflowTestCase):
    def test_seal_then_assign_and_cross_region_collaboration(self) -> None:
        case_id = self.seal_and_complaint()
        # 未分派不能直接协同（案件须先分派）
        with self.assertRaises(DomainError):
            self.sys.link_jurisdiction(case_id, self.hz, "杭州", "属地协同")
        self.sys.assign_case(case_id, self.sh, "发生地管辖")
        self.sys.link_jurisdiction(case_id, self.hz, "杭州", "经营主体属地协同")
        case = self.sys.repo.load(InspectionCase, case_id)
        self.assertEqual(case.involved_regions, ("上海", "杭州"))

    def test_jurisdiction_limited_to_home_and_occurrence_regions(self) -> None:
        case_id = self.seal_and_complaint()
        self.sys.assign_case(case_id, self.sh, "发生地管辖")
        # 北京既非发生地也非主体属地，即使监管员有北京辖区权限也不能加入
        with self.assertRaises(DomainError):
            self.sys.link_jurisdiction(case_id, self.bj, "北京", "无关辖区")
        # 杭州监管员不能冒充上海
        with self.assertRaises(AccessDenied):
            self.sys.assign_case(case_id, self.hz, "跨辖区抢案")

    def test_duplicate_lead_region_is_rejected(self) -> None:
        case_id = self.seal_and_complaint()
        self.sys.assign_case(case_id, self.sh, "发生地管辖")
        with self.assertRaises(DomainError):
            self.sys.link_jurisdiction(case_id, self.sh, "上海", "重复关联")


class VersionNotificationTests(WorkflowTestCase):
    def test_new_promotion_version_notifies_open_cases_only(self) -> None:
        case_id = self.seal_and_complaint(case_id="case1")
        self.sys.assign_case(case_id, self.sh, "发生地管辖")
        # 网页内容变更：受理号不变，形成新版本，在办案件被通知
        intake, status, rev = self.sys.seal_evidence(
            "fp-2", "webpage", "https://x/p", iso("2026-09-05", "08:00:00")
        )
        self.assertEqual((status, rev), ("revised", 2))
        outcome, version = self.sys.capture_promotion(
            self.oid, "治疗抑郁症", "fp-2", "上海", intake
        )
        self.assertEqual((outcome, version), ("new", 2))
        case = self.sys.repo.load(InspectionCase, case_id)
        self.assertEqual([(n.version, n.source) for n in case.notices], [(2, "promotion")])

        # 案件结案后再来新版本，不再通知
        plan = self.sys.order_remediation(
            case_id, ["删除违禁词"], iso("2026-09-20"), "广告法", plan_id="plan1"
        )
        self.sys.review_remediation(plan, True, "已删除")
        self.sys.close_remediation(plan, "整改到位", "复核通过结案")
        self.sys.seal_evidence("fp-3", "webpage", "https://x/p", iso("2026-09-08", "08:00:00"))
        self.sys.capture_promotion(self.oid, "合规", "fp-3", "上海", intake)
        case = self.sys.repo.load(InspectionCase, case_id)
        self.assertEqual(len(case.notices), 1)

    def test_new_pricing_version_notifies_open_case(self) -> None:
        # 杭州消费者投诉订单退款：先封存订单证据再受理
        self.sys.grant_authorization("hz-consumer", "案件调查", iso("2027-12-31"), auth_id="a-hz")
        intake, _, _ = self.sys.seal_evidence(
            "order-fp-1", "order", "ORD-HZ-7", iso("2026-09-02", "10:00:00")
        )
        case_id = self.sys.file_online_complaint(
            self.pid, "杭州", self.oid, intake, "附加消费退款被拒",
            consumer_ref="hz-consumer", authorization_id="a-hz", case_id="case-hz",
        )
        self.sys.assign_case(case_id, self.hz, "经营主体属地管辖")
        outcome, version = self.sys.publish_pricing_terms(
            self.oid,
            {"items": [
                {"component": "package", "name": "主套餐", "refund_window_days": 7, "refundable": True},
                {"component": "addon", "name": "加时包", "refund_window_days": 7, "refundable": True},
            ]},
            "price-fp-1",
            intake,
        )
        self.assertEqual((outcome, version), ("new", 1))
        case = self.sys.repo.load(InspectionCase, case_id)
        self.assertEqual([(n.version, n.source) for n in case.notices], [(1, "pricing")])


class RemediationAndAppealTests(WorkflowTestCase):
    def _open_case_with_plan(self) -> tuple[str, str]:
        case_id = self.seal_and_complaint()
        self.sys.assign_case(case_id, self.sh, "发生地管辖")
        plan_id = self.sys.order_remediation(
            case_id, ["删除治疗表述"], iso("2026-09-20"), "广告法第十七条", plan_id="plan1"
        )
        return case_id, plan_id

    def test_extension_requires_later_deadline_and_basis(self) -> None:
        _, plan_id = self._open_case_with_plan()
        with self.assertRaises(DomainError):
            self.sys.extend_remediation(plan_id, iso("2026-09-19"), "提前不允许")
        with self.assertRaises(DomainError):
            self.sys.extend_remediation(plan_id, iso("2026-09-30"), " ")
        self.sys.extend_remediation(plan_id, iso("2026-09-30"), "商家提交部分整改证据，经审核同意")
        plan = self.sys.repo.load(wr.RemediationPlan, plan_id)
        self.assertEqual(plan.deadline, iso("2026-09-30"))
        self.assertEqual(len(plan.extensions), 1)

    def test_review_pass_and_fail_keep_basis(self) -> None:
        _, plan_id = self._open_case_with_plan()
        self.sys.review_remediation(plan_id, False, "复查页面仍含治疗表述")
        plan = self.sys.repo.load(wr.RemediationPlan, plan_id)
        self.assertEqual(plan.status, "failed")
        with self.assertRaises(DomainError):
            self.sys.close_remediation(plan_id, "x", "y")

    def test_appeal_and_withdrawal_keep_decision_basis(self) -> None:
        case_id, _ = self._open_case_with_plan()
        with self.assertRaises(DomainError):
            self.sys.appeal_case(case_id, " ")
        self.sys.appeal_case(case_id, "消费者主张存在新证据")
        self.assertEqual(self.sys.repo.load(InspectionCase, case_id).status, "appealed")
        with self.assertRaises(DomainError):
            self.sys.withdraw_appeal(case_id, " ")
        self.sys.withdraw_appeal(case_id, "核实新证据不成立，消费者提交书面撤告")
        case = self.sys.repo.load(InspectionCase, case_id)
        self.assertEqual(case.status, "remediating")
        self.assertTrue(case.withdraw_basis)

    def test_overdue_remediation_is_reported(self) -> None:
        _, plan_id = self._open_case_with_plan()
        self.clock.advance(days=30)
        pending = self.sys.pending_reviews()
        self.assertEqual([p.plan_id for p in pending], [plan_id])
        self.assertTrue(pending[0].overdue)


class PersonalInfoAccessTests(WorkflowTestCase):
    def test_out_of_jurisdiction_inspector_denied(self) -> None:
        case_id = self.seal_and_complaint()
        self.sys.assign_case(case_id, self.sh, "发生地管辖")
        with self.assertRaises(AccessDenied):
            self.sys.access_personal_info(case_id, self.bj, "查看付款人", "无辖区关系")

    def test_missing_consumer_authorization_denied(self) -> None:
        case_id = self.seal_and_complaint(case_id="case2", consumer=None, auth=None)
        self.sys.assign_case(case_id, self.sh, "发生地管辖")
        with self.assertRaises(AccessDenied):
            self.sys.access_personal_info(case_id, self.sh, "查看付款人", "无授权")

    def test_expired_authorization_denied_and_revoked_denied(self) -> None:
        self.sys.grant_authorization("c1", "案件调查", iso("2026-08-01"), auth_id="old")
        intake, _, _ = self.sys.seal_evidence("fp-1", "webpage", "https://x/p", iso("2026-09-01"))
        case_id = self.sys.file_online_complaint(
            self.pid, "上海", self.oid, intake, "投诉",
            consumer_ref="c1", authorization_id="old", case_id="case3",
        )
        self.sys.assign_case(case_id, self.sh, "管辖")
        with self.assertRaises(AccessDenied):
            self.sys.access_personal_info(case_id, self.sh, "查看", "授权已过期")

        self.sys.grant_authorization("c2", "案件调查", iso("2027-01-01"), auth_id="live")
        self.sys.revoke_authorization("live", "调查结束，消费者撤回")
        # revoke 不作用于案件，仍构造一个授权 live 的案件验证撤销失效
        intake2, _, _ = self.sys.seal_evidence("fp-2", "webpage", "https://z/p", iso("2026-09-02"))
        case4 = self.sys.file_online_complaint(
            self.pid, "上海", self.oid, intake2, "投诉2",
            consumer_ref="c2", authorization_id="live", case_id="case4",
        )
        self.sys.assign_case(case4, self.sh, "管辖")
        with self.assertRaises(AccessDenied):
            self.sys.access_personal_info(case4, self.sh, "查看", "授权已撤销")

    def test_authorized_access_is_logged_on_case(self) -> None:
        case_id = self.seal_and_complaint()
        self.sys.assign_case(case_id, self.sh, "发生地管辖")
        self.sys.access_personal_info(case_id, self.sh, "核实付款账户", "授权+属地")
        case = self.sys.repo.load(InspectionCase, case_id)
        self.assertEqual(len(case.access_log), 1)
        self.assertEqual(case.access_log[0].inspector_id, "i-sh")


class PenaltyTraceabilityTests(WorkflowTestCase):
    def test_penalty_links_promotion_occurrence_and_effective_license(self) -> None:
        case_id = self.seal_and_complaint()
        self.sys.assign_case(case_id, self.sh, "发生地管辖")
        occ = self.sys.record_service_occurrence(
            self.pid, self.oid, self.prac, "c1", "上海", "ORD-1", iso("2026-08-20"),
            occurrence_id="occ1",
        )
        explanation = self.sys.issue_penalty(
            case_id, occ, 1, "广告法第十七条", "责令停止发布并罚款",
            "封存页面、询问笔录、订单凭证", penalty_id="pen1",
        )
        self.assertEqual(explanation["promotion"]["promotion_version"], 1)
        self.assertEqual(explanation["promotion"]["evidence_intake_no"],
                         self.sys.projections.intake.intake_for_source("webpage", "https://x/p"))
        self.assertEqual(explanation["occurrence"]["order_ref"], "ORD-1")
        self.assertEqual(explanation["occurrence"]["service_region"], "上海")
        self.assertEqual(explanation["license_snapshot"]["license_id"], "L1")
        self.assertEqual(explanation["license_snapshot"]["effective_at"], iso("2026-08-20"))

    def test_penalty_requires_effective_license_at_service_time(self) -> None:
        # 商家资质 2026-01 才核验通过，服务发生在 2025 年 -> 无有效资质，拒绝出罚快照
        case_id = self.seal_and_complaint(case_id="case7")
        self.sys.assign_case(case_id, self.sh, "管辖")
        occ = self.sys.record_service_occurrence(
            self.pid, self.oid, self.prac, "c1", "上海", "OLD-1", iso("2025-12-01"),
            occurrence_id="occ-old",
        )
        with self.assertRaises(DomainError):
            self.sys.issue_penalty(case_id, occ, 1, "条款", "决定", "依据")

    def test_penalty_requires_all_three_elements(self) -> None:
        from wellness_regulation import Penalty

        penalty = Penalty("pen-x")
        with self.assertRaises(DomainError):
            penalty.issue(
                self.clock, case_id="c", provider_id=self.pid,
                promotion_ref={}, occurrence_ref={"x": 1}, license_snapshot={"x": 1},
                regulation_clause="条", decision="定", basis="据",
            )


class ReminderAndRestartTests(WorkflowTestCase):
    def test_license_due_reminder_survives_block_restore(self) -> None:
        self.sys.screen_offer(self.oid, "sound", "包治失眠")
        blocked = [d for d in self.sys.license_reminders(within_days=400) if d.license_id == "L1"]
        self.assertTrue(blocked and blocked[0].blocked)
        self.sys.restore_new_orders(self.pid, "整改通过恢复接单")
        # 恢复后到期提醒继续存在，blocked 标志刷新
        due = self.sys.license_reminders(within_days=400)
        entry = next(d for d in due if d.license_id == "L1")
        self.assertFalse(entry.blocked)

    def test_restart_rebuilds_state_from_events(self) -> None:
        case_id = self.seal_and_complaint()
        self.sys.assign_case(case_id, self.sh, "发生地管辖")
        self.sys.link_jurisdiction(case_id, self.hz, "杭州", "主体属地协同")
        self.sys.access_personal_info(case_id, self.sh, "核实付款", "授权")
        plan_id = self.sys.order_remediation(
            case_id, ["删词"], iso("2026-09-20"), "广告法", plan_id="plan1"
        )
        occ = self.sys.record_service_occurrence(
            self.pid, self.oid, self.prac, "c1", "上海", "ORD-1", iso("2026-08-20"),
            occurrence_id="occ1",
        )
        self.sys.issue_penalty(case_id, occ, 1, "条款", "罚款", "依据", penalty_id="pen1")

        restarted = RegulationSystem.from_events(self.sys.export_events(), clock=self.clock)
        case = restarted.repo.load(InspectionCase, case_id)
        self.assertEqual(case.involved_regions, ("上海", "杭州"))
        self.assertEqual(len(case.access_log), 1)
        self.assertEqual(restarted.repo.load(wr.RemediationPlan, plan_id).status, "open")
        self.assertEqual(
            restarted.explain_penalty("pen1")["occurrence"]["order_ref"], "ORD-1"
        )
        self.assertTrue(restarted.license_reminders(within_days=400))
        # 未完成复核在重启后仍被追踪
        self.assertEqual([p.plan_id for p in restarted.pending_reviews()], [plan_id])


if __name__ == "__main__":
    unittest.main()
