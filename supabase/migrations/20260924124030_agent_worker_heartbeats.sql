create table if not exists agent_control.worker_heartbeats (
  worker_id text primary key,
  agent_id text not null,
  metadata jsonb not null default '{}'::jsonb,
  started_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

alter table agent_control.worker_heartbeats enable row level security;

revoke all on table agent_control.worker_heartbeats from public, anon, authenticated;
grant select, insert, update, delete on table agent_control.worker_heartbeats to service_role;

create index if not exists worker_heartbeats_last_seen_idx
  on agent_control.worker_heartbeats(last_seen_at desc);
