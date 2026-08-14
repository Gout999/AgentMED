---
name: git-delegation
description: Controlled git changes through the platform git-delegation mechanism (.processing markers). Used by the repairer for candidate patches.
assign_when: A repair candidate needs repository changes in the governed app.
---

# git-delegation（caseloop 版）

修复师经平台 git-delegation 落候选补丁。范围：agent-station 仓库 prompt/scenario 资产。

- 改动前写 `.processing` 标记，完成后移除——每个 git 操作可审计；
- 补丁只落候选分支/记录，不合并主线、不发布（出口 2）；
- 产物与 digest 记入 WorkOrder；不经手任何凭证。
