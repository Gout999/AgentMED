# Stage 0 receipt boundary

This package contains contract and verifier receipts only. It contains no provider request, model output, AgentTeams task, Claude Code session, repository mutation, approval or release receipt.

All v4 ModelCallReceipt fixtures used by conformance tests declare `call_mode=REPLAY`. They prove that the frozen schema and semantic validators accept one internally consistent example and reject the recorded attacks; they do not prove a live model call.

The only passing evidence facet is `contract`. Every runtime, provider, Agent, repository, human-authorized and production facet remains `NOT_RUN`.
