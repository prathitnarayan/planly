-- 003: course pages synced by the laptop sync tool (backend/sync), per goal.
-- Stored as a JSON list on the goal, like interview / blueprint / capacity.
-- Safe to run twice.
alter table public.goals add column if not exists sources jsonb not null default '[]'::jsonb;
