"""mcp-servers 共享安全件（T4 重写）。

zeroops 的 common/approval.py（静态 token 可重放）与 common/audit.py（失败放行）
显式废弃，本节为重写依据：spec §5.2 / §7.6 / §11。
"""
