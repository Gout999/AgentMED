"""Patch copaw_worker/hooks/__init__.py to register the s0_deterministic tool.

Usage: python patch_hooks_init.py <path-to-hooks-init>
Idempotent: exits 0 if already patched.
"""
import sys

path = sys.argv[1]
source = open(path, encoding="utf-8").read()
if "s0_deterministic" in source:
    raise SystemExit(0)

anchor_import = "from copaw_worker.hooks.tools.taskflow import taskflow"
if anchor_import not in source:
    raise SystemExit(f"anchor import missing in {path}")
source = source.replace(
    anchor_import,
    anchor_import
    + "\n    from copaw_worker.hooks.tools.s0_deterministic import s0_deterministic",
    1,
)

anchor_register = 'logger.debug("Registered AgentTeams CoPaw taskflow tool")'
if anchor_register not in source:
    raise SystemExit(f"anchor register missing in {path}")
source = source.replace(
    anchor_register,
    anchor_register
    + '\n            _register_tool_function(\n                toolkit,\n                s0_deterministic,\n                namesake_strategy="override",\n            )\n'
    + '            logger.debug("Registered AgentTeams CoPaw s0_deterministic tool")',
    1,
)

open(path, "w", encoding="utf-8").write(source)
print(f"patched {path}")
