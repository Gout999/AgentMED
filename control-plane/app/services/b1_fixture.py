"""Repository-owned B1 complaint identity.

The live control boundary and the offline evidence verifier both load this
module so a generic Feishu message cannot be relabelled as the frozen B1
badcase by orchestration code.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

import yaml


class B1FixtureError(RuntimeError):
    pass


@dataclass(frozen=True)
class B1ComplaintFixture:
    text: str
    text_digest: str
    repository_ref: str


_REPO_ROOT = next(
    (
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "contracts" / "fixtures").is_dir()
    ),
    Path(__file__).resolve().parents[3],
)
_FIXTURE_REF = "contracts/fixtures/b1-prompt-regression.yaml"
_FIXTURE_PATH = _REPO_ROOT / _FIXTURE_REF


def load_b1_complaint_fixture() -> B1ComplaintFixture:
    """Load and validate the exact complaint frozen by the B1 contract."""

    try:
        document = yaml.safe_load(_FIXTURE_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise B1FixtureError(f"cannot load {_FIXTURE_REF}: {exc}") from exc
    scenario = document.get("scenario") if isinstance(document, dict) else None
    text = scenario.get("complaint_example") if isinstance(scenario, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("fixture_id") != "B1"
        or document.get("draft") is not False
        or document.get("fault_layer") != "prompt"
        or not isinstance(text, str)
        or not text.strip()
    ):
        raise B1FixtureError(f"{_FIXTURE_REF} has no final prompt-layer B1 complaint")
    return B1ComplaintFixture(
        text=text,
        text_digest="sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
        repository_ref=_FIXTURE_REF,
    )
