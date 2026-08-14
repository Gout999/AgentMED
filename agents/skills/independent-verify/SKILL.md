---
name: independent-verify
description: Independently verify a repair candidate against the frozen badcase probe. Dual-track gate (rule track + judge track). Used by the gatekeeper.
assign_when: A candidate/workorder is ready for gate verification before human approval.
---

# independent-verify（caseloop 版）

守门员专用。独立复核：候选是否真解决原坏例、是否引入回归。放行结论只属于守门员。

## 工具（eval-runner 投影）

- `gate.run`：坏例探针回放（修前 vs 修后）；
- `gate.run_verification`：确定性复核（digest/规则轨）；
- `gate.report`：双轨报告（规则轨 + 裁判轨，fail-closed）。

## 纪律

- 不信任修复师的自述，只信自己跑出来的观测；
- 修后必须 pass 且修前必须 fail 才可能放行；任一不满足 → NOT DEPLOYED；
- 报告绑定 candidate digest + 探针 digest + 观测对比，可机器复核。
