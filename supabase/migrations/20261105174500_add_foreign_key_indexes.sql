-- Completes foreign-key coverage identified by the Supabase performance advisor.
-- These indexes support the server-side-only relational queries; no browser grants
-- or RLS policies are introduced.

create index agent_access_keys_approved_by_idx on public.agent_access_keys(approved_by);
create index agent_conversations_user_id_idx on public.agent_conversations(user_id);
create index agent_messages_conversation_id_idx on public.agent_messages(conversation_id);
create index alerts_assigned_to_idx on public.alerts(assigned_to);
create index alerts_acknowledged_by_idx on public.alerts(acknowledged_by);
create index alerts_resolved_by_idx on public.alerts(resolved_by);
create index audit_events_actor_id_idx on public.audit_events(actor_id);
create index governance_reviews_actor_id_idx on public.governance_reviews(actor_id);
create index mdt_reviews_requested_by_idx on public.mdt_reviews(requested_by);
create index mdt_reviews_clinician_attested_by_idx on public.mdt_reviews(clinician_attested_by);
create index notification_attempts_finding_id_idx on public.notification_attempts(finding_id);
create index research_grants_user_id_idx on public.research_grants(user_id);
create index research_grants_organization_id_idx on public.research_grants(organization_id);
create index users_organization_id_idx on public.users(organization_id);
