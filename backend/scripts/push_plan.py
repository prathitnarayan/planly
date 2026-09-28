"""
Copy your saved terminal plan (data/plan.json, incl. check-ins) into Supabase,
under your account. Run once, after scripts.login works.

    python -m scripts.push_plan
"""

from __future__ import annotations

import getpass
import sys

import httpx

from app.ai.goal_intake import InterviewState
from app.core import config, plan_file
from app.core.auth import verify_token
from app.core.repo import PostgresRepo


def main() -> None:
    if not (config.SUPABASE_URL and config.SUPABASE_ANON_KEY and config.DATABASE_URL):
        sys.exit("Set SUPABASE_URL, SUPABASE_ANON_KEY and DATABASE_URL in backend/.env first.")
    plan = plan_file.load()
    if plan is None:
        sys.exit("No data/plan.json — run python -m scripts.try_goal and save a plan first.")

    # sign in, so the plan is stored under YOUR user id (verified, not typed in)
    email = input("Email: ").strip()
    password = getpass.getpass("Password (hidden): ")
    res = httpx.post(f"{config.SUPABASE_URL}/auth/v1/token?grant_type=password",
                     json={"email": email, "password": password},
                     headers={"apikey": config.SUPABASE_ANON_KEY}, timeout=20)
    if res.status_code >= 300:
        sys.exit(f"Sign-in failed: {res.text}")
    user_id = verify_token(res.json()["access_token"], config.SUPABASE_URL, config.SUPABASE_JWT_SECRET)

    interview = InterviewState(goal=plan.goal, profile=plan.profile, done=True)
    repo = PostgresRepo(config.DATABASE_URL, pool_size=1)
    try:
        rec = repo.create(user_id, interview)
        rec.blueprint, rec.capacity = plan.blueprint, plan.capacity
        rec.plan_start, rec.checked_through = plan.start, plan.checked_through
        repo.save(user_id, rec)
        if plan.checkins:
            repo.add_checkins(user_id, rec.id, plan.checkins)
    finally:
        repo.close()
    print(f"\n✅ Uploaded '{plan.goal}' with {len(plan.checkins)} check-ins. Goal id: {rec.id}")
    print("It's in Supabase now (Table Editor -> goals). The local plan.json is unchanged.")


if __name__ == "__main__":
    main()
