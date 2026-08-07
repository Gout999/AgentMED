"""探针集加载：解析 contracts/fixtures/probes-customer-service.yaml（及其它探针集）。

冻结纪律：加载时按探针 id 排序，计算冻结 digest（见 digests.probe_set_digest）。
对同一探针文件，digest 恒定；任何增删改都须换新 digest。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .digests import probe_set_digest
from .models import Probe, ProbeSet

DEFAULT_PROBES_FIXTURE = "contracts/fixtures/probes-customer-service.yaml"


def _parse_probe(raw: dict) -> Probe:
    eb = raw.get("expected_behavior") or {}
    tags = raw.get("tags") or {}
    return Probe(
        id=str(raw["id"]),
        input=str(raw["input"]),
        description=str(eb.get("description", "")),
        must_include=tuple(str(x) for x in eb.get("must_include", [])),
        must_not_include=tuple(str(x) for x in eb.get("must_not_include", [])),
        format=eb.get("format"),
        max_output_chars=eb.get("max_output_chars"),
        fault_layer=tags.get("fault_layer"),
        topic=tags.get("topic"),
    )


def load_probe_set(repo_root: Path, fixture_path: str = DEFAULT_PROBES_FIXTURE) -> ProbeSet:
    """从仓库契约 fixture 加载探针集。fixture_path 相对 repo_root。"""
    path = (repo_root / fixture_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"探针集 fixture 不存在: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    probes = tuple(sorted((_parse_probe(p) for p in raw["probes"]), key=lambda p: p.id))
    return ProbeSet(
        probe_set_id=str(raw["probe_set_id"]),
        version=str(raw.get("version", "")),
        probes=probes,
    )


def load_probe_set_from_dict(raw: dict) -> ProbeSet:
    probes = tuple(sorted((_parse_probe(p) for p in raw["probes"]), key=lambda p: p.id))
    return ProbeSet(
        probe_set_id=str(raw["probe_set_id"]),
        version=str(raw.get("version", "")),
        probes=probes,
    )


def frozen_digest(ps: ProbeSet) -> str:
    """探针集冻结 digest（契约：冻结后不可变）。"""
    raw_probes = [
        {
            "id": p.id,
            "input": p.input,
            "expected_behavior": {
                "description": p.description,
                "must_include": list(p.must_include),
                "must_not_include": list(p.must_not_include),
                **({"format": p.format} if p.format else {}),
                **({"max_output_chars": p.max_output_chars} if p.max_output_chars is not None else {}),
            },
            "tags": {
                **({"fault_layer": p.fault_layer} if p.fault_layer else {}),
                **({"topic": p.topic} if p.topic else {}),
            },
        }
        for p in ps.probes
    ]
    return probe_set_digest(raw_probes)
