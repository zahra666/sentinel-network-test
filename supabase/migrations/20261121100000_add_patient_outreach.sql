-- Patient outreach: Sentinel Agent drafts reminder emails; staff approve them.
-- Delivery is a simulated outbox (no SMTP/messaging gateway). Results are
-- included only when the recorded consent allows. Server-access only;
-- FastAPI remains the sole gateway.
create table public.patient_contact_consents (
  id varchar(36) primary key,
  patient_id varchar(36) not null unique references public.patients(id),
  contact_email varchar(180) not null,
  email_reminders boolean not null default true,
  share_results boolean not null default false,
  recorded_by varchar(36) references public.users(id),
  recorded_at timestamptz not null default now()
);

create table public.patient_messages (
  id varchar(36) primary key,
  patient_id varchar(36) not null references public.patients(id),
  organization_id varchar(36) not null references public.organizations(id),
  channel varchar(24) not null default 'email',
  template varchar(48) not null,
  recipient varchar(180) not null,
  subject varchar(200) not null,
  body text not null,
  includes_results boolean not null default false,
  status varchar(24) not null default 'awaiting_approval',
  drafted_by_agent varchar(48) not null default 'sentinel_agent',
  requested_by varchar(36) not null references public.users(id),
  approved_by varchar(36) references public.users(id),
  approved_at timestamptz,
  delivery varchar(32) not null default 'simulated_outbox',
  created_at timestamptz not null default now()
);

create index patient_contact_consents_recorded_by_idx on public.patient_contact_consents(recorded_by);
create index patient_messages_patient_id_idx on public.patient_messages(patient_id);
create index patient_messages_organization_id_idx on public.patient_messages(organization_id);
create index patient_messages_status_idx on public.patient_messages(status);
create index patient_messages_requested_by_idx on public.patient_messages(requested_by);
create index patient_messages_approved_by_idx on public.patient_messages(approved_by);
create index patient_messages_created_at_idx on public.patient_messages(created_at);

alter table public.patient_contact_consents enable row level security;
alter table public.patient_messages enable row level security;
revoke all privileges on table public.patient_contact_consents from anon, authenticated;
revoke all privileges on table public.patient_messages from anon, authenticated;
