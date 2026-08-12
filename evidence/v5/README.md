# V5 evidence index

本页只导航 evidence bundle，不把 worktree 测试结果升级为 stage completion。

| Stage/slice | Bundle | 状态 | Subject commit | 当前用途 |
|---|---|---|---|---|
| V5-R0 Documentation Authority | [`r0docs_20260811T104032Z_4d15c1c`](stage-0/documentation-authority/r0docs_20260811T104032Z_4d15c1c/) | `PASS` | `4d15c1c81180386fa4852a53f8b8847e74cda050` | clean-checkout authority/archive/provenance closure；runtime facets 全部 `NOT_RUN` |
| V5-D1 Application/Component Lifecycle | [`d1lifecycle_20260811T123512Z_798531a`](decision-gates/d1-application-component-lifecycle/d1lifecycle_20260811T123512Z_798531a/) | `PASS (contract-only)` | `798531af539cd37e797723f2985d55c70fa1046e` | owner selected Option A；D-014 + exact CAS/adversarial contract closure；除 `contract` 外 8 facets `NOT_RUN` |
| V5-R3-full Second VersionSet Runtime | [`r3full_20260812T050342Z_caed0eb`](stage-1/system-version/r3full_20260812T050342Z_caed0eb/) | `PASS (contract + R3 replay)` | `18482f8` | standalone record/get/diff 激活并实现；CAS lineage/幂等/递归验证/tamper fail-closed；disposable PG journey 全过；live facets `NOT_RUN` |
| V5-D2 Complete Version-Graph Contract | [`d2versiongraph_20260812T020335Z_4852664`](decision-gates/d2-complete-version-graph-contract/d2versiongraph_20260812T020335Z_4852664/) | `PASS (contract-only)` | `4852664c60f92e73ee349ec0e7b27e81d84c7b6a4` | Master §17.3：一次冻结 `system-versions.record/get/diff`（FROZEN_FOR_IMPLEMENTATION / NOT_IMPLEMENTED）+ two-VersionSet fixture + 13 负例；未激活、generated 零 diff；除 `contract` 外 8 facets `NOT_RUN` |
| V5-1A Application Catalog | [`run-20260811-v5-1a`](stage-1/application-catalog/run-20260811-v5-1a/) | `NOT ACCEPTED AS CURRENT CLOSURE` | 旧 closure subject | 保留原始历史；不能证明当前 repair |
| V5-1B System Version | [`run-20260811-v5-1b`](stage-1/system-version/run-20260811-v5-1b/) | `NOT ACCEPTED AS CURRENT CLOSURE` | 旧 closure subject | 保留原始历史；不能证明当前 repair |
| V5-1C First System Case | [`run-20260811-v5-1c`](stage-1/first-system-case/run-20260811-v5-1c/) | `NOT ACCEPTED AS CURRENT CLOSURE` | 旧 closure subject | 保留被拒原因和历史 raw logs |

未提交的 remediation 目录不进入本索引，也不能作为 clean-checkout evidence；其结果只能在
所属 runtime stage 形成 semantic subject、manifest 和 post-commit verifier 后追加。

## Stage closure 要求

每个 stage 必须在自己的目录生成 digest-bearing `run-manifest.json`，绑定实际 semantic
commit full hash、dirty 状态、命令/环境、raw artifact digest、逐 facet 状态和 post-commit
verifier。目录存在、verification prose 或 worktree verifier PASS 都不等于 stage `DONE`。

Evidence 不因过期被物理移动或改写。新结果通过新 run id 追加；旧 bundle 用状态和
`superseded_by` 指针说明关系。
