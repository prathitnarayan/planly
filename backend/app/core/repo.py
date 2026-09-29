"""
Where goals live. One interface, two implementations:

  InMemoryRepo   local dev + tests. Lost on restart.
  PostgresRepo   Supabase Postgres (database/migrations/001_planly.sql).

EVERY method takes user_id and EVERY query filters by it. The backend's database
connection bypasses Supabase RLS, so this filter is what keeps users apart.
(RLS is still on, as a second lock for anything that talks to the DB directly.)

Both return COPIES: changing a record does nothing until you call save(). That keeps
the in-memory version honest — code that "works" only because it mutated a shared
object would silently fail on the real database.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Protocol

from pydantic import BaseModel, Field

from app.ai.goal_intake import InterviewState
from app.planners.capacity import CapacityProfile
from app.planners.progress import CheckIn
from app.schemas.blueprint import GoalBlueprint
from app.planners.integrity import Integrity, WatchEvidence, merge_intervals
from app.planners.learning import Learned, Outcome
from app.schemas.source import CourseSource


class Ticks(BaseModel):
    """Checkbox state for days that aren't closed yet, plus notes from the last close."""
    days: dict[str, list[str]] = Field(default_factory=dict)   # "2026-09-29" -> ticked session ids
    at: dict[str, datetime] = Field(default_factory=dict)      # session id -> when it was ticked
    auto: list[str] = Field(default_factory=list)              # session ids ticked by the evidence
    notes: list[str] = Field(default_factory=list)             # "Tue 29 Sep: 45 min of X not done..."
    notes_day: date | None = None                              # the day those notes are for


class GoalRecord(BaseModel):
    id: str
    interview: InterviewState
    blueprint: GoalBlueprint | None = None
    capacity: CapacityProfile | None = None
    checkins: list[CheckIn] = Field(default_factory=list)
    plan_start: date | None = None        # first planned day (set when capacity is first saved)
    checked_through: date | None = None   # last day the user has checked in for
    sources: list[CourseSource] = Field(default_factory=list)   # synced course pages (003)
    ticks: "Ticks" = Field(default_factory=lambda: Ticks())      # today's checkboxes (004)
    integrity: Integrity = Field(default_factory=Integrity)     # trust, streak, penalties (005)

    @property
    def title(self) -> str:
        return self.interview.goal


class UserSettings(BaseModel):
    """Per user, across all goals (006)."""
    capacity: CapacityProfile | None = None     # ONE pool of free time shared by every goal
    goal_order: list[str] = Field(default_factory=list)   # priority: first goal plans first
    learned: Learned = Field(default_factory=Learned)     # how this user really works (learning.py)


class GoalSummary(BaseModel):
    id: str
    title: str
    interview_done: bool
    has_blueprint: bool
    has_capacity: bool
    checkins: int
    updated_at: datetime


class GoalRepo(Protocol):
    def create(self, user_id: str, interview: InterviewState) -> GoalRecord: ...
    def get(self, user_id: str, goal_id: str) -> GoalRecord | None: ...
    def list(self, user_id: str) -> list[GoalSummary]: ...
    def save(self, user_id: str, rec: GoalRecord) -> None: ...
    def add_checkins(self, user_id: str, goal_id: str, checkins: list[CheckIn]) -> None: ...
    def log_correction(self, user_id: str, goal_id: str | None, row: dict) -> None: ...
    def delete(self, user_id: str, goal_id: str) -> bool: ...
    def merge_watch(self, user_id: str, events: list[WatchEvidence]) -> dict[str, WatchEvidence]: ...
    def get_settings(self, user_id: str) -> UserSettings: ...
    def save_settings(self, user_id: str, settings: UserSettings) -> None: ...
    def add_outcomes(self, user_id: str, goal_id: str, rows: list[Outcome]) -> None: ...
    def list_outcomes(self, user_id: str, since: date) -> list[Outcome]: ...
    def get_watch(self, user_id: str, keys: list[str]) -> dict[str, WatchEvidence]: ...


def _merged(old: WatchEvidence | None, ev: WatchEvidence) -> WatchEvidence:
    """Watch evidence only ever grows: played ranges are unioned, never replaced."""
    duration = max(ev.duration_s, old.duration_s if old else 0)
    ranges = (old.intervals if old else []) + ev.intervals
    return WatchEvidence(key=ev.key, url=ev.url or (old.url if old else None),
                         title=ev.title or (old.title if old else None), duration_s=duration,
                         intervals=merge_intervals(ranges, duration),
                         active_s=(old.active_s if old else 0.0) + ev.active_s,   # time adds up, ranges union
                         updated_at=datetime.now(timezone.utc))


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


# ---------- in memory ----------

class InMemoryRepo:
    def __init__(self) -> None:
        self._goals: dict[tuple[str, str], GoalRecord] = {}
        self._updated: dict[tuple[str, str], datetime] = {}
        self.corrections: list[dict] = []
        self.watch: dict[tuple[str, str], WatchEvidence] = {}
        self.settings: dict[str, UserSettings] = {}
        self.outcomes: list[tuple[str, Outcome]] = []

    def get_settings(self, user_id):
        return (self.settings.get(user_id) or UserSettings()).model_copy(deep=True)

    def save_settings(self, user_id, settings):
        self.settings[user_id] = settings.model_copy(deep=True)

    def add_outcomes(self, user_id, goal_id, rows):
        self.outcomes += [(user_id, r.model_copy(update={"goal_id": goal_id})) for r in rows]

    def list_outcomes(self, user_id, since):
        return [o.model_copy() for u, o in self.outcomes if u == user_id and o.day >= since]

    def merge_watch(self, user_id, events):
        out = {}
        for ev in events:
            out[ev.key] = self.watch[(user_id, ev.key)] = _merged(self.watch.get((user_id, ev.key)), ev)
        return {k: v.model_copy(deep=True) for k, v in out.items()}

    def get_watch(self, user_id, keys):
        return {k: self.watch[(user_id, k)].model_copy(deep=True) for k in keys if (user_id, k) in self.watch}

    def create(self, user_id, interview):
        rec = GoalRecord(id=str(uuid.uuid4()), interview=interview)
        self.save(user_id, rec)
        return rec.model_copy(deep=True)

    def get(self, user_id, goal_id):
        rec = self._goals.get((user_id, goal_id))
        return rec.model_copy(deep=True) if rec else None

    def list(self, user_id):
        rows = [(k[1], r) for k, r in self._goals.items() if k[0] == user_id]
        out = [_summary(gid, r, self._updated[(user_id, gid)]) for gid, r in rows]
        return sorted(out, key=lambda s: s.updated_at, reverse=True)

    def save(self, user_id, rec):
        key = (user_id, rec.id)
        checkins = self._goals[key].checkins if key in self._goals else []
        # check-ins are append-only and only change via add_checkins (same as the DB)
        self._goals[key] = rec.model_copy(deep=True, update={"checkins": checkins})
        self._updated[key] = datetime.now(timezone.utc)

    def add_checkins(self, user_id, goal_id, checkins):
        key = (user_id, goal_id)
        if key not in self._goals:
            raise KeyError(goal_id)
        self._goals[key].checkins += [c.model_copy() for c in checkins]
        self._updated[key] = datetime.now(timezone.utc)

    def log_correction(self, user_id, goal_id, row):
        self.corrections.append({**row, "user_id": user_id, "goal_id": goal_id})

    def delete(self, user_id, goal_id):
        self._updated.pop((user_id, goal_id), None)
        return self._goals.pop((user_id, goal_id), None) is not None


def _summary(goal_id: str, rec: GoalRecord, updated: datetime) -> GoalSummary:
    return GoalSummary(
        id=goal_id, title=rec.title, interview_done=rec.interview.done,
        has_blueprint=rec.blueprint is not None, has_capacity=rec.capacity is not None,
        checkins=len(rec.checkins), updated_at=updated,
    )


# ---------- Postgres (Supabase) ----------

class DatabaseUnavailable(RuntimeError):
    pass


def _explain(dsn: str) -> str:
    """A useful error message WITHOUT printing the password."""
    # Parse by hand: a "[YOUR-PASSWORD]" placeholder confuses urlsplit (brackets = IPv6).
    import re
    m = re.match(r"^\w+://([^:@/]*)(?::[^@]*)?@(\[[^\]]+\]|[^:/?]+)", dsn)
    user, host = (m.group(1) or "?", m.group(2)) if m else ("?", "?")
    hints = ["Couldn't connect to the database (DATABASE_URL). Stopped instead of retrying,",
             "so Supabase doesn't block you for repeated failed logins.",
             f"  user: {user}   host: {host}"]
    if "[" in dsn or "YOUR-PASSWORD" in dsn.upper():
        hints.append("  -> The [YOUR-PASSWORD] placeholder is still in DATABASE_URL (replace it, brackets too).")
    if "pooler.supabase.com" not in host:
        hints.append("  -> Not the Session pooler. Use Connect -> Session pooler (user looks like postgres.<ref>).")
    hints.append("  -> Wrong password? Reset it (Settings -> Database) using only letters and digits.")
    return "\n".join(hints)

class PostgresRepo:
    def __init__(self, dsn: str, pool_size: int = 5, connect_timeout: float = 10) -> None:
        from psycopg_pool import ConnectionPool, PoolTimeout

        # prepare_threshold=None: Supabase's poolers don't support prepared statements
        self.pool = ConnectionPool(
            dsn, min_size=1, max_size=pool_size, open=True,
            kwargs={"prepare_threshold": None, "autocommit": False},
        )
        # Fail fast and loudly. Real incident (28 Sep): a wrong password made the pool
        # retry in the background until Supabase temporarily blocked the IP.
        try:
            self.pool.wait(timeout=connect_timeout)
        except PoolTimeout as e:
            self.pool.close()
            raise DatabaseUnavailable(_explain(dsn)) from e

    @staticmethod
    def _json(model: BaseModel | None):
        from psycopg.types.json import Jsonb
        return Jsonb(model.model_dump(mode="json")) if model is not None else None

    @staticmethod
    def _jsonlist(models: list[BaseModel]):
        from psycopg.types.json import Jsonb
        return Jsonb([m.model_dump(mode="json") for m in models])

    def create(self, user_id, interview):
        with self.pool.connection() as conn:
            row = conn.execute(
                "insert into public.goals (user_id, title, interview) values (%s, %s, %s) returning id",
                (user_id, interview.goal, self._json(interview)),
            ).fetchone()
        return GoalRecord(id=str(row[0]), interview=interview)

    def get(self, user_id, goal_id):
        if not _valid_uuid(goal_id):
            return None
        with self.pool.connection() as conn:
            row = conn.execute(
                """select interview, blueprint, capacity, plan_start, checked_through, sources, ticks, integrity
                   from public.goals where id = %s and user_id = %s""",
                (goal_id, user_id),
            ).fetchone()
            if row is None:
                return None
            checkins = conn.execute(
                """select day, milestone_key, outcome, planned_minutes, actual_minutes,
                          milestone_complete, remaining_minutes, note
                   from public.checkins where goal_id = %s and user_id = %s
                   order by day, created_at""",
                (goal_id, user_id),
            ).fetchall()
        return GoalRecord(
            id=goal_id,
            interview=InterviewState.model_validate(row[0]),
            blueprint=GoalBlueprint.model_validate(row[1]) if row[1] else None,
            capacity=CapacityProfile.model_validate(row[2]) if row[2] else None,
            plan_start=row[3],
            checked_through=row[4],
            sources=[CourseSource.model_validate(x) for x in (row[5] or [])],
            ticks=Ticks.model_validate(row[6] or {}),
            integrity=Integrity.model_validate(row[7] or {}),
            checkins=[
                CheckIn(day=c[0], milestone_key=c[1], outcome=c[2], planned_minutes=c[3],
                        actual_minutes=c[4], milestone_complete=c[5], remaining_minutes=c[6], note=c[7])
                for c in checkins
            ],
        )

    def list(self, user_id):
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select g.id, g.title, (g.interview->>'done')::boolean, g.blueprint is not null,
                          g.capacity is not null,
                          (select count(*) from public.checkins c where c.goal_id = g.id and c.user_id = %s),
                          g.updated_at
                   from public.goals g where g.user_id = %s order by g.updated_at desc""",
                (user_id, user_id),
            ).fetchall()
        return [
            GoalSummary(id=str(r[0]), title=r[1], interview_done=bool(r[2]), has_blueprint=r[3],
                        has_capacity=r[4], checkins=r[5], updated_at=r[6])
            for r in rows
        ]

    def save(self, user_id, rec):
        with self.pool.connection() as conn:
            cur = conn.execute(
                """update public.goals set title = %s, interview = %s, blueprint = %s, capacity = %s,
                          plan_start = %s, checked_through = %s, sources = %s, ticks = %s, integrity = %s
                   where id = %s and user_id = %s""",
                (rec.title, self._json(rec.interview), self._json(rec.blueprint),
                 self._json(rec.capacity), rec.plan_start, rec.checked_through,
                 self._jsonlist(rec.sources), self._json(rec.ticks), self._json(rec.integrity),
                 rec.id, user_id),
            )
            if cur.rowcount != 1:
                raise KeyError(rec.id)

    def add_checkins(self, user_id, goal_id, checkins):
        with self.pool.connection() as conn:
            owned = conn.execute(
                "select 1 from public.goals where id = %s and user_id = %s", (goal_id, user_id)
            ).fetchone()
            if not owned:
                raise KeyError(goal_id)
            with conn.cursor() as cur:
                cur.executemany(
                    """insert into public.checkins
                         (user_id, goal_id, day, milestone_key, outcome, planned_minutes,
                          actual_minutes, milestone_complete, remaining_minutes, note)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [(user_id, goal_id, c.day, c.milestone_key, c.outcome, c.planned_minutes,
                      c.actual_minutes, c.milestone_complete, c.remaining_minutes, c.note)
                     for c in checkins],
                )
            conn.execute("update public.goals set updated_at = now() where id = %s", (goal_id,))

    def log_correction(self, user_id, goal_id, row):
        with self.pool.connection() as conn:
            conn.execute(
                """insert into public.estimate_corrections
                     (user_id, goal_id, milestone_key, milestone_name, ai_hours, user_hours,
                      ratio, ai_confidence, had_benchmark, source)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (user_id, goal_id, row["milestone_key"], row["milestone_name"], row["ai_hours"],
                 row["user_hours"], row["ratio"], row["ai_confidence"], row["had_benchmark"], row["source"]),
            )

    def delete(self, user_id, goal_id):
        if not _valid_uuid(goal_id):
            return False
        with self.pool.connection() as conn:
            cur = conn.execute("delete from public.goals where id = %s and user_id = %s", (goal_id, user_id))
            return cur.rowcount == 1

    def get_watch(self, user_id, keys):
        if not keys:
            return {}
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select video_key, url, title, duration_s, intervals, updated_at, active_s
                   from public.watch_evidence where user_id = %s and video_key = any(%s)""",
                (user_id, list(keys)),
            ).fetchall()
        return {r[0]: WatchEvidence(key=r[0], url=r[1], title=r[2], duration_s=r[3],
                                    intervals=[tuple(x) for x in (r[4] or [])], updated_at=r[5],
                                    active_s=r[6] or 0.0) for r in rows}

    def merge_watch(self, user_id, events):
        from psycopg.types.json import Jsonb
        old = self.get_watch(user_id, [e.key for e in events])
        out = {}
        with self.pool.connection() as conn:
            for ev in events:
                m = _merged(old.get(ev.key), ev)
                old[ev.key] = out[ev.key] = m
                conn.execute(
                    """insert into public.watch_evidence (user_id, video_key, url, title, duration_s, intervals, active_s, updated_at)
                       values (%s, %s, %s, %s, %s, %s, %s, now())
                       on conflict (user_id, video_key) do update set url = excluded.url, title = excluded.title,
                         duration_s = excluded.duration_s, intervals = excluded.intervals,
                         active_s = excluded.active_s, updated_at = now()""",
                    (user_id, m.key, m.url, m.title, m.duration_s, Jsonb([list(x) for x in m.intervals]), m.active_s),
                )
        return out

    def get_settings(self, user_id):
        with self.pool.connection() as conn:
            row = conn.execute("select capacity, goal_order, learned from public.user_settings where user_id = %s",
                               (user_id,)).fetchone()
        if not row:
            return UserSettings()
        return UserSettings(capacity=CapacityProfile.model_validate(row[0]) if row[0] else None,
                            goal_order=row[1] or [], learned=Learned.model_validate(row[2] or {}))

    def save_settings(self, user_id, settings):
        from psycopg.types.json import Jsonb
        with self.pool.connection() as conn:
            conn.execute(
                """insert into public.user_settings (user_id, capacity, goal_order, learned, updated_at)
                   values (%s, %s, %s, %s, now())
                   on conflict (user_id) do update set capacity = excluded.capacity,
                     goal_order = excluded.goal_order, learned = excluded.learned, updated_at = now()""",
                (user_id, self._json(settings.capacity), Jsonb(settings.goal_order), self._json(settings.learned)),
            )

    def add_outcomes(self, user_id, goal_id, rows):
        if not rows:
            return
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "insert into public.outcomes (user_id, goal_id, day, data) values (%s, %s, %s, %s)",
                [(user_id, goal_id, r.day, self._json(r)) for r in rows],
            )

    def list_outcomes(self, user_id, since):
        with self.pool.connection() as conn:
            rows = conn.execute(
                "select goal_id, data from public.outcomes where user_id = %s and day >= %s order by day",
                (user_id, since),
            ).fetchall()
        return [Outcome.model_validate({**r[1], "goal_id": str(r[0]) if r[0] else None}) for r in rows]

    def close(self) -> None:
        self.pool.close()
