-- 005: verification. Safe to run twice.
-- Trust score, streak, owed minutes and verdict history per goal.
alter table public.goals add column if not exists integrity jsonb not null default '{}'::jsonb;

-- Watch evidence from the Chrome extension: which parts of each video actually played.
-- Only collected on sites the user switched on. Ranges only ever grow (union).
create table if not exists public.watch_evidence (
  user_id    uuid not null default auth.uid() references auth.users(id) on delete cascade,
  video_key  text not null,            -- "yt:<id>" or "page:<url>#n"
  url        text,
  title      text,
  duration_s real not null,
  intervals  jsonb not null default '[]'::jsonb,   -- [[from_s, to_s], ...] played media time
  updated_at timestamptz not null default now(),
  primary key (user_id, video_key)
);
alter table public.watch_evidence enable row level security;
drop policy if exists watch_own on public.watch_evidence;
create policy watch_own on public.watch_evidence
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
