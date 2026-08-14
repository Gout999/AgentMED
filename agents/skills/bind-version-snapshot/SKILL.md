---
name: bind-version-snapshot
description: Bind the exact immutable SystemVersionSet to a Case/experiment. Used by attributionist and repairer.
assign_when: Evidence or experiments must reference the exact system version at incident time.
---

# bind-version-snapshot（caseloop 版）

把「出错当时运行的确切版本」绑定进 Case/实验：

- `versionset.list` / `versionset.get`（eval-runner / release-admin 投影）读不可变版本集；
- 实验与候选都必须引用具体 version digest；
- 版本来源缺失时诚实标 UNKNOWN，不猜。
