create extension if not exists pgmq;

create schema if not exists agent_control;
revoke all on schema agent_control from public;
grant usage on schema agent_control to service_role;

create table if not exists agent_control.projects (
  id uuid primary key default gen_random_uuid(),
  owner_id text not null,
  name text not null,
  description text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists agent_control.runs (
  id uuid primary key default gen_random_uuid(),
  owner_id text not null,
  project_id uuid references agent_control.projects(id) on delete set null,
  goal text not null,
  status text not null default 'queued' check (status in ('queued','running','blocked','completed','failed','cancelled')),
  thread_id text not null,
  checkpoint_namespace text,
  retry_count integer not null default 0 check (retry_count >= 0),
  result jsonb,
  error jsonb,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists agent_control.run_events (
  id bigint generated always as identity primary key,
  run_id uuid not null references agent_control.runs(id) on delete cascade,
  owner_id text not null,
  event_type text not null,
  state text,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists agent_control.task_nodes (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references agent_control.runs(id) on delete cascade,
  owner_id text not null,
  task_key text not null,
  title text not null,
  objective text,
  status text not null default 'pending' check (status in ('pending','running','completed','failed','blocked','skipped')),
  verification_status text not null default 'unverified' check (verification_status in ('unverified','verified','partially_verified','failed','blocked')),
  evidence jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(run_id, task_key)
);

create table if not exists agent_control.approvals (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references agent_control.runs(id) on delete cascade,
  owner_id text not null,
  action text not null,
  risk_level text not null check (risk_level in ('read_only','low_risk_write','consequential','destructive')),
  status text not null default 'pending' check (status in ('pending','approved','rejected','expired')),
  requested_at timestamptz not null default now(),
  resolved_at timestamptz,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists agent_control.artifacts (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references agent_control.runs(id) on delete cascade,
  owner_id text not null,
  kind text not null,
  uri text,
  content jsonb,
  created_at timestamptz not null default now()
);

create index if not exists runs_owner_created_idx on agent_control.runs(owner_id, created_at desc);
create index if not exists run_events_run_created_idx on agent_control.run_events(run_id, created_at);
create index if not exists task_nodes_run_status_idx on agent_control.task_nodes(run_id, status);
create index if not exists approvals_run_status_idx on agent_control.approvals(run_id, status);

grant select, insert, update, delete on all tables in schema agent_control to service_role;
grant usage, select on all sequences in schema agent_control to service_role;

do $$
begin
  if not exists (select 1 from pgmq.list_queues() where queue_name = 'agent_runs') then
    perform pgmq.create('agent_runs');
  end if;
end
$$;
