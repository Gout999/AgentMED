"""trust-ledger：信任账本模块（被各 server 与控制面复用；非独立 MCP server）。

spec §6 / §9.8 接口：record_outcome / get_state / evaluate_promotion /
request_promotion / suspend / reinstate。Wilson 双侧 95% 为晋升判据。
"""
