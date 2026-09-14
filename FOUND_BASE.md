# FOUND BASE

Repository: https://github.com/JoshuaC215/agent-service-toolkit

License: MIT; actual LICENSE fetched and read, copyright Joshua Carroll.

Upstream commit: 18d76e919b357e448f60b97c50e704db79456096.

Last meaningful activity: 2026-09-06, model catalog update and live-model checks.

Existing capabilities: FastAPI streaming API, Streamlit chat/history, LangGraph agents, Postgres/SQLite checkpoints, model adapter, lazy agent registry, official GitHub MCP integration, Docker, unit/integration tests, shared bearer authentication.

What already works: these are upstream source-supported capabilities, not yet locally demonstrated. Baseline execution must precede behavior edits.

What we will preserve: upstream history, license, tests, UI, API/client contracts, checkpoint initialization, deployment files, existing example agents and provider integrations.

What is missing: Deep Agents harness integration, worker restart/resume E2E, and full authenticated deployment wiring.

Minimal additions required: register a Deep Agents harness through the existing agent extension boundary; deploy the configured LiteLLM aliases; integrate the worker lifecycle; extend existing UI.

Why chosen over alternatives:

| Candidate | Evidence and tradeoff |
| --- | --- |
| bodanLabs/deepagents-showcase | Deep Agents + FastAPI/React, but no license identified; last commit 2026-02-05 was README-only. Cannot adopt on implied permission. |
| zhaoquan219/open_deepagents | Excellent API/UI/ledger overlap, but no license identified; last commit 2026-06-11. Cannot adopt on implied permission. |
| vstorm-co/full-stack-ai-agent-template | MIT, Deep Agents support, auth, migrations, Next.js, tests, Docker. Maintained but a broad multi-framework generator rather than a small runnable application; substantially more generated surface to baseline and own. |
| wassim249/fastapi-langgraph-agent-production-ready-template | MIT, active 2026-08-16, FastAPI/Postgres/Mem0/auth; no equivalent existing interactive UI, custom provider fallback would need adaptation. |
| IgnazioDS/langgraph-fastapi-starter | Small and Postgres-friendly; GitHub reports NOASSERTION license, limited maintenance evidence. |
| OpenHands/OpenHands | Maintained MIT core but reserved by the research baseline for a later coding specialist; inappropriate top-level harness. |
| agent-service-toolkit | Small runnable application with tested UI/API/checkpoint/MCP seams. Deep Agents can be integrated as a dependency without replacing its application. |

Deep Agents was evaluated first after receipt of the research baseline. The framework repository is MIT and active as of 2026-09-07. Its public repository advisory endpoint returned no published advisories; this is not a security guarantee. Package-specific release/version and dependency audit must precede adoption.

The selected application has no GitHub releases. Pin its exact tested commit. Its public repository advisory endpoint returned no entries on 2026-09-07.

## Constraints discovered

The workspace initially contained only an unborn Git repository. No gh, uv, or Docker executable was found on PATH. No GitHub/Supabase/LiteLLM/Qwen credentials were present in the relevant environment variables. Connected GitHub identity is hsndm566; the connector has no fork operation, so authenticated browser access will be checked. No credentials are to be printed or committed.

## Acceptance status

Phase 0 selection is complete and the fork exists at https://github.com/hsndm566/personal-cloud-agent. The current fork main contains the read-only GitHub MCP hardening, Clerk JWT ownership boundary, and isolated LangGraph user-memory contract. Local verification on 2026-09-14: ruff passed; pyrefly reported 0 errors; 213 tests passed and 4 skipped. Supabase control-plane tables, pgmq dispatch, structured planning/verifier, version-controlled skill selection, a pinned Mem0 OSS adapter, and an injectable LiteLLM alias gateway are implemented. Deep Agents execution, worker restart/resume E2E, and full authenticated deployment remain pending. Do not describe milestone 1 as complete until the supplied checklist is demonstrated.
