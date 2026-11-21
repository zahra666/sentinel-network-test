-- Synthetic current-medication list used by the read-only SGLT2 care-gap review.
-- Stored internally (not exposed as a FHIR resource); FastAPI is the sole gateway.
create table public.medication_statements (
  id varchar(36) primary key,
  patient_id varchar(36) not null references public.patients(id),
  source_id varchar(96) not null,
  version integer not null default 1,
  status varchar(32) not null default 'active',
  source_coding_system varchar(160),
  source_code varchar(96),
  source_display varchar(240),
  atc_code varchar(16),
  atc_display varchar(160),
  terminology_status varchar(48) not null default 'needs_terminology_review',
  effective_from timestamptz,
  recorded_at timestamptz not null default now(),
  provenance jsonb not null default '{}'::jsonb,
  constraint uq_medication_source_version unique (source_id, version)
);

create index medication_statements_patient_id_idx on public.medication_statements(patient_id);
create index medication_statements_status_idx on public.medication_statements(status);
create index medication_statements_atc_code_idx on public.medication_statements(atc_code);
create index medication_statements_terminology_status_idx on public.medication_statements(terminology_status);

alter table public.medication_statements enable row level security;
revoke all privileges on table public.medication_statements from anon, authenticated;
