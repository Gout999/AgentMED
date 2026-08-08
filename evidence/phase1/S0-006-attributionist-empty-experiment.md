# S0-006 e2e 实战发现：归因师空实验事件簇（2026-08-08 09:22-09:35）

## 现象
T6b e2e B1 全闭环中，归因师（attributionist）接受归因任务后：
1. 09:22 声明"调用 experiment.plan，使用真实 digest 作为 version_refs"
2. 实际冻结的 protocol_frozen（事件 seq 2）versions={} discovery=[] hidden=[] controls=[] **全空**
3. 09:26 启动空实验后只轮询 `GET /v1/experiments/{id}`（每 2-3s 一次），从未调用 `experiment.run`
4. 实验 RUNNING 但永远不会前进——runner 就是 agent 自己，架构里没有隐形执行者

## 根因簇（四个独立缺口叠加）
- **agent 行为**：未校验 protocol_frozen 返回值内容是否符合自己声明的意图（说用真 digest，实际空）；对架构心智模型错误（以为 start 后有 executor）
- **网关校验缺口**：日志 `Tool 'experiment-plan' not listed, no validation will be performed`——网关对工具名映射（experiment.plan→experiment-plan）不认识，跳过 schema 校验，空参数放行
- **控制面域校验缺口**：experiment.protocol_frozen 接受空 cells，无最小领域约束（discovery 非空、versions≥2、hypothesis_layer 必填）
- **观测缺口**：agent 轮询空转无背压/无超时告警，烧 RPM

## 处置
人工（caseloop-approver）房间纠偏：指出证据、要求作废空实验、重冻探针+真实 5-cell+真正 experiment.run。
观察点：agent 能否收到诊断后自主恢复（反剧本终考的关键样本）。

## 后续修复候选（e2e 收尾时评）
- control-plane: protocol_frozen 加领域校验（非空 cells）→ 400
- 网关: 工具名映射修正 + 未列出工具默认拒绝而非放行
- agents 定义: attributionist SOUL 补"start 前必须回读 frozen 内容核对"条款

## 隔离测试（09:48，主控亲自）
路径：mcp_client.py → :8003 experiment.plan + probe.freeze（probe_set 顶层平铺四键）。
结果：**平台完全正常**，事件完整落库（exp_01KZFGX7RZNK6RJ9NEJF4FJ4TZ 的 protocol_frozen，discovery/hidden/controls/versions 全对）。
结论：空 cells = 归因师构造的 probe_set 顶层键名不对（疑似多套一层嵌套）。 digest 每次不同证明它传了内容，但键名错位导致 .get() 全空。

## 根因簇修正（隔离后精确定位）
1. agent 行为：probe_set 参数结构错误 ×3 次重复同一错误；不校验冻结结果
2. **probe.freeze 零结构校验**：任意 dict 都收，缺键默认空 → 空实验静默冻结（平台校验缺口，最该修）
3. **experiment.plan 的 version_refs 参数被静默丢弃**（签名有、实现不用，误导性契约）
4. agent 心智模型：以为 start 后有隐形 executor（第一次）；纠偏后已学会 experiment.run

## 副产物
exp_01KZFGX7RZNK6RJ9NEJF4FJ4TZ（PROTOCOL_FROZEN，replay 案件上的隔离测试残留，不会启动）

## 终局根因（09:55 定位，e2e 最大发现）
eval MCP 工具面残缺：experiment.run 只是状态翻转（POST /start），**全平台没有任何工具能执行探针、
记录 cells（/cells 端点存在但无 MCP 封装）、提交 verdict**。归因师的"轮询"行为不是心智模型错误——
是它在找不到执行工具时的合理 fallback。agent 的直觉对了，平台没跟上。
→ 修复立项 S0-006-fix-brief.md：experiment.execute 异步执行工具（后台线程跑 eval-harness
ExperimentRunner，cells 流式落库，verdict 收官）+ probe.freeze 结构校验 + plan 契约对齐 +
控制面纵深 400 + 归因师 SOUL 两条款。已派发 Claude Code 施工。

## 反剧本观察注记
归因师三次空实验显示同一错误重复（不会自检参数结构），但收到"冻结后回读"提示后第三次
cells 结构转正确（学习发生）；versions 仍空（因 plan 的 version_refs 被平台静默丢弃，非 agent 过错）。
人工纠偏→agent 行为修正的闭环有效，但平台校验缺位时 agent 错误成本极高——校验即教鞭。

## 次要发现 #5（10:03）：错误卫生
归因师撞 StepFun RPM 后把原始错误 `Error: Model quota exceeded: step-3.7-flash
(Details: /tmp/copaw_query_error_xxx.json)` 直接贴进房间——内部路径/错误结构外泄，
房间沦为日志流。改进候选：copaw channel 对模型错误做用户可读包装（"配额暂时超限，稍后自动重试"），
或 worker 配置静默重试不上墙。

## 追加发现（11:50，URL 修复后）
- **step_plan URL 根因**：.env/controller/compose 全部指 `api.stepfun.com/v1`（计费端点，账户无余额→quota_exceeded），
  PRO 套餐在 `api.stepfun.com/step_plan/v1`。控制台零消耗=套餐从未被调用。三处已切（.env /
  controller 重建 / compose 89ea667），worker 链路实测恢复。
- **conformance 39 测后遗**：复跑后 demo-app active versionset 被生命周期测试留在 v-test-rb-v1
  （step-2-16k 套餐端点不存在该模型→chat 兜底 model_not_found）。reset_state.sh 恢复+重注 B1。
  改进候选：conformance 套件收尾自动 reset_state（当前靠人记）。
- **探针发现缺口**：归因师 RBAC（eval-runner+case-admin）无探针清单工具；contracts 冻结探针
  ID（cs-001~016）只能靠主控喂。改进候选：eval-runner 加 probe.list 工具（自描述执行清单）。

## 追加发现 #6（12:15）：宣告≠执行模式
质量官 12:05 发消息"我现在创建 repair project 并委派给 repairer"，随后 loop 终止，
10 分钟内 repairer 零活动、零委派发生。这是继归因师"说用真 digest 实际传空"之后第二次
观察到的 say-do 鸿沟：agent 把"宣告意图"当"完成动作"。纠偏消息已发。
反剧本终考注记：此类 agent 需要"交付物存在性"外部校验（taskflow check_task 就是正确机制，
质量官自己验证归因师时用过，但不会对自己用）。

## 追加发现 #7（17:35）：heartbeat 抑制域工作 + 候选 digest 伪造
- 质量官新容器醒来先收 Team Leader Heartbeat（"Do not do domain work / do not assign tasks"），
  随后 8 分钟 loop 静默结束，未执行主控的门禁推进指令——heartbeat 的"不做域工作"指令
  疑似压制了域驱动。改进候选：heartbeat 与域消息分通道优先级，或 heartbeat 指令限定
  "仅本条心跳回合不做域工作"。
- worker JWT 1h TTL 过期后 `agt` 401，控制器不自动重铸（4 小时无人管）；手工恢复路径：
  docker rm -f worker 容器 → 控制器 reconcile 异步重建（~2-5 分钟）→ 新 token 生效。
  改进候选：controller 周期重铸或 worker sidecar 自刷。
- 修复师候选 WorkOrder 结构优秀，但**两个 digest 是模式补全的伪造值**（drifted
  81122ca0d4e5… vs 真实 81122ca0b3b2…；baseline b469e9587e3c… vs 真实 b469e9589eab…）。
  未提示守门员——作为门禁判别力的实战测试：规则轨若能对账 live digest 应拦截。

## 追加发现 #8（17:50）：伪造 digest 的双层拦截实验结果
- 门禁规则轨：对伪造 digest **放行**（hash_binding 只查内部一致性，不对账 live）——判别力缺口，
  改进候选：规则轨加"digest 必须存在于 live 观测/版本集内容"校验。
- 平台绑定层：**拦截成功**（workorder.draft → quality API 版本集绑定校验失败 → quality_api_error 502）。
  防线在，但位置靠后（草案阶段才爆），且错误表象（502 Bad Gateway）与根因（digest 不存在）
  严重脱节，排查成本高。改进候选：quality 绑定失败返回 422+具体 digest 不匹配详情。
- judge_track 的裁判/运动员 digest 为模板占位值（sha256:9999…/8888…），非真实模型 digest，
  "裁判≠运动员"检查形同虚设。改进候选：gate 报告填真实模型 digest（live config 可查）。
