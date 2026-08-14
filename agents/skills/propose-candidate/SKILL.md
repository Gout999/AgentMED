---
name: propose-candidate
description: Draft a repair candidate (prompt/config patch) and bind it to a WorkOrder. Used by the repairer.
assign_when: Attribution has identified the broken layer and a fix is draftable.
---

# propose-candidate（caseloop 版）

修复师专用。产候选补丁，绑定 WorkOrder，落 Gate——**不发布**（出口 2）。

## 工具（release-admin 投影）

- `versionset.list` / `versionset.get`：读当前绑定版本；
- `candidate.create`：登记候选（diff/描述 + digest）；
- `workorder.draft` / `workorder.freeze`：起草并冻结工作单（hash 绑定）；
- `workorder.get` / `release.get`：查状态（只读）。

## 范围（owner 授权）

- 只改 agent-station 仓库的 prompt/scenario 资产；不碰平台脚本与核心代码；
- git 改动走 `git-delegation`（.processing 标记）；
- 候选必须引用归因实验的证据（case_id + 实验 run id）。
