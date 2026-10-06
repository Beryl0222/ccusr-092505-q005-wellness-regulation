# 疗愈服务合规巡检库

本项目提供疗愈服务合规巡检库的领域事件交换约定、基础校验库与核心领域服务。接入方使用统一的聚合标识、事件版本和带时区的发生时间，保证业务事实在不同环节之间可以复核。

## 目录

- `contracts/domain.schema.json`：领域事件信封和已登记类型。
- `data/sample.json`：中文联调样例。
- `src/wellness_regulation/contracts.py`：不依赖第三方包的基础校验器。
- `src/wellness_regulation/models.py`：领域对象与枚举（主体、资质、类别、负面清单、服务、宣传版本、证据、投诉、案件、整改、处罚等）。
- `src/wellness_regulation/store.py`：内存存储、受理号索引与不可篡改表守护。
- `src/wellness_regulation/system.py`：领域服务门面 `ComplianceSystem`，全部写操作入口。
- `src/wellness_regulation/access.py`：监管员职责范围判定与个人信息脱敏。
- `tests/`：契约边界检查与业务规则用例。

## 业务规则落点

- **医疗诊疗边界**：`determine_offer_boundary` 将医疗诊疗与一般体验分开判定。一般体验项目宣称医疗疗效、任何项目触碰负面清单、医疗项目缺少有效医疗资质，均判为越界，并立即限制经营主体新订单。
- **资质有效期**：`sweep_license_expiry` 把过期资质登记为 EXPIRED 并限制新订单；`expiry_reminders` 提供到期提醒。已发生的服务记录保存在不可篡改表，任何覆盖写入都会被拒绝，历史服务不被改写。
- **取证与受理号**：`capture_evidence` 按（来源类型, 来源标识）去重，同一网页或订单重复取证返回原受理号；`seal_evidence` 封存时计算内容摘要，之后不再提供改写入口。
- **宣传版本**：`publish_publicity` 对同一来源的内容变更生成新版本并保留旧版本，正在处理的案件会收到变更通知。
- **投诉分派**：线上投诉须先 `seal_complaint_evidence` 封存证据再 `open_case` 分派；发生地与登记地不同的案件自动按经营主体和发生地建立协同。
- **个人信息**：`view_case` 仅在监管员职责区域覆盖案件责任地或协同地时展示消费者个人信息，否则脱敏为 `***`。
- **整改与复核**：`extend_remediation`、`conclude_review`、`revoke_penalty` 均强制留下决定依据，缺依据即拒绝。
- **处罚可追溯**：`issue_penalty` 校验宣传版本与资质在服务发生时有效；`explain_penalty` 给出"哪条宣传、哪次服务、哪份有效资质"的完整链条。
- **服务重启**：`restart_service` 只解除新订单限制，不改写资质状态、到期提醒与未完成复核，重启后这些照常继续。

所有状态变更都会以契约事件追加到 `store.events`，可用 `validate_event` 对照 `contracts/domain.schema.json` 复核。

## 测试

```bash
python3 -m unittest discover -s tests
```

## 编译检查

```bash
python3 -m compileall -q src tests
```
