# 变异巡检报告 patrol_69ddd85d672a49acb60e
- 运行时间：2026-08-07T19:13:00.025135Z
- 变异用例：11（检出 7 / 漏检 4 / 未生效排除 0 / 基线失效排除 0）
- **检出率（生效用例口径）：63.64%**

| 算子 | 层 | 目标探针 | 检出 | 生效 |
|------|----|---------|------|------|
| prompt-deny-refund | prompt | cs-001 | ✓ | ✓ |
| prompt-deny-refund | prompt | cs-002 | ✓ | ✓ |
| prompt-remove-keyword | prompt | cs-004 | ✓ | ✓ |
| prompt-remove-keyword | prompt | cs-005 | ✓ | ✓ |
| prompt-flip-shipping | prompt | cs-003 | ✗ | ✓ |
| prompt-break-shipping | prompt | cs-013 | ✗ | ✓ |
| kb-outdate-battery | kb | cs-006 | ✓ | ✓ |
| kb-wrong-capacity | kb | cs-009 | ✓ | ✓ |
| param-high-temperature | model_params | cs-010 | ✗ | ✓ |
| param-high-temperature | model_params | cs-012 | ✗ | ✓ |
| param-cap-max-tokens | model_params | cs-012 | ✓ | ✓ |