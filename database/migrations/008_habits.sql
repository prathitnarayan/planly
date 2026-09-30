-- 008: habit streaks. Safe to run twice.
-- habits: what you're quitting or building. habit_days: one row per answered day (yes/no).
-- urges: when a craving hit (only the time is used, to learn YOUR risky hours).

create table if not exists public.habits (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null default auth.uid() references auth.users(id) on delete cascade,
  data       jsonb not null,               -- name, kind, why, label, started, archived, nudge
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists habits_user on public.habits (user_id);
alter table public.habits enable row level security;
drop policy if exists habits_own on public.habits;
create policy habits_own on public.habits
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

create table if not exists public.habit_days (
  habit_id   uuid not null references public.habits(id) on delete cascade,
  user_id    uuid not null default auth.uid() references auth.users(id) on delete cascade,
  day        date not null,
  kept       boolean not null,
  updated_at timestamptz not null default now(),
  primary key (habit_id, day)
);
create index if not exists habit_days_user_day on public.habit_days (user_id, day);
alter table public.habit_days enable row level security;
drop policy if exists habit_days_own on public.habit_days;
create policy habit_days_own on public.habit_days
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());

create table if not exists public.urges (
  id         bigserial primary key,
  habit_id   uuid not null references public.habits(id) on delete cascade,
  user_id    uuid not null default auth.uid() references auth.users(id) on delete cascade,
  at         timestamptz not null default now(),
  local_at   timestamp not null               -- the user's own clock at that moment
);
create index if not exists urges_user_at on public.urges (user_id, at);
alter table public.urges enable row level security;
drop policy if exists urges_read on public.urges;
drop policy if exists urges_add on public.urges;
create policy urges_read on public.urges for select using (user_id = auth.uid());
create policy urges_add on public.urges for insert with check (user_id = auth.uid());
