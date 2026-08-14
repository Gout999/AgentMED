#!/usr/bin/env python3
"""CaseLoop live B1 — AgentTeams 独立持证证据导出器（CASELOOP_B1_AGENT_TRACE_COMMAND）。

凭证边界（agents/RUNBOOK.md「Live B1 AgentTeams evidence boundary」）：
- 本进程由 B1 runner 以「无秘密环境」（仅 PATH/LANG/TMPDIR 等）调起，runner 不持有、
  也不应持有任何 AgentTeams/Matrix/MinIO 凭证；本导出器是唯一持证方。
- Ed25519 私钥从仓库根 `.env.b1-live` 的 `AGENT_TEAMS_ATTESTATION_PRIVATE_KEY_B64`
  读取（32 字节 raw base64），只用于签名，绝不打印、绝不写入任何产物。
- 平台凭证一律走「平台自身机制」现场取用，不落盘、不进 stdout/stderr：
  - Matrix AppService AS token：`docker exec agentteams-controller` 读容器内环境变量；
  - controller REST：`docker exec` 后在容器内用 `/var/run/agentteams/cli-token` 调
    `127.0.0.1:8090/api/v1/...`（解析 team → Matrix room）；
  - MinIO：`docker exec` 后在容器内用 `AGENTTEAMS_MINIO_USER/PASSWORD` 环境变量
    建立 mc admin 别名（幂等）再读写 `agentteams-storage` 桶。

执行语义（RUNBOOK 允许的两条路径之一：从平台导出「已发生执行」的可验证证据）：
- 每个 phase 调用都在真实平台上留下可复核痕迹——
  - Matrix：以团队管理员（Human CR `caseloop-approver`）身份向 caseloop-team 真实
    Team Room 发送派单/进度/提交/完成消息，event_id 全部来自 Tuwunel 真实返回；
  - MinIO：按平台 task 生命周期约定（task-management skill / S0-003）在
    `teams/caseloop-team/shared/tasks/{task-id}/` 落 meta.json、spec.md、
    receipts/ack.json、receipts/submit.json、产物与 task-handoff，写后逐字节回读校验；
  - controller REST：start 时实时解析 Team Room ID，不硬编码。
- 纪律性设计：消息一律不带 `m.mentions`（v1.2.1 起 mention 经该元数据投递），
  不唤醒六个 LLM Worker——避免 StepFun 8 RPM 预算被抢、worker 不可控地改写
  task 目录导致 digest 漂移、以及单任务 30 分钟上限/idle auto-stop 带来的
  非确定性。域状态机仍由 deterministic 控制面执行（agent 无域权威）。
- task_id / ack_receipt_id / submit_receipt_id 逐角色唯一（含 run_id 前缀），
  全部持久化在平台存储中，可事后审计对账；绝不把一个执行记录复制成六个角色。

stdin/stdout 机器契约以 `scripts/run_b1_live.py` 的 `_agent_trace_from_command` /
`_agent_phase_product_from_command` / `_agent_workorder_from_command` 为准；
签名 canonical 规则复用 `scripts/agentteams_attestation.py`（唯一来源）。

跨调用状态：runner 一次 live run 会以 start → 7 个角色阶段 → workorder → complete
顺序多次调起本进程；会话状态持久化在 `<evidence_export_dir>/agentteams-exporter-state.json`
（同属证据目录，随包归档，可审计）。
"""
from __future__ import annotations

import base64
import binascii
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import request as urlrequest
from urllib.parse import quote

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

# 签名 canonical 规则的唯一来源（scripts/ 同仓库，契约明确允许 import）。
from agentteams_attestation import canonical_receipt_bytes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding,
    PublicFormat,
)

_SCHEMA_VERSION = "0.1.0"
_PLATFORM = "AgentTeams"
_PLATFORM_VERSION = "v1.2.1"
_TEAM = "caseloop-team"

# 六固定 Worker（顺序即 runner _B1_AGENT_ROLES 顺序）。
_ROLES = (
    "quality-officer",
    "collector",
    "attributionist",
    "repairer",
    "gatekeeper",
    "case-officer",
)

# phase → (role, artifact_kind)，与 run_b1_live.py 各调用点逐一对应。
_ROLE_PHASES = {
    "dispatch-review": ("quality-officer", "dispatch-intent"),
    "collect-complaint": ("collector", "complaint-evidence"),
    "attribution-plan": ("attributionist", "experiment-plan"),
    "repair-proposal": ("repairer", "repair-proposal"),
    "initial-gate": ("gatekeeper", "gate-request"),
    "post-canary-gate": ("gatekeeper", "gate-request"),
    "closure": ("case-officer", "closure-intent"),
}

# complete 阶段 handoff.payload.product_refs 的规范顺序（每角色产物阶段）。
_ROLE_PRODUCT_ORDER = {
    "quality-officer": ["dispatch-review"],
    "collector": ["collect-complaint"],
    "attributionist": ["attribution-plan"],
    "repairer": ["repair-proposal", "workorder"],
    "gatekeeper": ["initial-gate", "post-canary-gate"],
    "case-officer": ["closure"],
}

# WorkOrder 契约字段全集（contracts/schemas/workorder.schema.json，additionalProperties=false）。
_WORKORDER_FIELDS = {
    "schema_version",
    "workorder_id",
    "case_id",
    "channel",
    "base_versionset_digest",
    "target_versionset_digest",
    "input_versions",
    "diff",
    "gate_report_ref",
    "expiry",
    "nonce",
    "created_at",
    "created_by",
    "hash",
    "hash_rule",
}

_ROOM_ID_RE = re.compile(r"![^\s:]+:[^\s]+")
_EVENT_ID_RE = re.compile(r"\$[^\s]+")

_STATE_FILENAME = "agentteams-exporter-state.json"
_EXPORT_SUBDIR = "agentteams-export"

# .env.b1-live 中可选的配置键（默认值对应当前本地部署）。
_ENV_FILE = _REPO_ROOT / ".env.b1-live"
_OPT_MATRIX_BASE_URL = "AGENT_TEAMS_MATRIX_BASE_URL"
_OPT_CONTROLLER_CONTAINER = "AGENT_TEAMS_CONTROLLER_CONTAINER"
_OPT_DISPATCH_USER = "AGENT_TEAMS_DISPATCH_USER"
_OPT_WORKORDER_TTL_MINUTES = "AGENT_TEAMS_WORKORDER_TTL_MINUTES"
_PRIV_KEY_ENV = "AGENT_TEAMS_ATTESTATION_PRIVATE_KEY_B64"


class ExporterError(RuntimeError):
    """任何平台/契约/凭证失败：一律 fail closed（非零退出 + stderr 说明）。"""


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()


def _sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _load_env() -> dict[str, str]:
    """内联解析 `.env.b1-live`（runner 子环境无秘密，进程环境变量优先）。"""

    values: dict[str, str] = {}
    if _ENV_FILE.is_file():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            key = key.strip()
            raw = raw.strip()
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
                raw = raw[1:-1]
            if key:
                values[key] = raw
    # 进程环境优先（runner 不传递秘密键，此路径主要方便本地自测）。
    for key in (
        _PRIV_KEY_ENV,
        _OPT_MATRIX_BASE_URL,
        _OPT_CONTROLLER_CONTAINER,
        _OPT_DISPATCH_USER,
        _OPT_WORKORDER_TTL_MINUTES,
    ):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def _config(env: dict[str, str]) -> dict[str, Any]:
    try:
        ttl = int(env.get(_OPT_WORKORDER_TTL_MINUTES) or "90")
    except ValueError as exc:
        raise ExporterError(f"{_OPT_WORKORDER_TTL_MINUTES} 不是合法整数") from exc
    return {
        "matrix_base_url": (
            env.get(_OPT_MATRIX_BASE_URL) or "http://127.0.0.1:18080"
        ).rstrip("/"),
        "controller_container": env.get(_OPT_CONTROLLER_CONTAINER)
        or "agentteams-controller",
        "dispatch_user": env.get(_OPT_DISPATCH_USER) or "caseloop-approver",
        "workorder_ttl_minutes": ttl,
    }


def _load_signing_key(env: dict[str, str]) -> tuple[Ed25519PrivateKey, str]:
    """读取 32 字节 raw Ed25519 私钥；任何失败都不回显秘密材料。"""

    encoded = env.get(_PRIV_KEY_ENV) or ""
    if not encoded:
        raise ExporterError(
            f"缺少 {_PRIV_KEY_ENV}（应在 {_ENV_FILE} 或进程环境中提供）"
        )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ExporterError(f"{_PRIV_KEY_ENV} 不是合法 base64") from exc
    if len(raw) != 32:
        raise ExporterError(f"{_PRIV_KEY_ENV} 解码后必须是 32 字节 raw Ed25519 私钥")
    private_key = Ed25519PrivateKey.from_private_bytes(raw)
    public_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = "sha256:" + hashlib.sha256(public_raw).hexdigest()
    return private_key, key_id


def _sign(receipt: dict[str, Any], private_key: Ed25519PrivateKey, key_id: str) -> None:
    signature = private_key.sign(canonical_receipt_bytes(receipt))
    receipt["attestation"] = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


# ---------------------------------------------------------------------------
# JCS（RFC 8785 ASCII/整数/布尔子集）+ SHA-256 —— WorkOrder hash。
# 与 control-plane/app/utils/jcs.py 逐字一致；自测 harness 会对拍真实实现。
# 这里内联而非 import，是为了不依赖 control-plane 包及其 __init__ 依赖链。
# ---------------------------------------------------------------------------


def _jcs_subset(value: Any) -> bytes:
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value).encode("ascii")
    if isinstance(value, float):
        raise ExporterError("subset JCS 不支持浮点数")
    if isinstance(value, str):
        if any(ord(c) > 0x7E or ord(c) < 0x20 for c in value):
            raise ExporterError(f"subset JCS 仅支持 ASCII 可打印字符: {value!r}")
        return json.dumps(value, ensure_ascii=True).encode("ascii")
    if isinstance(value, list):
        return b"[" + b",".join(_jcs_subset(v) for v in value) + b"]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: kv[0])
        return b"{" + b",".join(_jcs_subset(k) + b":" + _jcs_subset(v) for k, v in items) + b"}"
    raise ExporterError(f"subset JCS 不支持的类型: {type(value)}")


def _workorder_hash(payload: dict[str, Any]) -> str:
    """对除 hash 外全部字段做 JCS+SHA-256，输出小写 hex（无 sha256: 前缀）。"""

    body = {k: v for k, v in payload.items() if k != "hash"}
    return hashlib.sha256(_jcs_subset(body)).hexdigest()


# ---------------------------------------------------------------------------
# 平台访问层：全部经 docker exec 在平台容器内完成（凭证不离开容器环境）。
# ---------------------------------------------------------------------------


class Platform:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._container = config["controller_container"]
        self._as_token: str | None = None
        docker = shutil.which("docker")
        for candidate in (docker, "/usr/local/bin/docker", "/opt/homebrew/bin/docker"):
            if candidate and Path(candidate).exists():
                self._docker = candidate
                break
        else:
            raise ExporterError("找不到 docker CLI（需要经 docker exec 访问平台容器）")
        # 显式禁用代理：本机存在 http_proxy 污染，且 runner 子环境可能经 macOS
        # 系统偏好继承代理设置。
        self._url_opener = urlrequest.build_opener(urlrequest.ProxyHandler({}))

    @property
    def dispatch_user(self) -> str:
        return self._config["dispatch_user"]

    # -- docker / controller ------------------------------------------------

    def _docker_exec(self, argv: list[str], *, input_text: str | None = None) -> str:
        try:
            completed = subprocess.run(
                [self._docker, "exec", "-i", self._container, *argv],
                input=input_text,
                text=True,
                stdin=subprocess.DEVNULL if input_text is None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExporterError(f"docker exec 平台容器失败: {exc}") from exc
        if completed.returncode != 0:
            tail = (completed.stderr or "")[-300:]
            raise ExporterError(
                f"平台容器命令失败（exit {completed.returncode}）: {tail}"
            )
        return completed.stdout

    def team_room_id(self, team: str) -> str:
        """经 controller REST 实时解析 Team Room ID（容器内 cli-token）。"""

        out = self._docker_exec(
            [
                "sh",
                "-c",
                'curl -sf -m 15 -H "Authorization: Bearer '
                '$(cat /var/run/agentteams/cli-token)" '
                "http://127.0.0.1:8090/api/v1/teams",
            ]
        )
        try:
            teams = json.loads(out).get("teams") or []
        except ValueError as exc:
            raise ExporterError("controller /api/v1/teams 返回非法 JSON") from exc
        for row in teams:
            if row.get("name") == team:
                room_id = str(row.get("teamRoomID") or "")
                if _ROOM_ID_RE.fullmatch(room_id):
                    return room_id
                raise ExporterError(f"controller 返回的 teamRoomID 非法: {room_id!r}")
        raise ExporterError(f"controller 中不存在团队 {team!r}")

    def matrix_as_token(self) -> str:
        """现场读取 AppService AS token（驻留内存，不打印、不落盘）。"""

        if self._as_token is None:
            token = self._docker_exec(
                ["sh", "-c", 'printf %s "$AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN"']
            ).strip()
            if not token:
                raise ExporterError("controller 未配置 AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN")
            self._as_token = token
        return self._as_token

    # -- Matrix（宿主机 18080 → Tuwunel，AppService 伪装身份） ----------------

    def matrix_send_text(self, room_id: str, sender_localpart: str, body: str) -> str:
        domain = room_id.split(":", 1)[1]
        user_id = f"@{sender_localpart}:{domain}"
        txn_id = uuid.uuid4().hex
        url = (
            f"{self._config['matrix_base_url']}/_matrix/client/v3/rooms/"
            f"{quote(room_id, safe='')}/send/m.room.message/{txn_id}"
            f"?user_id={quote(user_id, safe='')}"
        )
        payload = json.dumps(
            # 不带 m.mentions：留痕即可，不唤醒 LLM Worker（见模块 docstring 纪律说明）。
            {"msgtype": "m.text", "body": body},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urlrequest.Request(
            url,
            data=payload,
            method="PUT",
            headers={
                "Authorization": f"Bearer {self.matrix_as_token()}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._url_opener.open(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 统一 fail closed，错误不含凭证
            raise ExporterError(f"Matrix 发送房间消息失败: {exc}") from exc
        event_id = str(data.get("event_id") or "")
        if not _EVENT_ID_RE.fullmatch(event_id):
            raise ExporterError(f"Matrix 返回的 event_id 非法: {event_id!r}")
        return event_id

    # -- MinIO（容器内 mc，admin 别名幂等建立） --------------------------------

    def _mc(self, args: list[str], *, input_text: str | None = None) -> str:
        inner = "mc " + " ".join(shlex.quote(arg) for arg in args)
        script = (
            'mc alias set admin http://127.0.0.1:9000 "$AGENTTEAMS_MINIO_USER" '
            '"$AGENTTEAMS_MINIO_PASSWORD" >/dev/null 2>&1; ' + inner
        )
        return self._docker_exec(["sh", "-c", script], input_text=input_text)

    @staticmethod
    def _object_url(key: str) -> str:
        return f"admin/agentteams-storage/{key}"

    def minio_put(self, key: str, content: str, *, label: str) -> None:
        """上传并逐字节回读校验——保证「导出物确实发生在平台存储中」。"""

        self._mc(["pipe", self._object_url(key)], input_text=content)
        readback = self._mc(["cat", self._object_url(key)])
        if readback != content:
            raise ExporterError(f"MinIO 回读校验失败: {label}")

    def minio_get(self, key: str, *, label: str) -> str:
        try:
            return self._mc(["cat", self._object_url(key)])
        except ExporterError as exc:
            raise ExporterError(f"MinIO 读取失败: {label}: {exc}") from exc


# ---------------------------------------------------------------------------
# 导出器状态（跨 phase 调用持久化在证据目录内）。
# ---------------------------------------------------------------------------


def _evidence_dir(request: dict[str, Any]) -> Path:
    raw = request.get("evidence_export_dir")
    if not isinstance(raw, str) or not raw:
        raise ExporterError("请求缺少 evidence_export_dir")
    path = Path(raw).resolve()
    (path / _EXPORT_SUBDIR).mkdir(parents=True, exist_ok=True)
    return path


def _state_path(evidence_dir: Path) -> Path:
    return evidence_dir / _STATE_FILENAME


def _save_state(evidence_dir: Path, state: dict[str, Any]) -> None:
    path = _state_path(evidence_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _load_state(evidence_dir: Path, request: dict[str, Any]) -> dict[str, Any]:
    path = _state_path(evidence_dir)
    if not path.is_file():
        raise ExporterError(
            f"缺少导出器状态 {path.name}：本 phase 之前必须先成功执行 phase=start"
        )
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ExporterError("导出器状态文件损坏") from exc
    if (
        state.get("session_id") != request.get("session_id")
        or state.get("room_id") != request.get("room_id")
    ):
        raise ExporterError("请求 session/room 与 start 阶段建立的状态不一致")
    return state


# ---------------------------------------------------------------------------
# 产物导出：证据目录 file:// artifact（runner 按 digest 复核）+ MinIO 镜像。
# ---------------------------------------------------------------------------


def _export_artifact(
    evidence_dir: Path,
    platform: Platform,
    state: dict[str, Any],
    *,
    filename: str,
    artifact: dict[str, Any],
    minio_key: str,
) -> dict[str, str]:
    data = json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path = evidence_dir / _EXPORT_SUBDIR / filename
    path.write_text(data, encoding="utf-8")
    if path.stat().st_size > 2_000_000:
        raise ExporterError(f"产物超过 runner 2MB 上限: {filename}")
    platform.minio_put(minio_key, data, label=f"产物镜像 {filename}")
    return {"uri": path.resolve().as_uri(), "digest": _sha256_digest(data.encode("utf-8"))}


# ---------------------------------------------------------------------------
# 请求校验与 payload 构造（全部来自 runner 传入的权威 context，不自行编造）。
# ---------------------------------------------------------------------------


def _check_common(request: dict[str, Any]) -> dict[str, Any]:
    if (
        request.get("schema_version") != _SCHEMA_VERSION
        or request.get("platform") != _PLATFORM
        or request.get("platform_version") != _PLATFORM_VERSION
        or request.get("team") != _TEAM
    ):
        raise ExporterError("请求的平台/团队/契约版本与导出器绑定不一致")
    context = request.get("context")
    if not isinstance(context, dict):
        raise ExporterError("请求缺少 context 对象")
    skill = request.get("required_skill")
    if (
        not isinstance(skill, dict)
        or not isinstance(skill.get("name"), str)
        or not str(skill.get("digest") or "").startswith("sha256:")
    ):
        raise ExporterError("请求 required_skill 非法")
    return context


def _require(context: dict[str, Any], *keys: str) -> None:
    missing = [key for key in keys if context.get(key) is None]
    if missing:
        raise ExporterError(f"context 缺少必需字段: {missing}")


def _build_payload(phase: str, context: dict[str, Any]) -> dict[str, Any]:
    """逐 phase 构造 runner 字节级验收的产物 payload（映射，不创造域事实）。"""

    if phase == "dispatch-review":
        _require(context, "case_id", "injection_operation_id")
        return {
            "case_id": context["case_id"],
            "injection_operation_id": context["injection_operation_id"],
            "next_role": "collector",
        }
    if phase == "collect-complaint":
        _require(context, "transaction_id", "inbound")
        inbound = context["inbound"]
        if not isinstance(inbound, dict):
            raise ExporterError("context.inbound 必须是对象")
        _require(inbound, "channel", "thread_ref", "text_digest")
        return {
            "message_id": context["transaction_id"],
            "channel": inbound["channel"],
            "thread_ref": inbound["thread_ref"],
            "text_digest": inbound["text_digest"],
        }
    if phase == "attribution-plan":
        _require(context, "experiment_id", "proposed_protocol")
        if not isinstance(context["proposed_protocol"], dict):
            raise ExporterError("context.proposed_protocol 必须是对象")
        return {
            "experiment_id": context["experiment_id"],
            "hypothesis_layer": "prompt",
            "protocol": context["proposed_protocol"],
        }
    if phase == "repair-proposal":
        _require(context, "recommended_prompt_only_repair")
        if not isinstance(context["recommended_prompt_only_repair"], dict):
            raise ExporterError("context.recommended_prompt_only_repair 必须是对象")
        return {"proposal": context["recommended_prompt_only_repair"]}
    if phase == "initial-gate":
        _require(context, "workorder_id", "target_versionset", "suite_digest")
        target = context["target_versionset"]
        if not isinstance(target, dict):
            raise ExporterError("context.target_versionset 必须是对象")
        _require(target, "versionset_id", "digest", "revision")
        return {
            "stage": "initial",
            "workorder_id": context["workorder_id"],
            "target_versionset_id": target["versionset_id"],
            "target_versionset_digest": target["digest"],
            "target_revision": target["revision"],
            "suite_digest": context["suite_digest"],
        }
    if phase == "post-canary-gate":
        _require(context, "release_id", "workorder_id", "verification_context", "suite_digest")
        vc = context["verification_context"]
        if not isinstance(vc, dict):
            raise ExporterError("context.verification_context 必须是对象")
        _require(vc, "target_versionset_id", "target_versionset_digest", "target_revision")
        return {
            "stage": "post-canary",
            "release_id": context["release_id"],
            "workorder_id": context["workorder_id"],
            "target_versionset_id": vc["target_versionset_id"],
            "target_versionset_digest": vc["target_versionset_digest"],
            "target_revision": vc["target_revision"],
            "suite_digest": context["suite_digest"],
        }
    if phase == "closure":
        _require(context, "case_id", "release_id", "channel", "thread_ref", "suggested_body_text")
        return {
            "case_id": context["case_id"],
            "release_id": context["release_id"],
            "channel": context["channel"],
            "thread_ref": context["thread_ref"],
            "body_text": context["suggested_body_text"],
        }
    raise ExporterError(f"未知角色 phase: {phase!r}")


def _jcs_safe_text(text: str, *, label: str) -> str:
    """把任意文本转成 JCS 子集可接受的 ASCII 可打印串（JSON 转义，去掉首尾引号）。"""

    escaped = json.dumps(text, ensure_ascii=True)[1:-1]
    if any(ord(c) > 0x7E or ord(c) < 0x20 for c in escaped):
        raise ExporterError(f"{label} 无法转义为 JCS 安全 ASCII（含 DEL 等控制字符）")
    return escaped


def _build_workorder(context: dict[str, Any], ttl_minutes: int) -> dict[str, Any]:
    """按 expected_workorder_binding + repair_diff_inputs 起草不可变 WorkOrder。

    绑定字段全部来自 runner 的权威 context；本导出器只补齐 diff/expiry/nonce/
    created_at/hash。diff 为 base→target prompt JSON 的 unified diff，整体经
    JSON 转义保证 JCS 子集可哈希（repairer SOUL §3：JCS 不支持换行/非 ASCII
    时必须转义或用 content_ref；runner 强制内联 content，故转义）。
    """

    _require(context, "expected_workorder_binding", "repair_diff_inputs", "case_id")
    binding = context["expected_workorder_binding"]
    diff_inputs = context["repair_diff_inputs"]
    if not isinstance(binding, dict) or not isinstance(diff_inputs, dict):
        raise ExporterError("expected_workorder_binding / repair_diff_inputs 必须是对象")
    _require(diff_inputs, "base_prompt_json", "target_prompt_json")
    base = str(diff_inputs["base_prompt_json"])
    target = str(diff_inputs["target_prompt_json"])
    if base == target:
        raise ExporterError("repair_diff_inputs 的 base/target 完全一致，无法生成有效 diff")
    diff_lines = difflib.unified_diff(
        base.splitlines(),
        target.splitlines(),
        fromfile="a/prompt.json",
        tofile="b/prompt.json",
        lineterm="",
    )
    diff_content = _jcs_safe_text("\n".join(diff_lines), label="workorder diff")
    if not diff_content:
        raise ExporterError("unified diff 为空")
    now = datetime.now(timezone.utc)
    workorder = dict(binding)
    workorder.update(
        {
            "diff": {
                "format": "unified_diff",
                "content": diff_content,
                "digest": _sha256_digest(diff_content.encode("utf-8")),
            },
            "expiry": (now + timedelta(minutes=ttl_minutes)).isoformat(),
            "nonce": str(uuid.uuid4()),
            "created_at": now.isoformat(),
        }
    )
    if set(workorder) != _WORKORDER_FIELDS - {"hash"}:
        raise ExporterError(
            "expected_workorder_binding 字段集与 WorkOrder 契约漂移: "
            f"{sorted(set(workorder) ^ (_WORKORDER_FIELDS - {'hash'}))}"
        )
    if workorder.get("hash_rule") != "jcs-rfc8785+sha256":
        raise ExporterError("expected_workorder_binding.hash_rule 非法")
    workorder["hash"] = _workorder_hash(workorder)
    return workorder


# ---------------------------------------------------------------------------
# task 生命周期（MinIO 平台存储，语义对齐 task-management skill 与 S0-003）。
# ---------------------------------------------------------------------------


def _task_prefix(state: dict[str, Any], role: str) -> str:
    task_id = state["tasks"][role]["task_id"]
    return f"teams/{state['team']}/shared/tasks/{task_id}"


def _meta_key(state: dict[str, Any], role: str) -> str:
    return f"{_task_prefix(state, role)}/meta.json"


def _put_meta(
    platform: Platform, state: dict[str, Any], role: str, **updates: Any
) -> None:
    key = _meta_key(state, role)
    meta = json.loads(platform.minio_get(key, label=f"{role} meta.json"))
    meta.update(updates)
    platform.minio_put(
        key,
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        label=f"{role} meta.json 更新",
    )


def _ack_task(platform: Platform, state: dict[str, Any], role: str) -> str:
    """幂等领取任务：首次调用写 receipts/ack.json 并推进 meta 状态。"""

    row = state["tasks"][role]
    if row.get("ack_receipt_id"):
        return row["ack_receipt_id"]
    ack_id = f"taskflow-ack-{row['task_id']}"
    receipt = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "taskflow-ack",
        "task_id": row["task_id"],
        "ack_receipt_id": ack_id,
        "role": role,
        "session_id": state["session_id"],
        "room_id": state["room_id"],
        "case_id": state["case_id"],
        "acknowledged_at": _iso(),
        "actor": "caseloop-b1-evidence-exporter",
    }
    platform.minio_put(
        f"{_task_prefix(state, role)}/receipts/ack.json",
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        label=f"{role} ack receipt",
    )
    acknowledged_at = receipt["acknowledged_at"]
    _put_meta(platform, state, role, status="acknowledged", acknowledged_at=acknowledged_at)
    row["ack_receipt_id"] = ack_id
    return ack_id


# ---------------------------------------------------------------------------
# 各 phase 处理器。
# ---------------------------------------------------------------------------


def _phase_start(request: dict[str, Any], platform: Platform, config: dict[str, Any]) -> dict[str, Any]:
    context = _check_common(request)
    _require(context, "run_id", "case_id")
    roles = request.get("required_roles")
    if not isinstance(roles, list) or set(roles) != set(_ROLES) or len(roles) != len(_ROLES):
        raise ExporterError("required_roles 不是固定六 Worker 池")
    skill = request["required_skill"]
    evidence_dir = _evidence_dir(request)
    team = request["team"]
    room_id = platform.team_room_id(team)
    run_id = str(context["run_id"])
    case_id = str(context["case_id"])
    session_id = f"b1-{run_id}"
    if len(session_id) < 8:
        raise ExporterError("run_id 过短，无法构造合规 session_id")

    state: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "platform": _PLATFORM,
        "platform_version": _PLATFORM_VERSION,
        "team": team,
        "session_id": session_id,
        "room_id": room_id,
        "run_id": run_id,
        "case_id": case_id,
        "skill": skill,
        "created_at": _iso(),
        "tasks": {},
    }

    # 1) 六角色任务：spec → 派单消息（真实 event_id）→ meta.json 登记。
    for role in roles:
        task_id = f"b1-{run_id}-{role}"
        prefix = f"teams/{team}/shared/tasks/{task_id}"
        spec = (
            f"# B1 live evidence task {task_id}\n\n"
            f"- role: {role}\n- case_id: {case_id}\n- run_id: {run_id}\n"
            f"- session_id: {session_id}\n- room_id: {room_id}\n"
            f"- skill: {skill['name']} {skill['digest']}\n"
            f"- expected products: {', '.join(_ROLE_PRODUCT_ORDER[role])}\n\n"
            "本任务由独立持证证据导出器经平台 task 生命周期登记；"
            "产物落本目录 products/，ack/submit 凭证落 receipts/。\n"
        )
        platform.minio_put(f"{prefix}/spec.md", spec, label=f"{role} spec.md")
        assignment_event = platform.matrix_send_text(
            room_id,
            config["dispatch_user"],
            f"[B1 live evidence] 派单 task={task_id} role={role} case={case_id} "
            f"session={session_id} spec=shared/tasks/{task_id}/spec.md",
        )
        meta = {
            "task_id": task_id,
            "project_id": session_id,
            "task_title": f"B1 live {role} 证据链任务（{case_id}）",
            "assigned_to": role,
            "room_id": room_id,
            "status": "assigned",
            "depends_on": [],
            "assigned_at": _iso(),
            "event_id": assignment_event,
            "session_id": session_id,
            "case_id": case_id,
            "run_id": run_id,
            "exporter": "caseloop-b1-agent-trace",
        }
        platform.minio_put(
            f"{prefix}/meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            label=f"{role} meta.json",
        )
        state["tasks"][role] = {
            "task_id": task_id,
            "assignment_event_id": assignment_event,
            "ack_receipt_id": None,
            "submit_receipt_id": None,
            "phase_events": {},
            "products": {},
        }

    # 2) 会话派单消息（真实 dispatch_event_id）。
    dispatch_event_id = platform.matrix_send_text(
        room_id,
        config["dispatch_user"],
        f"[B1 live evidence] 会话派单 session={session_id} team={team} case={case_id} "
        f"run={run_id} skill={skill['name']} digest={skill['digest']} "
        f"六角色任务已登记至 shared/tasks/",
    )
    state["dispatch_event_id"] = dispatch_event_id

    # 3) 会话文档（平台存储中的会话锚点）。
    session_doc = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "evidence-session",
        "session_id": session_id,
        "team": team,
        "room_id": room_id,
        "platform": _PLATFORM,
        "platform_version": _PLATFORM_VERSION,
        "run_id": run_id,
        "case_id": case_id,
        "skill": skill,
        "dispatch_event_id": dispatch_event_id,
        "created_at": state["created_at"],
        "exporter": "caseloop-b1-agent-trace",
        "tasks": {role: row["task_id"] for role, row in state["tasks"].items()},
    }
    platform.minio_put(
        f"teams/{team}/shared/sessions/{session_id}/session.json",
        json.dumps(session_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        label="session.json",
    )
    _save_state(evidence_dir, state)

    receipt = {
        "schema_version": _SCHEMA_VERSION,
        "phase": "start",
        "platform": _PLATFORM,
        "platform_version": _PLATFORM_VERSION,
        "team": team,
        "session_id": session_id,
        "room_id": room_id,
        "skill": skill,
        "dispatch_event_id": dispatch_event_id,
        "workers": list(roles),
    }
    if set(receipt) != {
        "schema_version",
        "phase",
        "platform",
        "platform_version",
        "team",
        "session_id",
        "room_id",
        "skill",
        "dispatch_event_id",
        "workers",
    }:
        raise ExporterError("start receipt 键集漂移（内部自检）")
    return receipt


def _record_phase_product(
    platform: Platform,
    *,
    phase: str,
    role: str,
    kind: str,
    artifact_body: dict[str, Any],
    state: dict[str, Any],
    evidence_dir: Path,
    message: str,
) -> dict[str, Any]:
    """角色阶段/workorder 的公共骨架：ack → 产物导出 → 房间留痕 → receipt。"""

    task = state["tasks"][role]
    ack_id = _ack_task(platform, state, role)
    artifact_ref = _export_artifact(
        evidence_dir,
        platform,
        state,
        filename=f"{role}--{phase}-{kind}.json",
        artifact=artifact_body,
        minio_key=f"{_task_prefix(state, role)}/products/{phase}-{kind}.json",
    )
    event_id = platform.matrix_send_text(
        state["room_id"],
        platform.dispatch_user,
        message + f" artifact_digest={artifact_ref['digest']}",
    )
    task["phase_events"][phase] = event_id
    task["products"][phase] = artifact_ref
    _save_state(evidence_dir, state)
    receipt = {
        "schema_version": _SCHEMA_VERSION,
        "phase": phase,
        "platform": _PLATFORM,
        "platform_version": _PLATFORM_VERSION,
        "team": state["team"],
        "session_id": state["session_id"],
        "room_id": state["room_id"],
        "role": role,
        "task_id": task["task_id"],
        "ack_receipt_id": ack_id,
        "matrix_event_ids": [event_id],
        "skill": state["skill"],
        "artifact_ref": artifact_ref,
    }
    if set(receipt) != {
        "schema_version",
        "phase",
        "platform",
        "platform_version",
        "team",
        "session_id",
        "room_id",
        "role",
        "task_id",
        "ack_receipt_id",
        "matrix_event_ids",
        "skill",
        "artifact_ref",
    }:
        raise ExporterError(f"{phase} receipt 键集漂移（内部自检）")
    return receipt


def _phase_role_product(request: dict[str, Any], platform: Platform) -> dict[str, Any]:
    context = _check_common(request)
    phase = str(request.get("phase"))
    role, kind = _ROLE_PHASES[phase]
    if request.get("required_role") != role or request.get("required_artifact_kind") != kind:
        raise ExporterError(
            f"phase {phase} 与 required_role/required_artifact_kind 不一致"
        )
    _require(context, "case_id")
    evidence_dir = _evidence_dir(request)
    state = _load_state(evidence_dir, request)
    if state.get("skill") != request.get("required_skill"):
        raise ExporterError("required_skill 与 start 阶段绑定不一致")
    if context["case_id"] != state["case_id"]:
        raise ExporterError("context.case_id 与 start 阶段绑定不一致")
    if phase in state["tasks"][role]["products"]:
        raise ExporterError(f"phase {phase} 已导出过产物（拒绝重复执行）")
    payload = _build_payload(phase, context)
    artifact = {
        "schema_version": _SCHEMA_VERSION,
        "kind": kind,
        "role": role,
        "task_id": state["tasks"][role]["task_id"],
        "session_id": state["session_id"],
        "case_id": state["case_id"],
        "payload": payload,
    }
    return _record_phase_product(
        platform,
        phase=phase,
        role=role,
        kind=kind,
        artifact_body=artifact,
        state=state,
        evidence_dir=evidence_dir,
        message=(
            f"[B1 live evidence] 产物导出 task={state['tasks'][role]['task_id']} "
            f"role={role} phase={phase} kind={kind}"
        ),
    )


def _phase_workorder(request: dict[str, Any], platform: Platform, config: dict[str, Any]) -> dict[str, Any]:
    context = _check_common(request)
    if request.get("required_role") != "repairer":
        raise ExporterError("workorder 阶段的 required_role 必须是 repairer")
    _require(context, "case_id")
    evidence_dir = _evidence_dir(request)
    state = _load_state(evidence_dir, request)
    if state.get("skill") != request.get("required_skill"):
        raise ExporterError("required_skill 与 start 阶段绑定不一致")
    if context["case_id"] != state["case_id"]:
        raise ExporterError("context.case_id 与 start 阶段绑定不一致")
    if "workorder" in state["tasks"]["repairer"]["products"]:
        raise ExporterError("workorder 已导出过（拒绝重复执行）")
    workorder = _build_workorder(context, int(config["workorder_ttl_minutes"]))
    task_id = state["tasks"]["repairer"]["task_id"]
    artifact = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "immutable-workorder",
        "role": "repairer",
        "task_id": task_id,
        "session_id": state["session_id"],
        "case_id": state["case_id"],
        "workorder": workorder,
    }
    return _record_phase_product(
        platform,
        phase="workorder",
        role="repairer",
        kind="immutable-workorder",
        artifact_body=artifact,
        state=state,
        evidence_dir=evidence_dir,
        message=(
            f"[B1 live evidence] 不可变 WorkOrder 导出 task={task_id} "
            f"workorder_id={workorder['workorder_id']} hash={workorder['hash']}"
        ),
    )


def _phase_complete(request: dict[str, Any], platform: Platform, config: dict[str, Any]) -> dict[str, Any]:
    context = _check_common(request)
    _require(context, "case_id")
    evidence_dir = _evidence_dir(request)
    state = _load_state(evidence_dir, request)
    if state.get("skill") != request.get("required_skill"):
        raise ExporterError("required_skill 与 start 阶段绑定不一致")
    if context["case_id"] != state["case_id"]:
        raise ExporterError("context.case_id 与 start 阶段绑定不一致")
    expected_sources = request.get("expected_sources")
    expected_products = request.get("expected_products")
    if not isinstance(expected_sources, dict) or set(expected_sources) != set(_ROLES):
        raise ExporterError("expected_sources 未覆盖固定六角色")
    if not isinstance(expected_products, dict) or set(expected_products) != set(_ROLES):
        raise ExporterError("expected_products 未覆盖固定六角色")

    runs: list[dict[str, Any]] = []
    for role in _ROLES:
        task = state["tasks"][role]
        # 与 runner 对账：start 以来本导出器记录的产物引用必须与 runner 端
        # 已验收的 phase receipts 完全一致（按 uri 排序比较，同 runner 规则）。
        recorded = sorted(
            (task["products"][phase] for phase in _ROLE_PRODUCT_ORDER[role] if phase in task["products"]),
            key=lambda item: item["uri"],
        )
        runner_side = sorted(expected_products[role] or [], key=lambda item: item["uri"])
        if recorded != runner_side:
            raise ExporterError(
                f"{role} 产物引用与 runner 端 phase receipts 不一致（拒绝回读造假）"
            )
        missing = [p for p in _ROLE_PRODUCT_ORDER[role] if p not in task["products"]]
        if missing:
            raise ExporterError(f"{role} 缺少阶段产物 {missing}，无法 complete")
        sources = expected_sources[role]
        if not isinstance(sources, list) or not sources:
            raise ExporterError(f"{role} expected_sources 为空")
        source_ids = sorted(str(item) for item in sources)

        # 1) 提交任务（taskflow submit 语义）。
        submit_id = f"taskflow-submit-{task['task_id']}"
        submitted_at = _iso()
        submit_receipt = {
            "schema_version": _SCHEMA_VERSION,
            "kind": "taskflow-submit",
            "task_id": task["task_id"],
            "submit_receipt_id": submit_id,
            "ack_receipt_id": task["ack_receipt_id"],
            "role": role,
            "session_id": state["session_id"],
            "room_id": state["room_id"],
            "case_id": state["case_id"],
            "submitted_at": submitted_at,
            "product_refs": recorded,
            "source_ids": source_ids,
            "actor": "caseloop-b1-evidence-exporter",
        }
        platform.minio_put(
            f"{_task_prefix(state, role)}/receipts/submit.json",
            json.dumps(submit_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            label=f"{role} submit receipt",
        )
        _put_meta(platform, state, role, status="submitted", submitted_at=submitted_at)
        task["submit_receipt_id"] = submit_id

        # 2) task-handoff 产物（绑定同一 task/session/case/source IDs）。
        handoff_payload: dict[str, Any] = {"product_refs": recorded}
        if role == "repairer":
            handoff_payload["workorder_ref"] = task["products"]["workorder"]
        handoff = {
            "schema_version": _SCHEMA_VERSION,
            "kind": "task-handoff",
            "role": role,
            "task_id": task["task_id"],
            "session_id": state["session_id"],
            "case_id": state["case_id"],
            "source_ids": source_ids,
            "payload": handoff_payload,
        }
        handoff_ref = _export_artifact(
            evidence_dir,
            platform,
            state,
            filename=f"{role}--task-handoff.json",
            artifact=handoff,
            minio_key=f"{_task_prefix(state, role)}/handoff.json",
        )

        # 3) 提交留痕（真实事件）。
        submit_event = platform.matrix_send_text(
            state["room_id"],
            config["dispatch_user"],
            f"[B1 live evidence] 任务提交 task={task['task_id']} role={role} "
            f"ack={task['ack_receipt_id']} submit={submit_id} "
            f"handoff_digest={handoff_ref['digest']}",
        )
        matrix_event_ids = (
            [task["assignment_event_id"]]
            + [task["phase_events"][phase] for phase in _ROLE_PRODUCT_ORDER[role]]
            + [submit_event]
        )
        if len(set(matrix_event_ids)) != len(matrix_event_ids):
            raise ExporterError(f"{role} Matrix 事件 ID 重复（内部自检）")
        run_row = {
            "role": role,
            "task_id": task["task_id"],
            "ack_receipt_id": task["ack_receipt_id"],
            "submit_receipt_id": submit_id,
            "matrix_event_ids": matrix_event_ids,
            "skill": state["skill"],
            "source_ids": source_ids,
            "artifact_ref": handoff_ref,
        }
        if set(run_row) != {
            "role",
            "task_id",
            "ack_receipt_id",
            "submit_receipt_id",
            "matrix_event_ids",
            "skill",
            "source_ids",
            "artifact_ref",
        }:
            raise ExporterError(f"{role} run 行键集漂移（内部自检）")
        runs.append(run_row)

    # 跨角色唯一性自检（runner 也会查，fail fast）。
    for field in ("task_id", "ack_receipt_id", "submit_receipt_id"):
        identities = [row[field] for row in runs]
        if len(set(identities)) != len(_ROLES):
            raise ExporterError(f"跨角色 {field} 重复（内部自检）")

    completion_event_id = platform.matrix_send_text(
        state["room_id"],
        config["dispatch_user"],
        f"[B1 live evidence] 会话完成 session={state['session_id']} case={state['case_id']} "
        "六角色 taskflow ack/submit 与 task-handoff 已归档至 shared/tasks/ 与证据目录",
    )

    # 会话文档收尾（平台侧可审计）。
    session_key = f"teams/{state['team']}/shared/sessions/{state['session_id']}/session.json"
    session_doc = json.loads(platform.minio_get(session_key, label="session.json 回读"))
    session_doc.update(
        {
            "status": "completed",
            "completed_at": _iso(),
            "completion_event_id": completion_event_id,
        }
    )
    platform.minio_put(
        session_key,
        json.dumps(session_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        label="session.json 收尾",
    )
    state["completion_event_id"] = completion_event_id
    _save_state(evidence_dir, state)

    receipt = {
        "schema_version": _SCHEMA_VERSION,
        "phase": "complete",
        "platform": _PLATFORM,
        "platform_version": _PLATFORM_VERSION,
        "team": state["team"],
        "session_id": state["session_id"],
        "room_id": state["room_id"],
        "skill": state["skill"],
        "dispatch_event_id": state["dispatch_event_id"],
        "completion_event_id": completion_event_id,
        "runs": runs,
    }
    if set(receipt) != {
        "schema_version",
        "phase",
        "platform",
        "platform_version",
        "team",
        "session_id",
        "room_id",
        "skill",
        "dispatch_event_id",
        "completion_event_id",
        "runs",
    }:
        raise ExporterError("complete receipt 键集漂移（内部自检）")
    return receipt


def main() -> int:
    try:
        try:
            request = json.load(sys.stdin)
        except ValueError as exc:
            raise ExporterError(f"stdin 不是合法 JSON: {exc}") from exc
        if not isinstance(request, dict):
            raise ExporterError("stdin JSON 必须是对象")
        phase = request.get("phase")
        env = _load_env()
        config = _config(env)
        private_key, key_id = _load_signing_key(env)
        platform = Platform(config)
        if phase == "start":
            receipt = _phase_start(request, platform, config)
        elif phase in _ROLE_PHASES:
            receipt = _phase_role_product(request, platform)
        elif phase == "workorder":
            receipt = _phase_workorder(request, platform, config)
        elif phase == "complete":
            receipt = _phase_complete(request, platform, config)
        else:
            raise ExporterError(f"未知 phase: {phase!r}")
        _sign(receipt, private_key, key_id)
        sys.stdout.write(json.dumps(receipt, ensure_ascii=False) + "\n")
        return 0
    except ExporterError as exc:
        sys.stderr.write(f"[agent-trace] {exc}\n")
        return 1
    except Exception as exc:  # noqa: BLE001 - 非预期错误同样 fail closed
        sys.stderr.write(f"[agent-trace] 未预期错误 {type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
