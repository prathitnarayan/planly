-- NOT APPLIED. Future normalized design (milestones/tasks as rows). The live schema is
-- database/migrations/001_planly.sql, which stores planner objects as JSONB documents.

-- Planly v0 schema (Supabase / Postgres)
-- Run in Supabase SQL editor, or keep as migration 001.
--
-- Rule: every table has user_id, and RLS allows a user to touch only their own rows.
-- Note: if FastAPI connects with the service-role key, RLS is BYPASSED —
-- the backend must still filter every query by user_id.

create extension if not exists pgcrypto;

-- ---------- enums ----------
create type goal_status   as enum ('interviewing', 'planning', 'active', 'paused', 'done', 'abandoned');
create type task_kind     as enum ('learn', 'practice', 'project', 'revise', 'assess', 'buffer');
create type task_status   as enum ('pending', 'scheduled', 'done', 'partial', 'missed', 'dropped');
create type sprint_status as enum ('planned', 'active', 'done');

-- ---------- goals ----------
create table goals (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  title       text not null,
  category    text,                          -- filled after classification
  status      goal_status not null default 'interviewing',
  deadline    date,
  profile     jsonb not null default '{}',   -- interview answers: current level, target, etc.
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- Raw interview turns (useful to replay/debug the question generator)
create table interview_messages (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  goal_id     uuid not null references goals(id) on delete cascade,
  role        text not null check (role in ('assistant', 'user')),
  content     text not null,
  created_at  timestamptz not null default now()
);

-- ---------- blueprint ----------
-- Versioned: a replan that changes scope creates a new version, old ones kept.
create table blueprints (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  goal_id     uuid not null references goals(id) on delete cascade,
  version     int  not null,
  is_active   boolean not null default true,
  data        jsonb not null,                -- full validated GoalBlueprint JSON
  model       text,                          -- which LLM produced it
  created_at  timestamptz not null default now(),
  unique (goal_id, version)
);

create table milestones (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  goal_id       uuid not null references goals(id) on delete cascade,
  blueprint_id  uuid not null references blueprints(id) on delete cascade,
  key           text not null,
  name          text not null,
  position      int  not null,               -- topological order
  estimated_hours numeric(6,2) not null,
  confidence    numeric(3,2) check (confidence between 0 and 1),
  done_criteria jsonb not null default '[]',
  unique (blueprint_id, key)
);

create table milestone_dependencies (
  milestone_id   uuid not null references milestones(id) on delete cascade,
  depends_on_id  uuid not null references milestones(id) on delete cascade,
  user_id        uuid not null references auth.users(id) on delete cascade,
  primary key (milestone_id, depends_on_id),
  check (milestone_id <> depends_on_id)
);

-- ---------- resources ----------
create table resources (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  goal_id       uuid references goals(id) on delete set null,
  kind          text not null,               -- course, book, playlist, question_bank, notes...
  title         text not null,
  url           text,
  raw_input     text,                        -- what the user typed
  units         jsonb not null default '{}', -- structured by LLM: {videos: 40, minutes: 720, problems: 150}
  created_at    timestamptz not null default now()
);

create table resource_milestones (
  resource_id   uuid not null references resources(id) on delete cascade,
  milestone_id  uuid not null references milestones(id) on delete cascade,
  user_id       uuid not null references auth.users(id) on delete cascade,
  coverage      numeric(3,2) check (coverage between 0 and 1),
  primary key (resource_id, milestone_id)
);

-- ---------- capacity ----------
-- Weekly recurring free slots. weekday: 0 = Monday ... 6 = Sunday
create table availability_slots (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  weekday     smallint not null check (weekday between 0 and 6),
  start_time  time not null,
  end_time    time not null,
  check (end_time > start_time)
);

-- One-off exceptions: "only 1h next Tuesday", "travelling this weekend"
create table availability_overrides (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  day         date not null,
  minutes     int  not null check (minutes >= 0),
  reason      text
);

-- ---------- plan ----------
create table sprints (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references auth.users(id) on delete cascade,
  goal_id          uuid not null references goals(id) on delete cascade,
  number           int  not null,
  start_date       date not null,
  end_date         date not null,
  objective        text,
  capacity_minutes int  not null,
  definition_of_done jsonb not null default '[]',
  status           sprint_status not null default 'planned',
  unique (goal_id, number)
);

create table tasks (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references auth.users(id) on delete cascade,
  goal_id           uuid not null references goals(id) on delete cascade,
  milestone_id      uuid references milestones(id) on delete set null,
  sprint_id         uuid references sprints(id) on delete set null,
  resource_id       uuid references resources(id) on delete set null,
  title             text not null,
  kind              task_kind not null,
  estimated_minutes int  not null check (estimated_minutes > 0),
  priority          smallint not null default 3,     -- 1 = highest
  status            task_status not null default 'pending',
  done_criteria     text,
  created_at        timestamptz not null default now()
);

create table task_dependencies (
  task_id        uuid not null references tasks(id) on delete cascade,
  depends_on_id  uuid not null references tasks(id) on delete cascade,
  user_id        uuid not null references auth.users(id) on delete cascade,
  primary key (task_id, depends_on_id),
  check (task_id <> depends_on_id)
);

-- The daily schedule. Separate from tasks so reschedules keep history:
-- a moved task gets a new block; the old block is marked superseded.
create table scheduled_blocks (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  task_id       uuid not null references tasks(id) on delete cascade,
  day           date not null,
  start_time    time not null,
  minutes       int  not null check (minutes > 0),
  superseded    boolean not null default false,
  created_at    timestamptz not null default now()
);

-- ---------- execution log (the future ML dataset) ----------
-- Never update/delete rows here; append only.
create table task_executions (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references auth.users(id) on delete cascade,
  task_id             uuid not null references tasks(id) on delete cascade,
  block_id            uuid references scheduled_blocks(id) on delete set null,
  outcome             task_status not null check (outcome in ('done', 'partial', 'missed')),
  estimated_minutes   int not null,          -- snapshot of the estimate at that time
  actual_minutes      int,                   -- null when missed
  completed_fraction  numeric(3,2),          -- for partial
  logged_at           timestamptz not null default now(),
  note                text
);

-- Every planning decision, for "why this task?" and debugging replans
create table planning_events (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  goal_id     uuid references goals(id) on delete cascade,
  type        text not null,                 -- TASK_MISSED, REPLAN_TRIGGERED, PLAN_GENERATED...
  payload     jsonb not null default '{}',
  created_at  timestamptz not null default now()
);

-- ---------- indexes ----------
create index on goals (user_id);
create index on tasks (goal_id, status);
create index on tasks (sprint_id);
create index on scheduled_blocks (user_id, day) where not superseded;
create index on task_executions (user_id, logged_at);
create index on planning_events (goal_id, created_at);

-- ---------- row level security ----------
do $$
declare t text;
begin
  foreach t in array array[
    'goals','interview_messages','blueprints','milestones','milestone_dependencies',
    'resources','resource_milestones','availability_slots','availability_overrides',
    'sprints','tasks','task_dependencies','scheduled_blocks','task_executions','planning_events'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format(
      'create policy own_rows on %I for all using (user_id = auth.uid()) with check (user_id = auth.uid())',
      t
    );
  end loop;
end $$;
