# agents/ OPEN-ISSUES（T5 成稿对冻结设计的异议与解释）

> 成稿按 `docs/plans/wave3-soul-design.md`（冻结）骨架写作；以下为成稿过程中发现的需要主控裁决/知悉的差异。
> 每项给出：冻结设计原文 → 成稿做法 → 理由 → 建议。
> 状态：**待主控裁决**（`冻结`=按冻结原样；`采纳`=采纳成稿解释）。

---

## 1. `approval.request` 的 ACL 归属（设计 vs spec 冲突）

- **冻结设计**：wave3 §6「修复师工具面：mcp-release-admin（workorder.draft/freeze/approval.request）」——把 `approval.request` 列给了修复师。
- **spec §9.4 ACL**：`approval.request` 仅**守门员**可调（impl `release_admin.py` docstring 同样标注 "ACL：守门员"）。
- **成稿做法**：`approval.request` 归守门员（gatekeeper SOUL §2）；修复师只到 `workorder.draft` / `workorder.freeze`，其「永不」明确"不直接提请审批"。
- **理由**：spec §9.4 与 impl 一致（守门员），且门禁先于审批（GATE_PASSED 前置校验），提请审批天然在守门员域内；修复师提请会造成"运动员自报通过"。
- **建议**：**采纳**成稿做法；如需修复师可提请，需 spec §9.4 ACL 一并对拍修改。

## 2. `case.escalate` / `case.timeline` 在质量官工具面

- **冻结设计**：wave3 §1「质量官工具面：case.list/get/timeline/claim/submit_suggestion」；「永不：直接写控制面状态（只 submit_suggestion）」。
- **impl / README**：`case.escalate`（ACL 全员）、`case.timeline` 在 `mcp-servers/README.md` 与 `case_admin.py` 中真实存在，但不在 spec §9.3 表中。
- **成稿做法**：质量官工具面加了 `case.escalate`（升级人工机制）；「永不」解释为"只经 `case.submit_suggestion` / `case.escalate` 这类控制面通道提交，不调 Quality API 写面"。
- **理由**：质量官的"升级决策"职责需要真实升级通道；`case.submit_suggestion` 的 kind 枚举（triage|attribution|fix|gate|verify）不含 escalate；`case.escalate` 是 impl 提供的唯一升级路径且 ACL 全员。
- **建议**：**采纳**成稿解释；或主控确认后把 spec §9.3 补上 `case.timeline` / `case.escalate` 两行。

## 3. 守门员的 trust-ledger 可达性（内嵌模块 vs 远程 Worker）

- **冻结设计**：wave3 §3「守门员工具面含 trust-ledger（查账本状态）」。
- **spec §9.8**：trust-ledger 是**库**，内嵌于 Case Controller 与守门员运行时，**不是独立 MCP server**；5 个 MCP server 中无 trust 查询工具。
- **成稿做法**：gatekeeper SOUL §2 把 trust-ledger 表述为"内嵌模块（spec §9.8）：`get_state` / `evaluate_promotion`"，用于放行建议时核对账本；若 gatekeeper 以远程 AgentTeams Worker 运行，账本查询需控制面透出。
- **理由**：不编造 MCP 工具；按 spec §9.8 的"内嵌"口径如实描述。
- **建议**：**采纳**成稿表述；Phase 2 考虑在网关侧补一个只读 trust 查询工具（或经 `case.timeline` 的 `TrustOutcomeRecorded` 事件透出）。

## 4. 工具级 ACL 的粒度（server 级 consumers vs 单工具）

- **冻结设计 / spec §9.2**：鉴权三层，第一层"Higress 按身份做工具级 ACL"。
- **实测**：Higress consumers 是 **server 级**（每个 mcpServer 一份 `allowedConsumers`）；`mcp-servers/README.md` 也写明"ACL 由 Higress 网关执行（server 侧文档声明 + `kb.upsert` 本地校验）"，未实现单工具级网关 ACL。
- **成稿做法**：双约束——Step 10 consumers 按 server 授权到人 + 各 SOUL §4「永不」列明不可调用其他角色专属工具（`gate.run` / `experiment.*` / `kb.upsert` / `workorder.*`）。
- **理由**：在不改动网关能力的前提下尽量收紧；SOUL 工具面纪律是行为层兜底。
- **建议**：**采纳**双约束；若需要强制单工具 ACL，属 mcp-servers/ 网关配置的后续工作。

## 5. 质量官的 mcp-notification 工具面

- **冻结设计**：wave3 §1「mcp-notification（升级人工时发消息）」。
- **spec §9.6 ACL**：`feishu.reply_origin`（控制面/案例官）、`feishu.approval_card`（控制面）、`feishu.weekly_report`（案例官）、`matrix.log`（全员）。
- **成稿做法**：质量官的 notification 工具面只列 `matrix.log`；升级到人的通知由控制面投递（`case.escalate` → 控制面 → 飞书审批卡/原群消息）。
- **理由**：按 §9.6 ACL，质量官能用的通知工具只有 `matrix.log`；把人话消息通道给质量官会与"控制面投递"铁律冲突。
- **建议**：**采纳**成稿解释；如需质量官直发人话，需放宽 §9.6 ACL。

## 6. Team CR 无 `goal` 字段（spike 用法会被静默剪掉）

- **现象**：`agents/spike/team.yaml` 的 Team spec 带 `goal`，但 v1beta1 TeamSpec（v1.2.x 上游）**无该字段**，controller 对未知字段静默剪除。
- **成稿做法**：`agents/team.yaml` Team 仅用 `description`（目标并入），不写 `goal`。
- **建议**：**采纳**；团队目标若需结构化，Phase 2 由控制面维护。

## 7. 弹性模板的 Phase 1 口径

- **冻结设计**：wave3 §7「attributionist / repairer 以 worker 模板形式定义；Phase 1 固定 warm pool，不宣称动态扩缩」。
- **成稿做法**：二者以普通 Worker CR 部署（与常设一致），SOUL 头部标注"弹性 Worker（Phase 1 固定 warm pool，不宣称动态扩缩）"；扩缩容统一口径"Agent 申请、控制面决策执行"（spec §8.3）。
- **建议**：**采纳**；Phase 2 再由 Caseload Controller 对这两类 Worker 做 create/drain/remove。

## 8. Worker 空闲休眠默认行为

- **事实**：Worker 空闲超时可能进入 `Sleeping`（spike 验证可唤醒）；`state` 默认 Running。
- **成稿做法**：team.yaml 显式 `state: Running`，不设 `idleTimeout`，由平台默认行为决定休眠；RUNBOOK Step 5 注明"被 @mention 时唤醒"。
- **建议**：演示若要求 worker 常驻，可在后续把 `idleTimeout` 调大或关停休眠（平台配置项）。
