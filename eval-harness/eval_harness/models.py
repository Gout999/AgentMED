"""领域模型：探针 / 探针集 / 实验配置。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Probe:
    id: str
    input: str
    description: str = ""
    must_include: tuple[str, ...] = ()
    must_not_include: tuple[str, ...] = ()
    format: str | None = None          # "json" 等
    max_output_chars: int | None = None
    fault_layer: str | None = None
    topic: str | None = None

    @property
    def is_format_json(self) -> bool:
        return self.format == "json"


@dataclass(frozen=True)
class ProbeSet:
    probe_set_id: str
    version: str
    probes: tuple[Probe, ...]

    def by_id(self) -> dict[str, Probe]:
        return {p.id: p for p in self.probes}

    def get(self, probe_id: str) -> Probe:
        return self.by_id()[probe_id]

    def subset(self, ids: list[str]) -> tuple[Probe, ...]:
        index = self.by_id()
        missing = [i for i in ids if i not in index]
        if missing:
            raise KeyError(f"探针集中不存在: {missing}")
        return tuple(index[i] for i in ids)


@dataclass
class ExperimentPlan:
    """一次对照实验计划（5-cell 或全因子）。"""
    experiment_id: str
    case_id: str
    matrix: str                      # "five_cell" | "full_factorial_2x2x2"
    repetitions: int
    confidence: float                # 固定 0.95
    delta_min: float                 # δ_min
    probe_set_digest: str
    version_digests: dict            # {P0,P1,K0,K1,M0,M1: sha256:...}
    discovery: list[str] = field(default_factory=list)
    hidden_confirmation: list[str] = field(default_factory=list)
    unaffected_controls: list[str] = field(default_factory=list)
    random_seed: int | None = None

    @property
    def affected_probe_ids(self) -> list[str]:
        return self.discovery + self.hidden_confirmation
