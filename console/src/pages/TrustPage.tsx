import { Card } from "../components/Card";
import { DataPending } from "../components/DataPending";

/**
 * 门禁与信任：信任账本网格按 risk_class × action_type 展示 Wilson 下界 + 自治状态。
 * 该数据源（eval 门禁报告 / trust_ledger）在 control-plane 侧无 REST 读端点，均标注「数据待接入」
 * （见 console/OPEN-ISSUES.md #2 #7）；现环境 trust_ledger 表为空（真实空）。
 */

const RISK_CLASSES = [
  { key: "R0_READ", label: "R0 只读" },
  { key: "R1_REVERSIBLE_WRITE", label: "R1 可逆写" },
  { key: "R2_HIGH_IMPACT", label: "R2 高影响" },
];

/** R1 白名单动作（trust_ledger/ledger.py DEFAULT_R1_WHITELIST，真实默认值） */
const WHITELIST_ACTIONS = ["case.triage", "workorder.draft.prompt", "notification.reply_origin"];

export function TrustPage() {
  return (
    <div className="space-y-6">
      {/* 门禁报告列表（双轨） */}
      <Card
        title="门禁报告"
        extra={<DataPending issue="eval/gate 数据在 mcp_* 表，control-plane 无 REST（OPEN-ISSUES #7）" />}
        bodyClassName="p-4"
      >
        <p className="mb-4 max-w-2xl text-xs leading-relaxed text-gray-400">
          双轨门禁（spec §9.5）：规则轨（确定性检查）与裁判轨（LLM 裁判，裁判模型 digest ≠ 运动员模型 digest）分开报告，
          另附确定性测试与 live-provider E2E。控制面未暴露门禁报告读端点。
        </p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                <th className="pb-2 pr-4 font-medium">报告 ID</th>
                <th className="pb-2 pr-4 font-medium">规则轨</th>
                <th className="pb-2 pr-4 font-medium">裁判轨</th>
                <th className="pb-2 pr-4 font-medium">确定性测试</th>
                <th className="pb-2 pr-4 font-medium">live E2E</th>
                <th className="pb-2 pr-4 font-medium">总结论</th>
              </tr>
            </thead>
            <tbody>
              {[0, 1, 2].map((i) => (
                <tr key={i} className="border-b border-gray-50">
                  <td className="py-2.5 pr-4 font-mono text-xs text-gray-400">gate_…待接入</td>
                  <td className="py-2.5 pr-4 text-xs text-gray-400">—</td>
                  <td className="py-2.5 pr-4 text-xs text-gray-400">—</td>
                  <td className="py-2.5 pr-4 text-xs text-gray-400">—</td>
                  <td className="py-2.5 pr-4 text-xs text-gray-400">—</td>
                  <td className="py-2.5 pr-4 text-xs text-gray-400">—</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-gray-400">
          现环境 changeset 均引用 <code className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[11px]">eval://eval_…</code> 门禁引用，
          报告本体存于 mcp-eval-runner，待接入后按双轨分列渲染。
        </p>
      </Card>

      {/* 信任账本网格 */}
      <Card
        title="信任账本网格"
        extra={<DataPending issue="trust_ledger 表在库但无 REST 读端点（OPEN-ISSUES #2）" />}
        bodyClassName="p-4"
      >
        <p className="mb-4 max-w-2xl text-xs leading-relaxed text-gray-400">
          「信任是挣来的」：一次动作 = 一个样本，epoch 原始整数计数；Wilson 双侧 95% 下界 &gt; 0.9 且白名单 R1 才可提请晋升。
          现环境 trust_ledger 表为空（真实空）。
        </p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                <th className="pb-2 pr-4 font-medium">risk_class</th>
                {WHITELIST_ACTIONS.map((a) => (
                  <th key={a} className="pb-2 pr-4 font-mono font-medium">{a}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {RISK_CLASSES.map((rc) => (
                <tr key={rc.key} className="border-b border-gray-50">
                  <td className="py-3 pr-4">
                    <span className="text-xs font-medium text-gray-700">{rc.label}</span>
                    <span className="ml-1.5 font-mono text-[10px] text-gray-400">{rc.key}</span>
                  </td>
                  {WHITELIST_ACTIONS.map((a) => (
                    <td key={a} className="py-3 pr-4">
                      <div className="flex flex-col gap-1">
                        <span className="font-mono text-xs tabular-nums text-gray-500">LB 待接入</span>
                        <span className="text-[10px] text-gray-400">—</span>
                      </div>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* 拒绝晋升事件记录 */}
      <Card
        title="拒绝晋升事件记录"
        extra={<DataPending issue="audit trust.promotion_rejected 无 REST 读端点（OPEN-ISSUES #2）" />}
        bodyClassName="p-4"
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                <th className="pb-2 pr-4 font-medium">时间</th>
                <th className="pb-2 pr-4 font-medium">action_type</th>
                <th className="pb-2 pr-4 font-medium">risk_class</th>
                <th className="pb-2 pr-4 font-medium">拒绝原因</th>
                <th className="pb-2 pr-4 font-medium">计数</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-gray-50">
                <td colSpan={5} className="py-6 text-center text-xs text-gray-400">
                  暂无拒绝晋升记录（MVP 口径：3/3 → Wilson 下界 ≈ 0.4385 &lt; 0.9 → 记账但拒绝晋升）
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
