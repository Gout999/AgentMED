---
name: attribute-skip
description: Attribution adjudication semantics (R1-R5) and the explicit 'skip/unattributable' outcome. Used by the attributionist.
assign_when: An experiment report needs adjudication or a layer cannot be attributed.
---

# attribute-skip（agentmed 版）

归因裁决语义：R1-R5 三态（是/否/无法归因）。证据不足时显式裁决「无法归因」，不硬指认、不编造。
裁决必须引用实验证据（run id + digest），产物落 shared/tasks 并由 leader 汇入 case 时间线。
