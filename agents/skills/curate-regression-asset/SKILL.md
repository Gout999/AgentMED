---
name: curate-regression-asset
description: Close a Case by curating its badcase/probe into the regression asset base. Used by the case-officer.
assign_when: A Case reaches closure and its repro must be preserved for future gates.
---

# curate-regression-asset（caseloop 版）

案件官收尾：把已验证的坏例与探针沉淀为回归资产（kb.upsert / badcase_search / holdout_get，casebase 投影）。
沉淀内容绑定 case_id + probe digest；不得收录未验证素材。
