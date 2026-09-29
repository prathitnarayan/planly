-- 004: checkbox state for the Today list (days not closed yet) + notes about moved work.
-- Safe to run twice.
alter table public.goals add column if not exists ticks jsonb not null default '{}'::jsonb;
