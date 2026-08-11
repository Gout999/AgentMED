# V5-1B Acceptance Record（独立 DS 验收）

- Stage: V5-1B（ComponentRevision / SystemVersionSet / SystemAssignment /
  trusted manifest import / `caseloop init`）
- Run: `run-20260811-v5-1b`（本目录已有单元/集成/CLI/conformance/console 证据与
  `verification.md`）
- 验收方: 独立 Designated Screener（DS）
- 结论: **PASS**（12/12 攻击面全部通过）
- 验收 commit 基线: `94870a5`（feat(v5): add immutable system versions）+
  `65a93b5`（test(v5): harden v5-1b acceptance，补强验收用）

## 攻击面（12/12 PASS）

独立 DS 按 blueprint V5-1B Verification 逐条核验 12 个攻击面，全部通过：
1. trusted one-shot manifest import ALL_OR_NOTHING（任一构造失败整事务回滚）
2. 幂等 replay（same key / same manifest-digest 双路径不产生第二套记录）
3. bootstrap assignment generation=1 / previous=null / exact attestation
4. VersionSet immutable（4 张表 before_update/before_delete guard）
5. identity assurance discriminator 闭合（IMMUTABLE_DIGEST/PROVIDER_VERSION/
   MUTABLE_ALIAS/OBSERVED_ONLY/UNKNOWN）
6. mutable alias / unknown 不冒充 immutable
7. graph digest 与 exact component revision bindings 精确绑定
8. same label/different digest 语义 diff
9. dependency substitution 语义 diff
10. policy permission expansion 语义 diff
11. discovery root escape / symlink / secret redaction / 重复扫描稳定性
12. 无法可靠识别组件保留 UNKNOWN

## 验收方补测（2 个 diff 向量）

DS 在既有 `_semantic_diff` 单测之外补测 2 个 diff 向量并全部通过：
- 同组件 revision 但 `artifact_refs` 变化 → 仅 ARTIFACT 级别差异，不改变
  version_set_digest 绑定（digest 覆盖的字段集合保持契约一致）；
- 拓扑 revision 相同但 edge 顺序规范化差异 → 同一拓扑 digest（规范化后
  不产生虚假 DIGEST_CHANGED）。

## 非阻塞发现 F1（记录，不阻塞本 stage）

- `configuration_digest` 在请求侧为 `null`（未提供时），持久化为 `[]`（服务端
  默认）——两侧 JSON 形态不一致。
- 影响面：仅当从**持久化 envelope 重算**该字段时才会观察到差异；请求指纹/
  事件 payload 均按各自侧一致处理，不产生二义。
- 处置：由后续 slice 统一归一化（在 ComponentRevision 写入路径上把 null 与 []
  归一为同一规范形态），本 stage 不动（避免改动已验收的 V5-1B 面）。
