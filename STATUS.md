# CaseLoop 交接状态（2026-08-09 晚，PR#1 合并后更新）

> 给接下来接手的队友。读完这份就能干活，不用翻聊天记录。
> 维护规则：谁改了状态谁更新这份文件，别让它烂掉。

## 一句话现状

Phase 1 纵切已完成并亲验，**PR#1「Phase 1 closeout」已合并**（701881f）：门禁/发布授权全面
fail-closed 并绑定 WorkOrder hash，事务性 outbox + 信任账本自动记账接入主循环，Console 接通
权威 T8 读投影，B1 replay 可重复证明入库（证据第三方可复验）。**live B1 仍未完成**——
replay 与 live 证据分开报告，live 在凭证/飞书/人工审批就绪前维持 BLOCKED，这是当前第一优先。

## 怎么把环境跑起来

```bash
# 1. 业务栈（demo-app + postgres + control-plane + console）
cd deploy && docker compose up -d
# console 在 http://127.0.0.1:8088，control-plane API 在 :18090，demo-app 在 :8080
# 注意：合并 PR#1 后 demo-app/control-plane 镜像需重建才是新代码（docker compose up -d --build）

# 2. AgentTeams 平台（agent 编排层）：装法见 deploy/README.md，密钥找 xiejiachen 要
# 3. demo-app 复位到干净基线（改故障前必做）
bash demo-app/scripts/reset_state.sh

# 4. B1 replay 证明（不依赖 live 凭证，任意机器可复验）
make demo-b1-replay
```

StepFun key 在 xiejiachen 的 Kimi workspace 环境变量里（PRO 套餐，额度管够），
模型用 `step-3.7-flash`。**不要把 key 写进任何 git 跟踪的文件**。

## 已证明的东西（都有证据，可复查）

| 案件 | 是什么 | 结果 | 证据 |
|------|--------|------|------|
| T6b | 售后政策自相矛盾（prompt 层故障） | 全链路首通 | `evidence/phase1/T6b-release-approval-apply-verify.md` |
| T6c-A | 同一故障，**换剧本外的措辞**投诉 | 闭环，0 次纠偏 | `evidence/phase1/t6c-a-offwording-b1.md` |
| T6c-B | **系统没见过的故障层**（KB 知识库） | 归因正确指向 KB，闭环 | `evidence/phase1/t6c-b-b2-kb-layer.md` |
| PR#1 P0-1/2/3 | 门禁绑定+outbox 事务+信任记账+console T8 | 主控三路深审+全量测试亲验 | PR #1 review 记录 |
| PR#1 P0-4 | B1 replay 可重复证明（含 4 类伪造注入拒绝） | 校验器第三方复验 verified（22 工件+135 探针） | `evidence/p0/p0-4-b1/VERIFICATION.md` |

关键数字：3 次归因实验全部 ATTRIBUTED 且 payload 亲验全真；修复前后 digest 翻转真实
（T6c-A `81122ca0→b469e958`，T6c-B `4aa0bcc1→5df39e2d`）；信任账本 3/3 全对仍
Wilson 下界 0.4385 < 0.9 → **拒绝晋升**（设计预期，演示重点）。

测试基线（PR#1 合并后）：control-plane 330 + mcp-servers 121 + eval-harness 76 + console 7，
demo-app 46、contracts conformance 43（后两个对**匹配版本的部署**跑；对旧容器跑会挂，
不是回归）。注意 control-plane 新增依赖 `cryptography==45.0.7`，老 venv 要补装。

## 接下来做什么（按优先级）

1. **live B1 收尾（P0-4 live）**：凭证齐了就能跑——StepFun 运动员/裁判异构 key、可达的
   Quality/Control Plane、真实飞书投诉通道、三份新鲜人工 ApprovalGrant、AgentTeams 任务流
   独立签名校验、崩溃恢复边界。入口：`make demo-b1-live`（fail closed，不会回退 replay）。
2. **飞书真接入**：现在是 mock + Matrix 房间代发（@caseloop-approver）。xiejiachen 在搞
   飞书应用凭证，拿到后替换 feishu mock 通道。
3. **缺口清单 G1–G17**：`wiki/build-guide.md`。G5/G8/G9 已被 PR#1 修掉；优先做 G2/G11
   （测试环境污染与 reset 500，天天绊人）、G7 收尾、G3（heartbeat）。
4. **Phase 2**：B3（模型参数漂移）、B4（多因素）+ 弹性扩缩。等 live B1 演示完再动。
5. **裁判模型替换**：现在门禁的 LLM 裁判也用 StepFun（运动员裁判同家，演示可接受，
   评审会问）。xiejiachen 说之后给别的模型 key。

## 必知的坑（每个都踩过，别重蹈）

- **地雷#9**：在本机跑任何 Python 脚本调 localhost 服务，必须前缀 `NO_PROXY='*'`。
  macOS 系统代理（127.0.0.1:7892）会劫持 httpx/requests 的 localhost 调用造成 502。curl 不受影响。
- **G2**：跑完 conformance 或 eval 套件后，**必须** `bash demo-app/scripts/reset_state.sh`，
  否则 demo-app active 版本集残留 v-test-*，下一案取证全污染（昨晚因此作废过一批证据）。
- **worker JWT 1h 过期**：agent 没反应先看这个。恢复：`docker rm -f agentteams-worker-<name>`，
  等控制器 2–5 分钟自动重建。
- **宣告≠执行**：agent 说"我做了"不等于做了。昨晚抓到 3 次（伪造 digest、宣告不执行、
  Markdown 顶替账本）。**验收一律亲验**：查数据库、查 digest、查 API 回读，不信房间里的嘴。
- **测试打错部署**：demo-app integration 和 conformance 套件打 `CASELOOP_QUALITY_API_BASE_URL`
  （默认 :8080 容器）。容器镜像旧、测试新 = 4 个假失败（PR#1 review 实测坐实）。

## 关键位置速查

- 权威蓝图：`docs/plan-v3.md` ｜ PRD：`docs/prd.md` ｜ 施工 wiki：`wiki/INDEX.md`
- 决策记录：`docs/decisions/`（D-002~D-007 已主控 ratified）
- 房间日志（agent 协作实录）：`evidence/phase1/e2e-t6c-room-log-final.json`（151 条）
- B1 replay 证明：`evidence/p0/p0-4-b1/`（VERIFICATION.md + manifest，第三方可复验）
- 控制台截图：`evidence/phase1/console-cases-t6c.png`
- Matrix 房间：`!sxPUX2qmXTlXmG5WL3:matrix-local.agentteams.io:18080`
- 账本调用方式：生产路径=发布完成事件自动记账（PR#1）；旧 trust_ledger 库仅限 contract/replay

## 纪律（昨晚定的，继续有效）

- 主控（Kimi）只规划和验收，施工委派给 Claude Code / Grok；小修小补可自做。
- agent 交付一律亲验后才算数，证据落 `evidence/`。
- StepFun RPM 纪律：8–10，串行调，别并发猛打。
- 契约变更必须主控批准并同步全部相关方（PR#1 已建立 D-xxx 决策记录 + ratified 程序）。
