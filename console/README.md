# AgentMED Console

Console 是控制面的只读人类界面。它通过相对路径 `/api/*` 读取 control-plane 投影；浏览器
不持有 Release Controller、Approval Authority 或 V5 credential-issuance 权限。页面显示的
desired、observed、effect、evidence completeness 与 integrity 状态不得互相替代。

## 本地运行

```bash
cd console
npm ci
npm run dev
```

Vite 默认监听 `127.0.0.1:5173`，并把 `/api/*` 转发到 compose 模式的
`http://127.0.0.1:18090`。如果只在宿主机用 8090 启动 control-plane，请先显式调整
`vite.config.ts` 的本地 proxy，不能把 connection failure 当作空数据或 PASS。

## 验证

```bash
npm run test
npm run build
```

`npm run test:integration` 会创建并销毁名称受限的 disposable PostgreSQL database，启动
本地 control-plane 与 Vite；先阅读脚本中的数据库和端口前置条件。真实 provider、飞书和
production 不属于该测试。

## 当前边界

- V1 Case 列表/详情兼容读取 legacy Aggregate 与 QualityCase；V5 readiness/read projection
  遇到 digest、authority、snapshot 或 scalar 不一致时显示 `UNKNOWN/integrity_error`；
- V5-1C confirmed Acceptance 仍等待 V5-4 exact ResolutionContract，因此不能显示 READY；
- Console 保持只读。审批、发布、回滚或外部写必须经过独立 authority/capability 路径；
- 未关闭的 artifact store、CI 输入、live provider 和依赖升级问题见
  [`OPEN-ISSUES.md`](OPEN-ISSUES.md)。

当前项目状态与证据边界见 [`PROJECT_STATE.md`](../docs/context/PROJECT_STATE.md)，文档权威
顺序见 [`docs/README.md`](../docs/README.md)。
