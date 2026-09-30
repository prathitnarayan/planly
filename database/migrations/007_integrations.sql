-- 007: Telegram notifications + Google Calendar (read-only free/busy). Safe to run twice.
alter table public.user_settings add column if not exists timezone text not null default 'Asia/Kolkata';
alter table public.user_settings add column if not exists integrations jsonb not null default '{}'::jsonb;
-- looked up by the Telegram webhook (chat -> user) and the one-time link code
alter table public.user_settings add column if not exists telegram_chat_id bigint;
alter table public.user_settings add column if not exists link_code text;
create unique index if not exists user_settings_chat on public.user_settings (telegram_chat_id)
  where telegram_chat_id is not null;
create unique index if not exists user_settings_link_code on public.user_settings (link_code)
  where link_code is not null;
