# 疗愈服务合规巡检库

面向音声、禅修、线上课程等疗愈服务的监管领域库：管理经营主体与资质有效期、服务类别与负面清单、宣传与价格退款条款版本、从业人员、消费者授权证据、巡查任务、整改期限与复核结论，并保证处罚可以回溯到"哪条宣传、哪次服务、哪份有效资质"。

系统采用**事件溯源**：业务状态只由仅追加的领域事件重放得到，不做物理删除或覆盖；重启后重放事件即可恢复全部状态、投影与提醒。

## 核心规则如何落地

| 监管要求 | 实现方式 |
| --- | --- |
| 医疗诊疗边界与一般体验分开判定 | `catalog.ServiceCategory` 区分 `general_experience` / `medical_diagnosis`；一般体验文案命中"治疗/治愈/根治"等判越界，医疗类别的固有诊疗用语不判越界 |
| 套餐拆分附加消费退款陷阱 | `catalog.analyze_pricing`：附加项概不退款、退款窗口短于套餐均给出命中项 |
| 资质过期/触碰禁项立即限新单 | `Provider.evaluate_block_reasons` + `screen_offer` 自动产生 `PROVIDER_ORDER_BLOCKED`；`place_new_order` 是下单闸口 |
| 不得篡改已发生服务 | `ServiceOccurrence` 仅允许一次 `SERVICE_RENDERED`，重复登记/`amend`/重放重复事件均抛 `ImmutableFactError`；限单不影响旧服务 |
| 同网页/订单重复取证保持原受理号 | 受理号按来源（`kind:source_ref`）稳定；同指纹 `duplicate`，内容变更 `revised` 形成修订版，受理号不变 |
| 内容变更形成新版本并通知在办案件 | `OFFER_CAPTURED` / `PRICING_TERMS_PUBLISHED` 仅在指纹变化时升版，`CaseWatch` 投影把通知发给在办案件（结案后不通知） |
| 线上投诉先封存再分派 | `file_online_complaint` 强制受理号已存在；事件序为 `EVIDENCE_SEALED → COMPLAINT_FILED → CASE_ASSIGNED` |
| 跨区域协同 | 协同辖区只能是**服务发生地**或**经营主体属地**，且须在监管员辖区内；每次关联留依据 |
| 个人信息最小访问 | 双门控：辖区职责 + 消费者有效授权（过期/撤销即拒），每次访问落 `CASE_PERSONAL_INFO_ACCESSED` |
| 延期/复查/申诉撤销留依据 | 无 `basis` 的延期、复核、恢复、撤销、处罚一律拒绝；延期只能延后不能提前 |
| 服务重启后继续提醒 | `ReminderBoard` 由事件重放：资质到期（含已限单/已恢复主体）与未完成/逾期复核持续跟踪 |
| 处罚三要素可解释 | `issue_penalty` 强制关联宣传版本（含受理号）、服务事实（订单/发生地）、服务发生时有效的资质快照，缺任一要素拒绝出罚 |

## 目录

- `contracts/domain.schema.json`：领域事件信封与已登记的 11 类聚合、27 类事件。
- `src/wellness_regulation/`
  - `contracts.py`：交换层基础校验（必填、类型、时区、版本、枚举）。
  - `engine.py`：仅追加 `EventStore`（版本连续校验）与事件溯源 `Aggregate`。
  - `catalog.py`：服务类别、负面清单、医疗边界与退款陷阱判定（纯函数可复核）。
  - `provider.py`：经营主体、资质有效期、限单/恢复。
  - `offer.py`：服务项目，宣传文案与价格退款条款两条独立版本线。
  - `people.py`：从业人员与不可变服务事实。
  - `evidence.py`：消费者授权与按受理号封存的证据。
  - `inspection.py`：巡查案件、整改计划、处罚溯源。
  - `security.py`：辖区 + 授权双门控。
  - `projections.py`：受理号索引、在办案件监视、到期与未完成复核提醒。
  - `system.py`：`RegulationSystem` 命令总装（推荐入口）。
- `data/sample.json`：单事件信封中文样例；`data/walkthrough.json`：26 个事件的端到端联调流。
- `scripts/generate_demo.py`：重新生成联调样例。
- `tests/`：契约检查、领域/工作流规则、联调流重放（共 53 个测试）。

## 快速使用

```python
from datetime import datetime, timezone, timedelta
from wellness_regulation import RegulationSystem, Inspector

sys = RegulationSystem()
sys.define_category("sound", "音声放松", "general_experience")
sys.update_negative_list("sound", ["包治失眠"], ["诱导大额预付"], "负面清单2026版")

pid = sys.register_provider("静心文化", "杭州")
sys.verify_license(pid, "L-001", "一般体验服务",
                   "2026-01-01T00:00:00+08:00", "2026-12-31T23:59:59+08:00", "证照核验单")
oid = sys.register_offer(pid, "sound", "睡眠音声课")

# 巡检：一般体验宣称治疗 + 命中禁项 → 立即限新单
analysis = sys.screen_offer(oid, "sound", "本课程可治疗失眠、包治失眠")
analysis.crosses_medical_boundary  # True
# sys.place_new_order(pid)  # 抛 DomainError

# 证据先封存（受理号在同一网页上永久稳定）
intake_no, status, revision = sys.seal_evidence(
    "fp-1", "webpage", "https://shop/p", "2026-10-01T08:00:00+08:00"
)
```

重启恢复与处罚解释：

```python
restarted = RegulationSystem.from_events(sys.export_events())
restarted.license_reminders(within_days=30)   # 到期提醒（限单/恢复后都继续）
restarted.pending_reviews()                   # 未完成或逾期的复核
restarted.explain_penalty("demo-penalty-01")
# {"promotion": {...宣传版本、指纹、受理号...},
#  "occurrence": {...订单、发生地、从业人员...},
#  "license_snapshot": {...服务发生时有效的资质...}, ...}
```

## 测试

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q src tests scripts
```

## 设计边界

- 本库只产出领域事实与决定依据，不直接对接支付/下单系统；`place_new_order` 是供外部下单系统调用的闸口接口。
- 医疗边界词表与负面清单按类别版本化维护，判定结果只报告命中词与依据，不代替法律适用。
- 事件载荷保留信封外的扩展字段（`additionalProperties`），接入方可追加证据哈希、附件地址等信息。
