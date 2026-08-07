# Spike 发现 S0-003：artifact 交接的官方机制与路径语义

> 日期：2026-08-07 ｜ 环境：AgentTeams v1.2.1（copaw 运行时）
> 结论：**跨 worker 交接必须走 task 生命周期同步，不能直接写"shared/任意路径"**。

## 实测过程

1. 让 spike-worker-a 写 `shared/spike/handoff.json`（非任务上下文）→
   文件落在**它自己的 MinIO workspace**
   （`agents/spike-worker-a/.copaw/workspaces/default/shared/spike/`），其他 worker 不可见。
2. spike-worker-b 读同路径 → 读不到，本地为空，filesync pull 失败。

## 官方机制（worker 内置 `file-sharing` Skill 文档实锤）

- **任务生命周期内**：`taskflow(action="ack_task")` / `taskflow(action="submit_task")`
  内部自动完成 pull/push/stat——交接文件放 `shared/tasks/{task-id}/`，
  项目只读上下文放 `shared/projects/{project-id}/`。
- **任务生命周期外**：读共享文件前显式 `filesync(action="pull", path="shared/.../")`；
  推中间产物 `filesync(action="push", path="shared/tasks/{task-id}/progress/")`。
- **禁止**在消息里暴露存储内部路径（`agentteams/agentteams-storage/...`、`teams/{team}/shared/...`、
  `/root/agentteams-fs/...`）——一律用本地相对路径 `shared/...`。
- 找不到文件时：先 pull → 查 pwd/本地路径 → 仍缺则 @协调者报 BLOCKED 并附 filesync 结果，
  不要自己造目录。

## 对 caseloop SOUL 的设计约束

- 每个 Agent 的 SOUL 必须写明：产物一律写 `shared/tasks/{task-id}/`，
  交接经由 taskflow ack/submit 自动同步；房间内只传「路径 + 摘要」。
- 控制面（Case Controller）派单若绕过 leader 的 delegate_task，
  需要保证 task-id 与共享目录约定一致，否则 worker 间互相看不到文件。
- leader 修正路径的实测：重新分派时把交付物改为 `shared/tasks/{task-id}/handoff.json`，
  worker 能正确理解（本 spike 已验证指令可修正）。

## 附带发现

- 自定义 Skill 推送 `agents/<worker>/skills/<skill>/SKILL.md` 到 MinIO 后，
  会自动同步进容器 `/root/.copaw-worker/<name>/skills/`（spike-echo 已确认出现）。
- worker 容器内 `copaw-sync` 指向缺失的 `file-sync` skill 脚本（v1.2.1 镜像小瑕疵，
  但 file-sharing 文档说任务生命周期不需要直接调 filesync，影响有限）。
- bucket 根存在 `teams/<team>/shared/`（团队空间，建 team 时自动创建），
  但 worker 的 MinIO policy 未含该前缀【访问权限待验证】。
