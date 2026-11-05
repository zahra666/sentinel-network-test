-- Run with `supabase test db` or against a dedicated disposable database.
-- The browser is intentionally not granted access; FastAPI is the sole gateway.
do $$
declare
  table_name text;
  tables text[] := array[
    'organizations', 'users', 'agent_access_keys', 'patients', 'observations', 'conditions', 'diagnostic_reports', 'scans', 'findings',
    'alerts', 'source_batches', 'research_grants', 'audit_events',
    'governance_reviews', 'notification_attempts', 'agent_conversations',
    'agent_messages', 'delegated_tasks', 'mdt_reviews'
  ];
begin
  foreach table_name in array tables loop
    if not (select relrowsecurity from pg_class where oid = format('public.%I', table_name)::regclass) then
      raise exception 'RLS must be enabled on public.%', table_name;
    end if;
    if has_table_privilege('anon', format('public.%I', table_name), 'select')
       or has_table_privilege('authenticated', format('public.%I', table_name), 'select') then
      raise exception 'Data API read access must be denied for public.%', table_name;
    end if;
  end loop;
end $$;
