# S0-007 freeze 502 根因事件：macOS 系统代理劫持 httpx localhost 调用

日期：2026-08-08（10:13-10:20 UTC）｜状态：已修复并验证｜修复提交：mcp-servers/common/http.py +trust_env=False

## 表象

守门员对 wo_01KZGCM2VPC9KJK51DVJD1YDA5（真 digest 重建版）执行 `workorder.freeze`，
连续 3+ 次 `502 DEPENDENCY_UNAVAILABLE`（"release controller unreachable"），WorkOrder 卡 DRAFT。
但操作员直接 `curl POST /v1/workorders` 控制面正常返回 422 validation_failed——控制面活着。

## 排查链（五步定位）

1. **MCP 日志**：`HTTP Request: POST http://127.0.0.1:18090/v1/workorders "HTTP/1.1 502 Bad Gateway"`——
   MCP server 到控制面的请求收到了 502。
2. **控制面容器日志**：全部历史里**没有任何一条** 502 的 POST /v1/workorders
   （只有我手动 curl 的 422 和 404）——请求根本没到达控制面进程。
3. **直 curl 正常 + httpx 复现 502**：同一台宿主机，curl → 200/422，httpx → 502。
   差异锁定在 HTTP 客户端的代理行为。
4. **根因**：`scutil --proxy` 显示 macOS 系统代理开启（HTTP/HTTPS/SOCKS → 127.0.0.1:7892）。
   httpx 默认 `trust_env=True` → `urllib.request.getproxies()` **在 macOS 上会回退读系统偏好代理**
   （即使进程无任何 proxy 环境变量）。系统代理的 ExceptionsList 虽有 `127.*`，
   但 getproxies→httpx 的 no_proxy 通配映射不覆盖该写法，localhost 请求被送往代理。
5. **代理实证**：`curl -x http://127.0.0.1:7892 http://127.0.0.1:18090/healthz` → **HTTP 502**。
   该代理（Clash 类，端口 7892）对 loopback 目标一律回 502 Bad Gateway——502 是代理生成的，
   不是任何 CaseLoop 组件生成的。

## 修复

- `mcp-servers/common/http.py` `HttpClient._request`：`httpx.request(..., trust_env=False)`。
  本机服务间调用永远不应穿系统代理。全仓仅此一处 httpx 调用点（已 grep 确认）。
- 重启全部 5 个 MCP server（case_admin/release_admin/eval_runner/notification/casebase_knowledge）。
- 验证：`mcp_client.py 8002 workorder.freeze` → `{"ok": true, "status": "FROZEN",
  "hash": "70200559b72a64b54b7c42d98b30add7a36765d09d147dfd5ce89e9517d6864b"}`，
  控制面 workorders 表已登记（registered=false，非重复）。

## 操作员手册新增（地雷 #9）

- **本机一切 httpx/python 客户端调 localhost 都要防系统代理**：跑 `mcp_client.py` 前缀
  `NO_PROXY='*' no_proxy='*'`；写新客户端要么 `trust_env=False` 要么显式 `NO_PROXY`。
- curl 不受影响（curl 只读环境变量、不读 macOS 系统代理）——**curl 通 ≠ httpx 通**，
  排查连通性问题必须两类客户端都试。
- 502 Bad Gateway 在本架构里不来自任何 CaseLoop 组件：FastAPI 只在 `quality_api_error`
  时映射 502（releases.py:50），uvicorn 自身不产生 502。看到 502 先怀疑中间层（代理）。

## 对发现 #8 的更正（重要）

发现 #8（17:50）把修复师伪造 digest WorkOrder 的 `quality_api_error 502` 归因为
"平台绑定层对账 live digest 拦截成功"——**该归因不成立**。当时控制面日志同样没有对应请求到达
记录，两次 502 同为代理所致。**"门禁绑定层能否拦截伪造 digest"这个判别力实验至今无有效数据**，
实验未被真正执行过。待办：用伪造 digest 重跑一次 workorder.draft/freeze（代理已修复），
观察绑定层行为——并入 T6c 泛化用例时一并做。

## 行为观察附注

- 守门员/修复师的"宣告≠执行"模式本次未再出现：守门员在 freeze 连续 502 后正确地把
  WorkOrder 留在 DRAFT、没有谎报 FROZEN，也没有伪造 hash 绕过——**失败时诚实**，
  这是与修复师（伪造 digest）的关键行为差异，记入信任账本输入。
