# 施工指南（所有施工 agent 必读）

## 角色分工
- **Kimi（主控）**：规划、拆任务、契约冻结把关、验收、e2e、视觉/前端验证、证据归档
- **Claude Code（coder subagent）**：仓库内中等粒度实现 + 自测
- **Grok 4.5（grok_run）**：成块后端基建，session 续接做修订

## 并行纪律
- 按文件 scope 切分，互不相交：`demo-app/` `control-plane/` `eval-harness/` `mcp-servers/` `contracts/` `agents/` `console/` 各归一家
- `contracts/` 冻结前不开工任何实现；契约变更必须主控批准并同步全部相关方
- 委派任务必须自包含：目标、精确文件路径、上下文、可碰/不可碰边界、验收标准

## 验证标准（不接受"我觉得好了"）
- 代码：测试实跑通过（贴结果摘要）；契约级变更必须跑 conformance suite
- 平台行为：实机验证 + 证据落 `evidence/`（日志/截图/导出物）
- 文档：中文、表格优先、字段英文；与 plan-v3 冲突时以 plan-v3 为准并标【待定】

## 仓库纪律
- 已授权自主 commit + push main；关键节点（契约冻结、Phase 出口）由主控打 tag
- commit message：`<type>(<scope>): 中文摘要`，type ∈ feat/fix/docs/chore/test
- **密钥永不入库**；`.env*` 已 gitignore；发现误提交立即报告主控
- 不做 git rebase/reset/force-push 等破坏性操作

## 发现即回写
- 平台新行为/新坑 → `wiki/platform-agentteams.md`（重大缺陷另存 `evidence/spike/`）
- 契约歧义 → `contracts/OPEN-QUESTIONS.md`
- 环境变化 → `wiki/environment.md`

## 禁止事项
- 不发明 plan-v3 没有的架构决策；不引入仓库没有的依赖（先查再报主控）
- 不 mock LLM 调用（demo-app 全真实 StepFun）；不把 audit.jsonl 当权威源
- 不做"置信≥0.8"式未定义指标；归因输出必须 Δ+95%CI+三态
