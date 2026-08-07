# 本机环境事实（2026-08-07 实测）

## 机器
macOS (Apple Silicon)，Docker Desktop 内存 7.65 GiB（建议保持 ≥8，工人多时吃紧）。
kubectl 已装但无任何集群上下文——AgentTeams 本地嵌入式多容器架构，**不需要 K8s**。

## LLM：StepFun（运动员 + 暂定裁判）
- 端点：`https://api.stepfun.com/v1`（OpenAI 兼容）
- 模型：`step-3.7-flash`（指定；另有 step-3.5-flash 等，不用）
- PRO 会员额度充足；演示确定性：temperature=0 + 冻结探针集
- Key 存放纪律：只存于 `~/Documents/kimi/workspace/ACL-team/.env`（仓库外）、
  Higress 网关侧、`~/agentteams-manager.env`；**仓库内禁止出现**
- 裁判模型暂同 StepFun（用户后续提供异构裁判模型后切换，门禁设计已预留"裁判≠运动员"）

## GitHub
- 仓库：`https://github.com/Gout999/CaseLoop`（owner Gout999）
- 本机 gh 有两个账号：`er-s-an`（对本仓库有权限）、`lijunle853`（无权限，是 active 默认）
- 仓库级 git 配置已就位：`credential.https://github.com/Gout999.helper=osxkeychain`
  + `credential.https://github.com/Gout999.username=er-s-an`（keychain 已存 er-s-an token）
- **用户已授权自主 commit + push（含直接推 main）**；契约冻结等关键节点打 tag

## AgentTeams 平台（v1.2.1，全新安装）
- 安装方式见 `deploy/README.md`；管理员 admin / 密码为本机安装时设定值（不入库）
- 端口：18080 Higress 网关｜18001 Higress 控制台｜18088 Element Web｜18888 Manager UI｜13000 Dashboard
- Matrix 域名 `matrix-local.agentteams.io:18080`；容器内 tuwunel :6167、MinIO :9000、controller REST :8090
- Worker 大脑：全部 step-3.7-flash（用户明确：不用本地 MLX 模型）

## 历史包袱
- `/Users/xiejiachen/zeroops/`：旧方向归档，可复用文档格式/team.yaml 骨架/compose 模式；
  `mcp-servers/common/approval.py`（静态 token 可重放）与 `audit.py`（失败放行）**只参考不沿用，重写**
- 旧 dev controller 已连同卸载一并清除

## 飞书
- 自建应用凭证未就绪（用户办理中）→ Phase 1 用 feishu mock（明示的降级演示路径），
  接口按真凭证设计，凭证到位即切真
