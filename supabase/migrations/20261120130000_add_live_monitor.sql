-- Staff-only live monitor: accelerated-time synthetic simulation runs and
-- aggregate review-state snapshots. Snapshots hold counts only, never patient
-- identifiers. Server-access only; FastAPI remains the sole gateway.
create table public.simulation_runs (
  id varchar(36) primary key,
  started_by varchar(36) not null references public.users(id),
  status varchar(24) not null default 'running',
  started_at timestamptz not null default now(),
  stopped_at timestamptz,
  expires_at timestamptz not null,
  tick_seconds integer not null default 5,
  simulated_days_per_tick integer not null default 7,
  simulated_start timestamptz not null,
  simulated_now timestamptz not null,
  last_tick_at timestamptz,
  ticks integer not null default 0,
  observations_written integer not null default 0,
  error_summary text,
  last_statuses json not null default '{}'
);

create table public.live_status_snapshots (
  id varchar(36) primary key,
  run_id varchar(36) references public.simulation_runs(id),
  scan_id varchar(36) references public.scans(id),
  captured_at timestamptz not null default now(),
  simulated_at timestamptz,
  counts json not null default '[]',
  open_alerts json not null default '{}',
  events json not null default '{}'
);

create index simulation_runs_started_by_idx on public.simulation_runs(started_by);
create index simulation_runs_status_idx on public.simulation_runs(status);
create index simulation_runs_started_at_idx on public.simulation_runs(started_at);
create index live_status_snapshots_run_id_idx on public.live_status_snapshots(run_id);
create index live_status_snapshots_scan_id_idx on public.live_status_snapshots(scan_id);
create index live_status_snapshots_captured_at_idx on public.live_status_snapshots(captured_at);

alter table public.simulation_runs enable row level security;
alter table public.live_status_snapshots enable row level security;
revoke all privileges on table public.simulation_runs from anon, authenticated;
revoke all privileges on table public.live_status_snapshots from anon, authenticated;
