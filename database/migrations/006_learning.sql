-- 006: learning + one shared pool of free time. Safe to run twice.

-- Per user, across goals: the shared weekly free time, goal priority order, and what Planly learned.
create table if not exists public.user_settings (
  user_id    uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  capacity   jsonb,
  goal_order jsonb not null default '[]'::jsonb,
  learned    jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
alter table public.user_settings enable row level security;
drop policy if exists settings_own on public.user_settings;
create policy settings_own on public.user_settings
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

-- One row per planned session of every closed day: planned vs credited, weekday, video pace.
-- Append-only. This is the dataset for learning (and later the ML model).
create table if not exists public.outcomes (
  id         bigserial primary key,
  user_id    uuid not null default auth.uid() references auth.users(id) on delete cascade,
  goal_id    uuid references public.goals(id) on delete set null,
  day        date not null,
  data       jsonb not null,
  created_at timestamptz not null default now()
);
create index if not exists outcomes_user_day on public.outcomes (user_id, day);
alter table public.outcomes enable row level security;
drop policy if exists outcomes_read on public.outcomes;
drop policy if exists outcomes_add on public.outcomes;
create policy outcomes_read on public.outcomes for select using (user_id = auth.uid());
create policy outcomes_add on public.outcomes for insert with check (user_id = auth.uid());

-- Real time spent on each lecture page (pauses included), for the video pace.
alter table public.watch_evidence add column if not exists active_s real not null default 0;
