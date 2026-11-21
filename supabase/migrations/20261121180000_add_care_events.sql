-- Synthetic annual-review history for the complication radar. These rows stay
-- outside source batches, FHIR export and the deterministic review tree, and are
-- reachable only through FastAPI.
create table public.care_events (
  id varchar(36) primary key,
  patient_id varchar(36) not null references public.patients(id),
  kind varchar(32) not null,
  performed_at timestamptz not null,
  value double precision,
  value_secondary double precision,
  unit varchar(32),
  result varchar(48),
  provenance jsonb not null default '{}'::jsonb
);

create index care_events_patient_id_idx on public.care_events(patient_id);
create index care_events_kind_idx on public.care_events(kind);
create index care_events_performed_at_idx on public.care_events(performed_at);

alter table public.care_events enable row level security;
revoke all privileges on table public.care_events from anon, authenticated;
