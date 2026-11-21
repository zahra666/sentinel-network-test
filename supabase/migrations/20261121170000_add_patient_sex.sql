-- Synthetic administrative sex (female / male) for sex-specific laboratory
-- reference intervals and the CKD-EPI creatinine equation. Nullable so existing
-- rows and FHIR-matched patients without a recorded sex remain valid.
alter table public.patients add column if not exists sex varchar(16);
alter table public.patients add constraint patients_sex_check check (sex is null or sex in ('female', 'male'));
-- RLS and privileges on public.patients are unchanged (FastAPI remains the sole gateway).
