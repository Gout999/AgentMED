# AgentMED 运行入口（比赛代码包 · 与 docs/competition/agentteams-package.md 对应）
.PHONY: test test-control-plane test-mcp test-harness conformance \
        control-plane projections gateway-register sandbox-verify approval-reader signal-source

PYTHON ?= python3
CONTRACTS_VENV ?= /tmp/caseloop-contracts-venv

# ---- 测试（三套 + 一致性契约）----
test: test-control-plane test-mcp test-harness conformance

test-control-plane:
	cd control-plane && .venv/bin/python -m pytest tests/unit -q

test-mcp:
	cd mcp-servers && .venv/bin/python -m pytest tests/ -q

test-harness:
	cd mcp-servers && .venv/bin/python -m pytest ../eval-harness/tests/unit -q

conformance:
	@test -x $(CONTRACTS_VENV)/bin/pytest || { \
	  $(PYTHON) -m venv $(CONTRACTS_VENV) && \
	  $(CONTRACTS_VENV)/bin/pip install -r contracts/conformance/requirements.txt; \
	}
	$(CONTRACTS_VENV)/bin/pytest contracts/conformance -q -m "not live"

# ---- 运行入口 ----
control-plane:
	cd control-plane && exec .venv/bin/python run_local.py

projections:
	cd mcp-servers && ./scripts/launch-projections.sh

gateway-register:
	cd mcp-servers && .venv/bin/python scripts/register_gateway.py

# 段5 沙箱验证（隔离容器修前/修后对照；样例文件在 var/sandbox/）
sandbox-verify:
	$(PYTHON) scripts/sandbox/runner.py --probe var/sandbox/b1-probe.json \
		--prompt-before var/sandbox/b1-prompt-before.md \
		--prompt-after var/sandbox/b1-prompt-after.md \
		--out var/sandbox/b1-sandbox-evidence.json

# 段6 审批：CLI 发 Matrix 决策 / reader 消费并落 grant
approval-reader:
	cd mcp-servers && .venv/bin/python scripts/approval_reader.py --once

# 段1 信号源（Langfuse 负分 → 立案；幂等）
signal-source:
	$(PYTHON) scripts/langfuse_signal_source.py