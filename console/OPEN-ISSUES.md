# Console 剩余真实缺口（P0-3）

> T7 原清单 #1–#7 在当时是准确的：env、Trust、events、Experiment full、WorkOrders、Gates
> 尚无可用 REST 投影。朋友的 T8 `9afcefa` 已提供这些读端点，本轮 P0-3 已接入生产页面；
> 静态 digest、固定 `0.00` 和假 Gate 行均已移除。

## 已关闭的 T7 缺口

| 原编号 | 当前结果 |
|---|---|
| #1 demo-app baseline | `/v1/env`；不可达显示 `UNKNOWN`，不再使用固定 digest/绿灯 |
| #2 Trust | `/v1/trust/ledger`、`/v1/trust/denials`；读取 P0-2 权威 ledger/audit |
| #3 Case timeline | `/v1/cases/{id}/events`；显示真实 seq/actor/causation/trace/payload |
| #4 Evidence refs | `/v1/evidence`；只投影通过来源完整性检查的 ref/digest |
| #5 Experiment cells/deltas | `?_view=full`；CI 数据不足时诚实显示 `UNKNOWN` |
| #6 WorkOrder hash/requester | `/v1/workorders`；不可变 WorkOrder 为权威并重验 payload/投影列与 Gate binding；读面不返回完整 payload |
| #7 Gate reports | `/v1/gates`；读取权威 GateReport，未绑定为 `UNBOUND`，损坏或绑定不完整 fail closed 为 `integrity_error/UNKNOWN` |

## 仍未关闭

1. **Evidence artifact store**：控制面只有引用与 digest，没有 artifact fetch/内容校验服务。
   Console 必须继续显示 `artifact UNKNOWN`，不能把可见 URI 当成已验证内容。
2. **Experiment confidence interval 输入**：现有事件没有每臂样本量/方差，无法真实重算 95% CI。
   页面显示 `UNKNOWN`；不得制造 CI 或固定数值。
3. **Live Quality API**：本地 real-stack 测试刻意不启动 demo-app，因此 `/v1/env` 的
   `demo_app=unavailable` 是预期。live-provider 环境需另行凭证与真实 demo-app。
4. **Live Feishu**：Notification 的 `feishu-mock` 只用于明确标注的 contract/replay；真实飞书
   E2E 仍被外部凭证阻塞，Console 不得暗示已经 live 发送。
5. **前端依赖主版本债务**：在保留朋友 React 18 / Router 6 / Vite 5 技术栈的前提下已做同主版本
   安全补丁；当前 npm audit 仍有 4 moderate + 1 high，清零需要 Router/Vite 跨主版本升级，不能混入
   P0-3。Vite real-stack runner 仅绑定 `127.0.0.1`，后续应作为独立升级任务处理。

## 写面边界

Console 保持只读。批准、拒绝、stage、canary、promote、rollback 等动作仍必须经独立审批权威和
Release Controller；不得为了做按钮而让浏览器直接构造授权或调用 Quality API 写面。
