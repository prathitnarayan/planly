-- Planly migration 002: remember when a plan starts and how far the user has checked in.
-- Run AFTER 001_planly.sql: SQL Editor -> New query -> paste -> Run. Safe to run twice.
alter table public.goals add column if not exists plan_start date;       -- first planned day
alter table public.goals add column if not exists checked_through date;  -- last day checked in for
