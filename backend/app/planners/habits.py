"""
Habit streaks. Pure code: inputs in, numbers out. No DB, no network, no AI.

Two kinds of habit:
  quit   "no smoking", "no Instagram after 11pm"   a kept day = clean
  build  "gym", "sleep by 12"                        a kept day = done

Every day is answered yes (kept) or no (slipped), or left blank. Rules:
- A slip resets the CURRENT streak, but the best streak and "26 of the last 30 days" stay.
  All-or-nothing streaks make people quit after the first slip; the 30-day record stops that.
- A blank day neither counts nor breaks the streak (nobody is punished for not answering).
- Nothing here is verified, so nothing is ever penalised. It's self-report, used only to encourage.

Personal messages come ONLY from the user's own data: their streaks, their slip days, the times
they log urges, and their own "why" line. Never from age, personality or anything guessed.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Kind = Literal["quit", "build"]

WINDOW_DAYS = 30               # "26 of the last 30 days"
URGE_LOOKBACK_DAYS = 60        # urges older than this don't shape the nudge time
SLIP_LOOKBACK_DAYS = 90
MIN_URGES = 4                  # need this many urges before guessing a risky time
MIN_URGES_IN_WINDOW = 3
MIN_WINDOW_SHARE = 0.4         # the 2-hour window must hold >= 40% of all urges
MIN_SLIPS = 3                  # need this many slips before naming a risky weekday
MIN_WEEKDAY_SHARE = 0.4
NUDGE_LEAD = timedelta(minutes=30)
MILESTONES = {3: "a real start", 7: "one full week", 14: "two weeks", 21: "three weeks", 30: "a whole month",
              50: "halfway to a hundred", 60: "two months", 90: "three months", 100: "triple digits", 180: "half a year",
              365: "a full year"}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MAX_HABITS = 10


class Habit(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1, max_length=80)
    kind: Kind = "quit"
    why: str | None = Field(default=None, max_length=200)     # their own words, quoted back on hard days
    label: str | None = Field(default=None, max_length=30)    # shown in notifications instead of the name
    started: date
    archived: bool = False
    nudge: bool = True                                         # heads-up before their usual risky time

    @field_validator("name", "why", "label", mode="before")
    @classmethod
    def _strip(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @property
    def shown(self) -> str:
        """What notifications show: the private label if set (lock screens are public)."""
        return self.label or self.name


class HabitDay(BaseModel):
    habit_id: str
    day: date
    kept: bool


class Urge(BaseModel):
    habit_id: str
    at: datetime                 # UTC
    local: datetime              # the user's wall-clock time when it happened (naive)


class StripDay(BaseModel):
    day: date
    state: Literal["kept", "slip", "blank", "before"]


class HabitStats(BaseModel):
    current: int                 # kept days since the last slip (blanks skipped)
    best: int                    # longest run ever, including the current one
    previous_best: int           # longest run BEFORE the current one (for "new record")
    kept_window: int             # kept days in the window
    window: int                  # days in the window (ends yesterday if today isn't answered yet)
    slips_window: int
    total_kept: int
    last_slip: date | None
    today: bool | None           # today's answer
    yesterday: bool | None
    strip: list[StripDay]        # the last 30 days, oldest first


def stats(habit: Habit, days: dict[date, bool], today: date) -> HabitStats:
    cur = best = prev_best = total = 0
    last_slip = None
    d = habit.started
    while d <= today:
        if d in days:
            if days[d]:
                cur += 1
                total += 1
                best = max(best, cur)
            else:
                prev_best = max(prev_best, cur)
                cur = 0
                last_slip = d
        d += timedelta(days=1)
    # previous_best = the best run that has already ENDED (the current one is still going)
    prev_best = max(prev_best, 0)
    end = today if today in days else today - timedelta(days=1)
    first = max(habit.started, end - timedelta(days=WINDOW_DAYS - 1))
    span = (end - first).days + 1 if end >= first else 0
    kept_w = sum(1 for x, k in days.items() if k and first <= x <= end)
    slips_w = sum(1 for x, k in days.items() if not k and first <= x <= end)
    strip = []
    for i in range(WINDOW_DAYS - 1, -1, -1):
        x = today - timedelta(days=i)
        state = ("before" if x < habit.started else "kept" if days.get(x) is True
                 else "slip" if days.get(x) is False else "blank")
        strip.append(StripDay(day=x, state=state))
    return HabitStats(current=cur, best=best, previous_best=prev_best, kept_window=kept_w, window=span,
                      slips_window=slips_w, total_kept=total, last_slip=last_slip, today=days.get(today),
                      yesterday=days.get(today - timedelta(days=1)) if today > habit.started else None,
                      strip=strip)


# ---------- the user's own patterns ----------

class RiskTime(BaseModel):
    """A 2-hour window when most of their urges happen, e.g. 22:00-24:00."""
    hour: int                    # start of the window (local)
    in_window: int
    total: int

    @property
    def label(self) -> str:
        return f"{self.hour:02d}:00–{(self.hour + 2) % 24:02d}:00"

    def nudge_at(self, day: date) -> datetime:
        """Local (naive) time to send the heads-up: 30 min before the window starts."""
        return datetime.combine(day, datetime.min.time()).replace(hour=self.hour) - NUDGE_LEAD


def risk_time(urges: list[Urge], today: date) -> RiskTime | None:
    since = today - timedelta(days=URGE_LOOKBACK_DAYS)
    hours = [u.local.hour for u in urges if u.local.date() >= since]
    if len(hours) < MIN_URGES:
        return None
    c = Counter(hours)
    window = {h: c[h] + c[(h + 1) % 24] for h in range(24)}
    # ties: the earlier hour (a heads-up too early beats one too late)
    h = max(range(24), key=lambda x: (window[x], -x))
    n = window[h]
    if n < MIN_URGES_IN_WINDOW or n / len(hours) < MIN_WINDOW_SHARE:
        return None
    return RiskTime(hour=h, in_window=n, total=len(hours))


class RiskDay(BaseModel):
    weekday: int
    slips: int
    total: int

    @property
    def name(self) -> str:
        return WEEKDAYS[self.weekday]


def risk_day(days: dict[date, bool], today: date) -> RiskDay | None:
    since = today - timedelta(days=SLIP_LOOKBACK_DAYS)
    slips = [d.weekday() for d, k in days.items() if not k and since <= d <= today]
    if len(slips) < MIN_SLIPS:
        return None
    wd, n = Counter(slips).most_common(1)[0]
    if n < 2 or n / len(slips) < MIN_WEEKDAY_SHARE:
        return None
    return RiskDay(weekday=wd, slips=n, total=len(slips))


def word(habit: Habit) -> str:
    return "clean" if habit.kind == "quit" else "done"


def lines(habit: Habit, st: HabitStats, today: date, rday: RiskDay | None) -> list[str]:
    """Personal lines, most useful first. Every number is theirs."""
    out = []
    if st.yesterday is False:
        if st.window:
            out.append(f"Yesterday slipped. You're still {st.kept_window} of the last {st.window} days "
                       f"{word(habit)} — one day doesn't undo that.")
        else:
            out.append("Yesterday slipped. Today counts the same as any other day.")
    if st.current and st.current == st.best and st.previous_best and st.current > st.previous_best:
        out.append(f"New best: {st.current} days (old best {st.previous_best}).")
    elif st.current in MILESTONES:
        out.append(f"{st.current} days {word(habit)} — {MILESTONES[st.current]}.")
    if rday and rday.weekday == today.weekday():
        out.append(f"{rday.name}s are when most of your slips happen ({rday.slips} of your last {rday.total}). "
                   f"Today's a {rday.name} — plan around it.")
    if habit.why:
        out.append(f"Your reason: “{habit.why}”")
    return out


def urge_reply(habit: Habit, st: HabitStats, urges_today: int) -> str:
    parts = [f"Logged. {st.current} day{'s' if st.current != 1 else ''} so far"
             + (f" — your best is {st.best}." if st.best > st.current else ".")]
    if habit.why:
        parts.append(f"Your reason: “{habit.why}”")
    parts.append("Urges usually fade if you wait them out. Give it 15 minutes: water, a walk, anything else.")
    if urges_today > 1:
        parts.append(f"That's {urges_today} today, and you're still here. That counts.")
    return "\n".join(parts)


# ---------- the daily quote ----------
# Short lines from long-dead writers (and proverbs), so no one's recent work is copied.
QUOTES: list[tuple[str, str]] = [
    ("A journey of a thousand miles begins with a single step.", "Lao Tzu"),
    ("Great acts are made up of small deeds.", "Lao Tzu"),
    ("Well begun is half done.", "Aristotle"),
    ("It does not matter how slowly you go, as long as you do not stop.", "Confucius"),
    ("The man who moves a mountain begins by carrying away small stones.", "Confucius"),
    ("While we are postponing, life speeds by.", "Seneca"),
    ("Difficulties strengthen the mind, as labour does the body.", "Seneca"),
    ("No man is free who is not master of himself.", "Epictetus"),
    ("First say to yourself what you would be; and then do what you have to do.", "Epictetus"),
    ("Waste no more time arguing what a good man should be. Be one.", "Marcus Aurelius"),
    ("What stands in the way becomes the way.", "Marcus Aurelius"),
    ("Very little is needed to make a happy life.", "Marcus Aurelius"),
    ("Rule your mind, or it will rule you.", "Horace"),
    ("Energy and persistence conquer all things.", "Benjamin Franklin"),
    ("Lost time is never found again.", "Benjamin Franklin"),
    ("Well done is better than well said.", "Benjamin Franklin"),
    ("Knowing is not enough; we must apply. Willing is not enough; we must do.", "Goethe"),
    ("The chains of habit are too weak to be felt until they are too strong to be broken.", "Samuel Johnson"),
    ("Habit is habit, and not to be flung out of the window by any man, but coaxed downstairs a step at a time.", "Mark Twain"),
    ("The secret of getting ahead is getting started.", "Proverb"),
    ("Do what you can, with what you have, where you are.", "Theodore Roosevelt"),
    ("The best way out is always through.", "Robert Frost"),
    ("He who has a why to live can bear almost any how.", "Nietzsche"),
    ("Fall seven times, stand up eight.", "Japanese proverb"),
    ("Be not afraid of going slowly; be afraid only of standing still.", "Chinese proverb"),
    ("The best time to plant a tree was twenty years ago. The second best time is now.", "Proverb"),
    ("Little by little, one travels far.", "Spanish proverb"),
    ("Drop by drop, the pot fills.", "Proverb"),
    ("Arise, awake, and stop not till the goal is reached.", "Swami Vivekananda"),
    ("You have a right to your actions, but never to the fruits of your actions.", "Bhagavad Gita"),
    ("Where there is a will, there is a way.", "Proverb"),
    ("Slow and steady wins the race.", "Aesop"),
    ("Every noble work is at first impossible.", "Thomas Carlyle"),
    ("Our patience will achieve more than our force.", "Edmund Burke"),
    ("He conquers who endures.", "Persius"),
    ("Begin, be bold, and venture to be wise.", "Horace"),
    ("Nothing is particularly hard if you divide it into small jobs.", "Henry Ford"),
    ("Do not wait; the time will never be just right.", "Napoleon Hill"),
    ("Today is the first day of the rest of your life.", "Proverb"),
    ("Discipline is remembering what you want.", "Proverb"),
]


def quote_for(user_id: str, day: date) -> tuple[str, str]:
    """Same quote all day; walks through the whole list before any repeats. The starting
    point differs per user so friends don't all get the same line."""
    offset = int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16)
    return QUOTES[(offset + day.toordinal()) % len(QUOTES)]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
