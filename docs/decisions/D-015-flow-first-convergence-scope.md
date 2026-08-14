# D-015 — Flow-first 收敛范围裁决

> 状态：**ACCEPTED（2026-08-14）**——owner 拍板：flow-first + 去冗余砍代码 + 出口 2 首演示。

## Context

CaseLoop 终态叙事（完整产品叙事文档 / 项目信息说明书）定义的是「可信治理」：每一步
变更都要可证明（不可变版本、证据封存、双用途 Gate、审计）。V5 施工计划按此口径排布。

Owner 于 2026-08-14 提出并确认：编排闭环（agent team + skills + MCP + Langfuse +
沙箱验证 + 人工卡点）是运转核心，应优先跑通；证明层（不可变版本、证据封存等）作为
渐进加固，不阻塞 flow 先转起来。

## Decision

1. **Flow-first**：主线改为先跑通「投诉 → 立案 → 取证 → 归因 → 修复 → 沙箱验证 →
   人工卡点 → VerifiedCandidate / NOT DEPLOYED」的编排闭环，复用存量 v3/v4 服务；
2. **被治理对象 = Agent Station**（本机现成：团队在跑、Langfuse 已收其 trace）；
3. **首个演示闭环 = 出口 2（Verification-only）**：不碰部署与上线；
4. **人工卡点形式**：审批通道 = team 原生 Matrix 消息（Human CR）；同一通道做 CLI 与 MCP 封装（人可 Element 直回或 CLI 发送，系统只认 Matrix 事件 + nonce 验证），不另建 Console；
5. **证明层不砍、只后置**：不可变 SystemVersionSet、Episode Snapshot 封存、
   two-purpose Gate 仍保留在收敛计划中，作为渐进加固项；
6. **废弃链路处置不变**：归档/冻结，不删除；
7. **去冗余执行**：依据 `docs/plans/native-capability-audit.md`（三路审计）立即执行——能用平台原生就用原生（A 替换），过度工程与重复（含自重复的 V4/V5 双轨）一律砍/归档（B/C），占位包规划重造一律重定向（D）；处置并入 flow-first 第 0 步。

## Consequences

- 主线排期从「V5 逐 stage 证明层优先」切换为「flow-first 计划」（见
  `docs/plans/flow-first-closure.md`）；
- V5 stage 施工不取消，降级为渐进加固路线，随 flow 需要逐项启用；
- flow 跑通不等于叙事终态：每段结果在证明层到位前仍是「人可复核」，不是「机器可证明」。

## Alternatives

- 维持 V5 逐 stage 证明层优先：被拒——owner 判定编排闭环优先；
- 砍掉证明层：被拒——证明层是 CaseLoop 的差异化，只后置不删除。