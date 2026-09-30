"""Settings loaded from backend/.env (never commit that file)."""

import os

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Any chat model that supports JSON mode works. Change in .env, not in code.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
# Any OpenAI-compatible provider. Empty = api.openai.com.
# AI Pipe: https://aipipe.org/openai/v1
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None

MAX_INTERVIEW_QUESTIONS = int(os.getenv("MAX_INTERVIEW_QUESTIONS", "6"))

# ---------- Supabase (leave empty for local dev: in-memory data, no login) ----------
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")        # only for scripts/login.py
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")    # only for LEGACY HS256 projects
DATABASE_URL = os.getenv("DATABASE_URL", "")                  # Session pooler connection string

# "supabase" = real login required. "dev" = everyone is one fixed dev user (local only!).
AUTH_MODE = "supabase" if SUPABASE_URL else "dev"
# A real database with no login would let anyone read everyone's plans. Refuse, unless
# you explicitly say this is a throwaway local database.
ALLOW_DEV_AUTH_WITH_DB = os.getenv("ALLOW_DEV_AUTH_WITH_DB", "") == "1"


def check_safe_config() -> None:
    if DATABASE_URL and AUTH_MODE == "dev" and not ALLOW_DEV_AUTH_WITH_DB:
        raise RuntimeError(
            "DATABASE_URL is set but SUPABASE_URL is empty: that would run a real database "
            "with no login. Set SUPABASE_URL (or ALLOW_DEV_AUTH_WITH_DB=1 for a throwaway local DB)."
        )

# Browser origins allowed to call the API (the Next.js frontend). Comma-separated.
FRONTEND_ORIGINS = [
    o.strip() for o in os.getenv("FRONTEND_ORIGINS", "http://localhost:3000").split(",") if o.strip()
]

# Where the laptop sync tool (backend/sync) sends course pages. Your Render URL, or local.
PLANLY_API_URL = os.getenv("PLANLY_API_URL", "http://localhost:8000").rstrip("/")

# Optional: exact YouTube durations for playlists pasted in the app and videos embedded in
# course pages. Free key: Google Cloud Console -> YouTube Data API v3 -> Credentials.
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# ---------- integrations (all optional; features hide themselves when unset) ----------
# Where this API is reachable from the internet, e.g. https://planly-u3y3.onrender.com
PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", "").rstrip("/")
# Where the web app lives (Google sign-in returns there). Default: first FRONTEND_ORIGINS entry.
FRONTEND_URL = os.getenv("FRONTEND_URL", "").rstrip("/") or (FRONTEND_ORIGINS[0] if FRONTEND_ORIGINS else "")
# Signs short-lived OAuth state and encrypts stored Google tokens. Any long random string.
APP_SECRET = os.getenv("APP_SECRET", "")
# Protects POST /cron/notify (called every 15 min by GitHub Actions / cron-job.org).
CRON_SECRET = os.getenv("CRON_SECRET", "")
# Telegram bot from @BotFather
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").lstrip("@")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
# Google Calendar, read-only free/busy (Google Cloud Console -> OAuth client, type "Web application")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
