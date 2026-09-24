# Northflank Worker Deployment

The durable-run worker is a separate, non-HTTP process. Do not run it inside the API container.

## Source

- Repository: `hsndm566/personal-cloud-agent`
- Recovery branch: `codex/foundation`
- Dockerfile: `docker/Dockerfile.worker`
- Container command: inherited from the image: `python -m worker.main`
- Public ports: none

The API and worker must be deployed from the same tested recovery commit.

## Required environment

The worker fails closed when the control-plane database or model configuration is missing.

Required:

```text
CONTROL_PLANE_DATABASE_URL=<server-side Postgres connection string>
CONTROL_PLANE_QUEUE_NAME=agent_runs

DATABASE_TYPE=postgres
POSTGRES_USER=<shared LangGraph Postgres user>
POSTGRES_PASSWORD=<secret>
POSTGRES_HOST=<host>
POSTGRES_PORT=<port>
POSTGRES_DB=<database>
```

Configure one real model provider supported by the existing base, for example a provider API key
or an OpenAI-compatible endpoint. Do not deploy the production worker with
`USE_FAKE_MODEL=true`.

Optional:

```text
CONTROL_PLANE_VISIBILITY_TIMEOUT=60
CONTROL_PLANE_POLL_INTERVAL=1.0
CONTROL_PLANE_MAX_ATTEMPTS=3
CONTROL_PLANE_HEARTBEAT_INTERVAL=15.0
CONTROL_PLANE_HEARTBEAT_STALE_AFTER=45.0
CONTROL_PLANE_WORKER_ID=<stable deployment identifier>
GITHUB_PAT=<only when read-only GitHub inspection tools are required>
```

Clerk JWT settings are not required by the worker. Ownership is taken from the persisted run and
queue message, then cross-checked by the worker before execution.

## Northflank service shape

Create a background/service workload from the same repository and branch as the recovery API.

1. Select the repository and `codex/foundation` branch.
2. Build with `docker/Dockerfile.worker`.
3. Do not expose a public HTTP port.
4. Attach the server-side database and model-provider secrets.
5. Keep automatic restarts enabled.
6. Use one worker replica initially.
7. Confirm the process stays running instead of repeatedly restarting.

The worker image performs a compile/import smoke check during build. A successful image build proves
the worker import path is valid, but it does not prove queue consumption.

## Liveness proof

The worker writes a heartbeat immediately at startup and then periodically.

The API exposes the newest heartbeat to authenticated users:

```text
GET /runs/system/worker-health
```

A healthy deployed worker should return `available: true`. A stale heartbeat returns
`available: false`.

The authoritative database record is:

```sql
select worker_id, agent_id, started_at, last_seen_at
from agent_control.worker_heartbeats
order by last_seen_at desc
limit 5;
```

Do not call the worker healthy from a deployment-dashboard state alone.

## End-to-end acceptance test

After a heartbeat is visible:

1. Submit an authenticated `POST /runs` request with a small read-only goal.
2. Confirm the run is persisted as `queued`.
3. Confirm pgmq leases the message.
4. Confirm the run transitions to `running`.
5. Confirm a `final_output` artifact is persisted.
6. Confirm the run becomes `completed`.
7. Confirm the queue message is archived.
8. Fetch `GET /runs/{run_id}/events` and verify the ordered timeline.
9. Fetch `GET /runs/{run_id}/artifacts` and verify the final output.

Do not merge the recovery foundation to `main` until this path is demonstrated against the live
Supabase project.

## Failure behavior

- Queue leases are renewed during long-running work using `pgmq.set_vt`.
- Transient execution failures remain unarchived and retry after the visibility timeout.
- Retry count is persisted on the run.
- The final configured attempt marks the run `failed` and permits the queue message to archive.
- Malformed queue messages are archived without invoking the agent.
- A queued, blocked, or interrupted run can be cancelled.
- An actively running run returns a conflict on cancellation until cooperative LangGraph
  cancellation is implemented.
- `final_output` persistence is idempotent per run, so a retry cannot create duplicate final
  output artifacts.
