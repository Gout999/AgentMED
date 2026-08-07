# Spike 发现 S0-001：Team 删除假成功 + Leader detach 卡死（dev controller）

> 日期：2026-08-07 ｜ 环境：本机 AgentTeams controller `dev`（embedded 模式）
> 结论：**主计划 §2.2 的预判成立**——「删除失败部分 non-fatal，不能以 CR 消失为成功依据」。
> 此发现直接支撑 Caseload Controller「资源凭证对账」设计，并可整理为上游 issue。

## 现象

1. `agt delete team s0-revision-team` 返回 `team/s0-revision-team deleted`，
   但 `agt get teams` 中团队仍为 `Active`——**CLI 假成功**。
2. REST `DELETE /api/v1/teams/{name}`（带 bearer token）返回空 body，同样无效。
3. controller error log（`/var/log/agentteams/agentteams-controller-error.log`）显示 reconcile 反复失败：
   `detach Team member "s0-triage": restore Manager to Worker personal room:
   invite @manager@… to !MmI4Li936NjhGLv1fR…: HTTP 403 M_FORBIDDEN:
   cannot invite user that is joined or banned`
   —— 即 **invite 非幂等**：目标用户已在房间时 controller 把整个删除流程判死。

## 已验证的事实

- 非 Leader 成员可通过 `PUT /api/v1/teams/{name}`（workerMembers 只留 leader）正常摘除，
  摘除后 worker 可正常 `agt delete worker`（kine tombstone 正常落库）。s0-patch/s0-verify 已删。
- `PUT` 不允许清空 workerMembers（校验"必须有且仅有一个 team_leader"）。
- Leader（最后一个成员）的 detach 必经"restore Manager to personal room"步骤，该步骤在
  @manager 已 joined 时 403 → 删除永远卡死。手动把 @manager 改为 leave 后仍报同一错误
  （controller 的 invite 无条件执行，不检查现状，也不容忍"已是目标状态"）。
- 存储层是 kine（SQLite `/data/agentteams-controller/agentteams.db`），
  key 形如 `/registry/agentteams.io/teams/default/<name>`。

## 影响与对策（写入设计）

1. Caseload Controller 的 drain/remove 流程**必须**做资源对账：删 CR 后回查
   `agt get teams/workers` + docker 容器 + Matrix 房间 + MinIO 用户四样齐全才算成功。
2. 摘出 Team 的顺序：先 PUT 移除全部普通 worker → 再处理 leader（leader 摘除是当前上游死结，
   需手工 Matrix 干预或等上游修复）。
3. 上游 issue 素材：team_controller.go 的 detach 应对 invite 做幂等处理
   （M_FORBIDDEN joined/banned 视为成功），并把单步失败降级为告警而非阻断删除。

## 现场处置

- s0-patch / s0-verify：已删（CR + 容器均消失）。
- s0-triage（leader）：CR 与 team 残留（删不动），容器已 `docker stop` 释放资源。
  残留 CR 不影响后续 caseloop team 创建（名字不同）。

## 证据文件

- `finding-team-delete-403.log`：controller 错误日志摘录
- `team-spec.json`：删除前团队 spec（REST GET）
- `team-before-delete.json`：REST 401 证明（token 要求）
