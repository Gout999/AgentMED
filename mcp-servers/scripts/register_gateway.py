"""Register the 12 agentmed MCP projections on the Higress gateway
via the console API (platform-native mcp-proxy mechanism)."""
import json
import os
import pathlib
import urllib.request
import http.cookiejar

CONSOLE = "http://127.0.0.1:18001"
ROOT = pathlib.Path(__file__).resolve().parent.parent

SERVERS = [
    ("mcp-agentmed-admin-quality-officer", 8101, ["worker-quality-officer"]),
    ("mcp-agentmed-admin-collector", 8201, ["worker-collector"]),
    ("mcp-agentmed-admin-case-officer", 8301, ["worker-case-officer"]),
    ("mcp-agentmed-admin-attributionist", 8401, ["worker-attributionist"]),
    ("mcp-agentmed-admin-repairer", 8501, ["worker-repairer"]),
    ("mcp-agentmed-release-gatekeeper", 8102, ["worker-gatekeeper"]),
    ("mcp-agentmed-release-repairer", 8202, ["worker-repairer"]),
    ("mcp-agentmed-eval-gatekeeper", 8103, ["worker-gatekeeper"]),
    ("mcp-agentmed-eval-attributionist", 8203, ["worker-attributionist"]),
    ("mcp-agentmed-notify-quality-officer", 8104, ["worker-quality-officer"]),
    ("mcp-agentmed-notify-case-officer", 8204, ["worker-case-officer"]),
    ("mcp-agentmed-casebase-knowledge", 8005, ["worker-case-officer"]),
]


def call(opener, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        CONSOLE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:300]


def main():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    console_password = os.environ.get("HIGRESS_CONSOLE_PASSWORD", "")
    if not console_password:
        raise SystemExit("缺少 HIGRESS_CONSOLE_PASSWORD（Higress 控制台 admin 口令）")
    status, body = call(opener, "POST", "/session/login",
                        {"username": "admin", "password": console_password})
    print("login:", status)

    for name, port, consumers in SERVERS:
        token = (ROOT / "var" / "tokens" / (name + ".token")).read_text().strip()
        # 1) service source
        status, _ = call(opener, "POST", "/v1/service-sources", {
            "type": "dns", "name": name + "-proxy",
            "domain": "host.docker.internal", "port": port, "protocol": "http",
        })
        # 2) mcpServer with mcp-proxy raw config
        yaml_cfg = (
            "server:\n"
            "  name: " + name + "\n"
            "  config:\n"
            "    type: mcp-proxy\n"
            "    transport: http\n"
            "    mcpServerURL: http://host.docker.internal:" + str(port) + "/mcp\n"
            "    timeout: 120\n"
            "  securitySchemes:\n"
            "  - id: UpstreamAuth0\n"
            "    type: apiKey\n"
            "    in: header\n"
            "    name: X-AgentMED-Gateway-Token\n"
            "    defaultCredential: \"" + token + "\"\n"
            "  defaultUpstreamSecurity:\n"
            "    id: UpstreamAuth0\n"
        )
        allowed = ["manager"] + consumers
        mcp_body = {
            "name": name,
            "description": name + " MCP Proxy Server (http)",
            "type": "OPEN_API",
            "rawConfigurations": yaml_cfg,
            "mcpServerName": name,
            "domains": ["aigw-local.agentteams.io"],
            "services": [{"name": name + "-proxy.dns", "port": port, "weight": 100}],
            "consumerAuthInfo": {"type": "key-auth", "enable": True,
                                   "allowedConsumers": allowed},
        }
        status, body = call(opener, "PUT", "/v1/mcpServer", mcp_body)
        print(name, "->", status, body[:120])


if __name__ == "__main__":
    main()
