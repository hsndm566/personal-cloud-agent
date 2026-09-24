create unique index if not exists artifacts_one_final_output_per_run_idx
  on agent_control.artifacts(run_id)
  where kind = 'final_output';
