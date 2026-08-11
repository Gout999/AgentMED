"""CLI entry point for the C1 activated-operation compiler.

Usage:
    python -m compiler emit    # write contracts/v5/generated/*.json
    python -m compiler check   # validate inputs and print the activated set
    python -m compiler check --json
"""

from __future__ import annotations

import argparse
import json
import sys

from .activated_operations import load_intent_registry
from .emit import REPO_ROOT, emit
from .manifest import build_capability_manifest, build_operation_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="caseloop-v5-compiler",
        description=(
            "C1 activated-operation compiler: deterministic, side-effect free "
            "(convergence plan C1)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "emit", help="write generated manifests to contracts/v5/generated/"
    )
    check_parser = subparsers.add_parser(
        "check", help="validate inputs and print the activated set without writing"
    )
    check_parser.add_argument(
        "--json", action="store_true", help="print a machine-readable summary"
    )
    args = parser.parse_args(argv)

    registry = load_intent_registry(REPO_ROOT / "contracts/v5/intent-registry.yaml")

    if args.command == "emit":
        written = emit()
        for key, path in written.items():
            print(f"{key}: {path.relative_to(REPO_ROOT)}")
        return 0

    operation_manifest = build_operation_manifest(
        registry, REPO_ROOT / "contracts/v5/schemas"
    )
    capability_manifest = build_capability_manifest(operation_manifest)
    summary = {
        "activated_intent_count": operation_manifest["activated_intent_count"],
        "intents": [op["intent"] for op in operation_manifest["operations"]],
        "capability_manifest_count": capability_manifest["enabled_intent_count"],
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        for intent in summary["intents"]:
            print(intent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
