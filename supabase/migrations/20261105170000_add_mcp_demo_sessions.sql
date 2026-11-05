-- Time-bounded, synthetic-only governance simulations. These rows are not
-- credentials, sessions, or clinical records and remain server-access only.
create table public.mcp_demo_sessions (
  id varchar(36) primary key,
  label varchar(80) not null,
  created_by varchar(36) not null references public.users(id),
  started_at timestamptz not null default now(),
  expires_at timestamptz not null,
  ended_at timestamptz
);

create index mcp_demo_sessions_created_by_idx on public.mcp_demo_sessions(created_by);
create index mcp_demo_sessions_expires_at_idx on public.mcp_demo_sessions(expires_at);

alter table public.mcp_demo_sessions enable row level security;
revoke all privileges on table public.mcp_demo_sessions from anon, authenticated;
