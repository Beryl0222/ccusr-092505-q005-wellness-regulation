"""整改延期、复查通过、申诉撤销都必须留下决定依据。"""

from __future__ import annotations

import unittest
from datetime import timedelta

import helpers
from wellness_regulation.errors import RationaleRequiredError
from wellness_regulation.models import CaseStatus, PenaltyStatus, RemediationStatus


class RemediationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system = helpers.new_system()
        self.entity = helpers.base_entity(self.system)
        self.case = helpers.open_case_shortcut(self.system, self.entity)
        self.plan = self.system.order_remediation(
            case_id=self.case.case_id,
            requirements="下架夸大宣传并调整退款条款",
            deadline=helpers.NOW + timedelta(days=15),
            now=helpers.NOW,
        )

    def test_extension_requires_rationale(self) -> None:
        new_deadline = helpers.NOW + timedelta(days=30)
        with self.assertRaises(RationaleRequiredError):
            self.system.extend_remediation(
                plan_id=self.plan.plan_id,
                new_deadline=new_deadline,
                rationale="  ",
                decided_by="监管员甲",
                now=helpers.NOW,
            )
        plan = self.system.extend_remediation(
            plan_id=self.plan.plan_id,
            new_deadline=new_deadline,
            rationale="商家提交整改排期，延期具备正当理由",
            decided_by="监管员甲",
            now=helpers.NOW,
        )
        self.assertEqual(new_deadline, plan.deadline)
        self.assertEqual(RemediationStatus.EXTENDED, plan.status)
        self.assertEqual(1, len(plan.extensions))
        self.assertEqual("商家提交整改排期，延期具备正当理由", plan.extensions[0].rationale)
        self.assertEqual("监管员甲", plan.extensions[0].decided_by)

    def test_review_pass_requires_basis(self) -> None:
        with self.assertRaises(RationaleRequiredError):
            self.system.conclude_review(
                plan_id=self.plan.plan_id,
                passed=True,
                basis="",
                concluded_by="监管员甲",
                now=helpers.NOW,
            )
        conclusion = self.system.conclude_review(
            plan_id=self.plan.plan_id,
            passed=True,
            basis="现场复查确认宣传已更正、退款条款已公示",
            concluded_by="监管员甲",
            now=helpers.NOW,
        )
        self.assertTrue(conclusion.passed)
        self.assertEqual("现场复查确认宣传已更正、退款条款已公示", conclusion.basis)
        case = self.system.store.get("cases", self.case.case_id)
        self.assertEqual(CaseStatus.CLOSED, case.status)

    def test_failed_review_keeps_case_open(self) -> None:
        self.system.conclude_review(
            plan_id=self.plan.plan_id,
            passed=False,
            basis="附加消费仍未公示退款条件",
            concluded_by="监管员甲",
            now=helpers.NOW,
        )
        case = self.system.store.get("cases", self.case.case_id)
        self.assertEqual(CaseStatus.REMEDIATION, case.status)

    def test_appeal_revocation_requires_rationale(self) -> None:
        system = helpers.new_system()
        chain = helpers.penalty_chain(system)
        with self.assertRaises(RationaleRequiredError):
            system.revoke_penalty(
                penalty_id=chain.penalty.penalty_id,
                rationale="",
                decided_by="复议委员会",
                now=helpers.NOW,
            )
        penalty = system.revoke_penalty(
            penalty_id=chain.penalty.penalty_id,
            rationale="申诉成立：取证页面时间与订单时间不匹配",
            decided_by="复议委员会",
            now=helpers.NOW,
        )
        self.assertEqual(PenaltyStatus.REVOKED, penalty.status)
        self.assertEqual("申诉成立：取证页面时间与订单时间不匹配", penalty.revoked_rationale)
        self.assertEqual("复议委员会", penalty.revoked_by)


if __name__ == "__main__":
    unittest.main()
