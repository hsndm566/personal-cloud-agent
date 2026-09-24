-- The worker can persist "interrupted" when a LangGraph interrupt pauses a run.
-- This migration updates the status contract for already-deployed control-plane schemas.

alter table agent_control.runs
  drop constraint if exists runs_status_check;

alter table agent_control.runs
  add constraint runs_status_check
  check (status in ('queued','running','blocked','completed','failed','cancelled','interrupted'));
