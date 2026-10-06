"""跨区域案件协同与监管员职责范围内的个人信息访问。"""

from __future__ import annotations

import unittest

import helpers


class CaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system = helpers.new_system()
        self.entity = helpers.base_entity(self.system, region="杭州市")

    def test_cross_region_case_coordinates_entity_and_occurrence_regions(self) -> None:
        case = helpers.open_case_shortcut(self.system, self.entity, region="宁波市")
        self.assertEqual("宁波市", case.responsible_region)
        self.assertEqual(("宁波市", "杭州市"), case.coordinating_regions)
        coordinated = [
            e for e in self.system.store.events if e["event_type"] == "CASE_COORDINATED"
        ]
        self.assertEqual(1, len(coordinated))
        self.assertEqual(case.case_id, coordinated[0]["aggregate_id"])

    def test_same_region_case_has_single_region(self) -> None:
        case = helpers.open_case_shortcut(self.system, self.entity, region="杭州市")
        self.assertEqual(("杭州市",), case.coordinating_regions)
        coordinated = [
            e for e in self.system.store.events if e["event_type"] == "CASE_COORDINATED"
        ]
        self.assertEqual(0, len(coordinated))

    def test_pii_visible_only_within_jurisdiction(self) -> None:
        case = helpers.open_case_shortcut(self.system, self.entity, region="宁波市")
        local = self.system.register_regulator(name="宁波监管员", regions=("宁波市",))
        outsider = self.system.register_regulator(name="外地监管员", regions=("北京市",))

        visible = self.system.view_case(regulator_id=local.regulator_id, case_id=case.case_id)
        self.assertEqual("王女士", visible["complaint"]["consumer"]["name"])
        self.assertEqual("13800000000", visible["complaint"]["consumer"]["phone"])

        masked = self.system.view_case(
            regulator_id=outsider.regulator_id, case_id=case.case_id
        )
        consumer = masked["complaint"]["consumer"]
        self.assertEqual("***", consumer["name"])
        self.assertEqual("***", consumer["phone"])
        self.assertEqual("***", consumer["id_number"])
        # 案件本身仍可见，只有个人信息脱敏
        self.assertEqual(case.case_id, masked["case_id"])


if __name__ == "__main__":
    unittest.main()
