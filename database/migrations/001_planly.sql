-- Planly schema v1 (Supabase / Postgres).
-- Paste into Supabase: SQL Editor -> New query -> Run. Safe to run once on a new project.
--
-- Design: the planner works on Pydantic objects (interview, blueprint, capacity), so
-- those are stored as JSONB documents. Things that are LOGGED over time — check-ins
-- and estimate corrections — get their own append-only tables (future ML data).
--
-- Every row has user_id. RLS lets a signed-in user touch only their own rows.
-- NOTE: the FastAPI backend connects with the database password, which BYPASSES RLS.
-- So the backend ALSO filters every query by user_id (see app/core/repo.py).

create extension if not exists pgcrypto;

-- ---------- goals ----------
create table if not exists public.goals (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null default auth.uid() references auth.users(id) on delete cascade,
  title           text not null,
  interview       jsonb not null,          -- InterviewState: messages, profile, flags
  blueprint       jsonb,                   -- GoalBlueprint (null until generated)
  capacity        jsonb,                   -- CapacityProfile (weekly slots)
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists goals_user_idx on public.goals (user_id, updated_at desc);

create or replace function public.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

drop trigger if exists goals_touch on public.goals;
create trigger goals_touch before update on public.goals
  for each row execute function public.touch_updated_at();

-- ---------- check-ins (append-only) ----------
create table if not exists public.checkins (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null default auth.uid() references auth.users(id) on delete cascade,
  goal_id             uuid not null references public.goals(id) on delete cascade,
  day                 date not null,
  milestone_key       text not null,
  outcome             text not null check (outcome in ('done', 'partial', 'missed')),
  planned_minutes     int  not null default 0 check (planned_minutes >= 0),
  actual_minutes      int  not null default 0 check (actual_minutes >= 0),
  milestone_complete  boolean not null default false,
  remaining_minutes   int  check (remaining_minutes >= 0),
  note                text,
  created_at          timestamptz not null default now(),
  check (outcome <> 'missed' or actual_minutes = 0)
);
create index if not exists checkins_goal_idx on public.checkins (goal_id, day, created_at);

-- ---------- estimate corrections (append-only; AI guess vs user's number) ----------
create table if not exists public.estimate_corrections (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null default auth.uid() references auth.users(id) on delete cascade,
  goal_id         uuid references public.goals(id) on delete set null,
  milestone_key   text not null,
  milestone_name  text not null,
  ai_hours        numeric(7,2) not null,
  user_hours      numeric(7,2) not null,
  ratio           numeric(6,3) not null,
  ai_confidence   numeric(3,2),
  had_benchmark   boolean not null default false,
  source          text not null,
  created_at      timestamptz not null default now()
);
create index if not exists corrections_user_idx on public.estimate_corrections (user_id, created_at);

-- ---------- row level security ----------
alter table public.goals                enable row level security;
alter table public.checkins             enable row level security;
alter table public.estimate_corrections enable row level security;

drop policy if exists goals_own on public.goals;
create policy goals_own on public.goals for all
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- logs: read and add your own; never edit or delete (they're history)
drop policy if exists checkins_read on public.checkins;
drop policy if exists checkins_add  on public.checkins;
create policy checkins_read on public.checkins for select using (user_id = auth.uid());
create policy checkins_add  on public.checkins for insert with check (user_id = auth.uid());

drop policy if exists corrections_read on public.estimate_corrections;
drop policy if exists corrections_add  on public.estimate_corrections;
create policy corrections_read on public.estimate_corrections for select using (user_id = auth.uid());
create policy corrections_add  on public.estimate_corrections for insert with check (user_id = auth.uid());
