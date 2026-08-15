FROM higress-registry.cn-hangzhou.cr.aliyuncs.com/agentteams/agentteams-copaw-worker@sha256:7a6780ef76b6c7b056a2c343eeabc697f70108dae153afe8ddb76a3fad9a41b4

LABEL org.opencontainers.image.title="Agent Station AgentTeams CoPaw acceptance overlay"
LABEL org.opencontainers.image.source="https://github.com/agentscope-ai/AgentTeams"
LABEL io.agent-station.agentteams.commit="849182af8e017168a5a200a87b1062142caf462d"
LABEL io.agent-station.derived-from="sha256:7a6780ef76b6c7b056a2c343eeabc697f70108dae153afe8ddb76a3fad9a41b4"

COPY agent_station_projectflow.py /tmp/agent_station_projectflow.py
COPY s0_deterministic.py /tmp/s0_deterministic.py
COPY patch_hooks_init.py /tmp/patch_hooks_init.py

RUN set -eu; \
    for task_site in \
      /opt/venv/standard/lib/python3.11/site-packages \
      /opt/venv/lite/lib/python3.11/site-packages; do \
      task_tools="$task_site/copaw_worker/hooks/tools"; \
      hooks_init="$task_site/copaw_worker/hooks/__init__.py"; \
      test -f "$task_tools/projectflow.py"; \
      test ! -e "$task_tools/projectflow_upstream.py"; \
      mv "$task_tools/projectflow.py" "$task_tools/projectflow_upstream.py"; \
      cp /tmp/agent_station_projectflow.py "$task_tools/projectflow.py"; \
      cp /tmp/s0_deterministic.py "$task_tools/s0_deterministic.py"; \
      python_bin="$task_site/../../../bin/python"; \
      "$python_bin" /tmp/patch_hooks_init.py "$hooks_init"; \
      "$python_bin" -m py_compile \
        "$task_tools/projectflow.py" \
        "$task_tools/projectflow_upstream.py" \
        "$task_tools/s0_deterministic.py" \
        "$hooks_init"; \
    done; \
    rm /tmp/agent_station_projectflow.py /tmp/s0_deterministic.py /tmp/patch_hooks_init.py
