---
name: draft-pr
description: Draft the release-side work order for an approved candidate (exit 3 path). Not used in the verification-only demo (exit 2).
assign_when: A candidate passes gate and a deployable change must be prepared (deferred to exit 3).
---

# draft-pr（agentmed 版）

出口 3（部署）路径的起草面：把通过门禁的候选整理成发布工作单。
当前演示（出口 2：VerifiedCandidate/NOT DEPLOYED）不触发本技能；流程到此为止由人工决定是否进入部署。
