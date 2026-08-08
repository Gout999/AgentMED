# CaseLoop 交接状态（2026-08-08 早）

> 给接下来接手的队友。读完这份就能干活，不用翻聊天记录。
> 维护规则：谁改了状态谁更新这份文件，别让它烂掉。

## 一句话现状

Phase 1 纵切**已完成并亲验**：从用户投诉到修复上线的全链路，用三个真实案件跑通了闭环，
其中两个是专门的泛化测试（防"大号状态机"质疑）。代码、证据、测试都已推上 main。

## 怎么把环境跑起来

```bash
# 1. 业务栈（demo-app + postgres + control-plane + console）
cd deploy && docker compose up -d
# console 在 http://127.0.0.1:8088，control-plane API 在 :18090，demo-app 在 :8080

# 2. AgentTeams 平台（agent 编排层）：装法见 deploy/README.md，密钥找 xiejiachen 要
# 3. demo-app 复位到干净基线（改故障前必做）
bash demo-app/scripts/reset_state.sh
```

StepFun key 在 xiejiachen 的 Kimi workspace 环境变量里（PRO 套餐，额度管够），
模型用 `step-3.7-flash`。**不要把 key 写进任何 git 跟踪的文件**。

## 昨晚证明了什么（都有证据，可复查）

| 案件 | 是什么 | 结果 | 证据 |
|------|--------|------|------|
| T6b | 售后政策自相矛盾（prompt 层故障） | 全链路首通 | `evidence/phase1/T6b-release-approval-apply-verify.md` |
| T6c-A | 同一故障，**换剧本外的措辞**投诉 | 闭环，0 次纠偏 | `evidence/phase1/t6c-a-offwording-b1.md` |
| T6c-B | **系统没见过的故障层**（KB 知识库） | 归因正确指向 KB，闭环 | `evidence/phase1/t6c-b-b2-kb-layer.md` |

关键数字：3 次归因实验全部 ATTRIBUTED 且 payload 亲验全真；修复前后 digest 翻转真实
（T6c-A `81122ca0→b469e958`，T6c-B `4aa0bcc1→5df39e2d`）；信任账本 3/3 全对仍
Wilson 下界 0.4385 < 0.9 → **拒绝晋升**（设计预期，演示重点）。

测试基线：mcp 50 + control-plane 105 + conformance 39 + eval unit 62 = **256 绿**。

## 接下来做什么（按优先级）

1. **飞书真接入**：现在是 mock + Matrix 房间代发（@caseloop-approver）。xiejiachen 在搞
   飞书应用凭证，拿到后替换 feishu mock 通道。
2. **G1–G10 平台缺口**：清单和修法在 `wiki/build-guide.md`，优先做 G9（账本工具化）、
   G7（release noop-close）、G8（case.close）——这三个昨晚亲手绊过我们。
3. **Phase 2**：B3（模型参数漂移）、B4（多因素）+ 弹性扩缩。先别动，等 Phase 1 演示完。
4. **裁判模型替换**：现在门禁的 LLM 裁判也用 StepFun（运动员裁判同家，演示可接受，
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

## 关键位置速查

- 权威蓝图：`docs/plan-v3.md` ｜ PRD：`docs/prd.md` ｜ 施工 wiki：`wiki/INDEX.md`
- 房间日志（agent 协作实录）：`evidence/phase1/e2e-t6c-room-log-final.json`（151 条）
- 控制台截图：`evidence/phase1/console-cases-t6c.png`
- Matrix 房间：`!sxPUX2qmXTlXmG5WL3:matrix-local.agentteams.io:18080`
- 账本调用方式：账本是库不是工具，用 `mcp-servers/.venv` 内联脚本调
  TrustLedgerService（库 `control_plane`，连接串问 xiejiachen）

## 纪律（昨晚定的，继续有效）

- 主控（Kimi）只规划和验收，施工委派给 Claude Code / Grok；小修小补可自做。
- agent 交付一律亲验后才算数，证据落 `evidence/`。
- StepFun RPM 纪律：8–10，串行调，别并发猛打。
