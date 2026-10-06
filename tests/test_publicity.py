"""宣传版本链与在办案件变更通知。"""

from __future__ import annotations

import unittest
from datetime import timedelta

import helpers


class PublicityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system = helpers.new_system()
        self.entity = helpers.base_entity(self.system)
        self.url = "https://example.com/promo"

    def _publish(self, content: str, now=None):
        return self.system.publish_publicity(
            entity_id=self.entity.entity_id,
            source_url=self.url,
            content=content,
            now=now or helpers.NOW,
        )

    def test_content_change_creates_new_version_and_keeps_old(self) -> None:
        v1 = self._publish("颂钵音疗体验课 199元")
        same = self._publish("颂钵音疗体验课 199元")
        self.assertEqual(v1.version_id, same.version_id)
        self.assertEqual(1, len(self.system.store.publicity_versions))

        later = helpers.NOW + timedelta(days=1)
        v2 = self._publish("限时特惠 99元", now=later)
        self.assertEqual(2, v2.version_no)
        self.assertEqual(v1.version_id, v2.supersedes)
        self.assertEqual(2, len(self.system.store.publicity_versions))
        # 旧版本原样保留
        kept = self.system.store.get("publicity_versions", v1.version_id)
        self.assertEqual("颂钵音疗体验课 199元", kept.content)

    def test_open_cases_are_notified_on_new_version(self) -> None:
        self._publish("颂钵音疗体验课 199元")
        open_case = helpers.open_case_shortcut(self.system, self.entity)
        closed_case = helpers.open_case_shortcut(self.system, self.entity)
        plan = self.system.order_remediation(
            case_id=closed_case.case_id,
            requirements="更正宣传用语",
            deadline=helpers.NOW + timedelta(days=15),
            now=helpers.NOW,
        )
        self.system.conclude_review(
            plan_id=plan.plan_id,
            passed=True,
            basis="现场复查确认宣传已更正",
            concluded_by="监管员甲",
            now=helpers.NOW,
        )

        self._publish("限时特惠 99元", now=helpers.NOW + timedelta(days=1))

        open_case = self.system.store.get("cases", open_case.case_id)
        closed_case = self.system.store.get("cases", closed_case.case_id)
        self.assertEqual(1, len(open_case.notifications))
        self.assertIn("第1版→第2版", open_case.notifications[0].message)
        # 已关闭的案件不再通知
        self.assertEqual(0, len(closed_case.notifications))


if __name__ == "__main__":
    unittest.main()
