"""
Did the user really do what they ticked? Strict rules, but a penalty needs PROOF.

Evidence (collected only on sites the user switched on in the Chrome extension):
  video  the played ranges of each video (only while the tab was visible; skipping
         ahead doesn't count; 2x speed still covers the content)
  site   the course page re-read AFTER the tick (Striver / LeetCode / IITM show solved/done)

Per item in a ticked session:
  ok       video >= 80% of the planned part played, or the site shows it done
  part     video 50-80% played
  bad      the video WAS opened in tracked Chrome but < 50% played, or the site was
           re-read after the tick and still shows it not done
  unknown  no evidence either way (phone, TV, no re-sync, offline work) -> self-reported

Session verdict: any bad -> mismatch; else any part -> partial; else any ok -> verified;
else self-reported. "Unknown" is never punished: a wrong accusation is worse than a lie.

Consequences (stricter setting — every number is a constant below):
  mismatch  no credit, +25% of the claimed time owed on that milestone, trust -15, streak reset
  partial   credit only for what was watched, trust -5
  verified  full credit, trust +2 (max 100)
  self      full credit; HALF credit while trust < 40
  trust < 70                -> checkable tasks can only be ticked by the evidence itself
  3 mismatch days in 7 days -> manual ticks on checkable tasks locked for 3 days
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from app.planners.tasks import SessionItem

OK_COVERAGE = 0.80
BAD_COVERAGE = 0.50
TRUST_START = 100
TRUST_MISMATCH = -15
TRUST_PARTIAL = -5
TRUST_VERIFIED = +2
EVIDENCE_ONLY_BELOW = 70
HALF_CREDIT_BELOW = 40
PENALTY_RATIO = 0.25
LOCK_AFTER_MISMATCH_DAYS = 3
LOCK_WINDOW_DAYS = 7
LOCK_DAYS = 3
MAX_EVENTS = 200

Verdict = Literal["verified", "partial", "self", "mismatch"]


class WatchEvidence(BaseModel):
    key: str                                  # "yt:<id>" or "page:<url>#n"
    url: str | None = None
    title: str | None = None
    duration_s: float
    intervals: list[tuple[float, float]] = Field(default_factory=list)   # played media ranges (seconds)
    updated_at: datetime | None = None


def merge_intervals(ranges: list[tuple[float, float]], duration: float | None = None) -> list[tuple[float, float]]:
    cleaned = []
    for a, b in ranges:
        a, b = float(a), float(b)
        if duration:
            a, b = max(0.0, min(a, duration)), max(0.0, min(b, duration))
        if b > a:
            cleaned.append((a, b))
    cleaned.sort()
    out: list[tuple[float, float]] = []
    for a, b in cleaned:
        if out and a <= out[-1][1] + 1.0:      # 1s slack for timer jitter
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def coverage(ev: WatchEvidence, part_from: float = 0.0, part_to: float = 1.0) -> float:
    """Share of the planned part of the video that was actually played (0..1)."""
    lo, hi = part_from * ev.duration_s, part_to * ev.duration_s
    if hi <= lo:
        return 1.0
    seen = sum(max(0.0, min(b, hi) - max(a, lo)) for a, b in ev.intervals)
    return min(1.0, seen / (hi - lo))


class SiteState(BaseModel):
    done: bool
    synced_at: datetime


class ItemCheck(BaseModel):
    key: str
    title: str
    status: Literal["ok", "part", "bad", "unknown"]
    coverage: float | None = None


class SessionCheck(BaseModel):
    verdict: Verdict
    claimed_minutes: int
    credit_minutes: int
    items: list[ItemCheck]


def check_item(it: SessionItem, evidence: dict[str, WatchEvidence], sites: dict[str, SiteState],
               ticked_at: datetime | None) -> ItemCheck:
    if it.verify == "video" and it.video_key:
        ev = evidence.get(it.video_key)
        if ev is None or ev.duration_s <= 0:
            return ItemCheck(key=it.key, title=it.title, status="unknown")
        cov = coverage(ev, it.part_from, it.part_to)
        status = "ok" if cov >= OK_COVERAGE else "part" if cov >= BAD_COVERAGE else "bad"
        return ItemCheck(key=it.key, title=it.title, status=status, coverage=round(cov, 2))
    if it.verify == "site":
        st = sites.get(it.key)
        if st and ticked_at and st.synced_at > ticked_at:
            return ItemCheck(key=it.key, title=it.title, status="ok" if st.done else "bad")
        if st and st.done:
            return ItemCheck(key=it.key, title=it.title, status="ok")
    return ItemCheck(key=it.key, title=it.title, status="unknown")


def check_session(minutes: int, items: list[SessionItem], evidence: dict[str, WatchEvidence],
                  sites: dict[str, SiteState], ticked_at: datetime | None, trust: int) -> SessionCheck:
    """Verdict + credit for ONE ticked session."""
    checks = [check_item(it, evidence, sites, ticked_at) for it in items]
    statuses = {c.status for c in checks}
    verdict: Verdict = ("mismatch" if "bad" in statuses else "partial" if "part" in statuses
                        else "verified" if "ok" in statuses else "self")
    if verdict == "mismatch":
        return SessionCheck(verdict=verdict, claimed_minutes=minutes, credit_minutes=0, items=checks)
    self_factor = 0.5 if trust < HALF_CREDIT_BELOW else 1.0
    covered = sum(it.minutes for it in items)
    credit = max(0, minutes - covered) * self_factor           # session time with no item attached
    for it, c in zip(items, checks):
        if c.status == "ok":
            credit += it.minutes
        elif c.status == "part":
            credit += it.minutes * (c.coverage or 0)
        else:                                                   # unknown = self-reported
            credit += it.minutes * self_factor
    return SessionCheck(verdict=verdict, claimed_minutes=minutes,
                        credit_minutes=min(minutes, round(credit)), items=checks)


AUTO_MIN_SHARE = 0.8   # items must cover most of the session for the evidence to tick it


def auto_done(items: list[SessionItem], evidence: dict[str, WatchEvidence], sites: dict[str, SiteState],
              session_minutes: int | None = None) -> bool:
    """The evidence alone proves the session: its items fill (most of) it, and every one is
    checkable and ok. A 90-min session with one 10-min video isn't proven by that video."""
    if not items:
        return False
    if session_minutes and sum(it.minutes for it in items) < AUTO_MIN_SHARE * session_minutes:
        return False
    return all(it.verify != "none" and check_item(it, evidence, sites, None).status == "ok" for it in items)


def checkable(items: list[SessionItem]) -> bool:
    return any(it.verify != "none" for it in items)


# ---------- the user's standing ----------

class IntegrityEvent(BaseModel):
    day: date
    session: str
    verdict: Verdict
    claimed_minutes: int
    credit_minutes: int
    penalty_minutes: int = 0
    trust_after: int
    detail: str = ""


class Integrity(BaseModel):
    trust: int = TRUST_START
    streak: int = 0
    lock_until: date | None = None
    penalty_minutes: dict[str, int] = Field(default_factory=dict)   # milestone -> extra minutes owed
    items_done: list[str] = Field(default_factory=list)            # course items Planly credited
    items_part: dict[str, float] = Field(default_factory=dict)     # long items credited up to a fraction
    events: list[IntegrityEvent] = Field(default_factory=list)

    def evidence_only(self, today: date) -> bool:
        """Checkable tasks can't be ticked by hand (only the evidence can tick them)."""
        return self.trust < EVIDENCE_ONLY_BELOW or bool(self.lock_until and today <= self.lock_until)

    def why_locked(self, today: date) -> str | None:
        if self.lock_until and today <= self.lock_until:
            return (f"{LOCK_AFTER_MISMATCH_DAYS} mismatches in a week: checkable tasks tick themselves from "
                    f"the evidence until {self.lock_until:%a %d %b}.")
        if self.trust < EVIDENCE_ONLY_BELOW:
            return f"Trust {self.trust} (< {EVIDENCE_ONLY_BELOW}): checkable tasks tick themselves from the evidence."
        return None


def apply_day(integ: Integrity, day: date, results: list[tuple[str, str, SessionCheck, list[SessionItem]]],
              anything_planned: bool) -> Integrity:
    """Update standing after a day is closed. results: (milestone_key, session title, check, items)
    for each TICKED session."""
    integ = integ.model_copy(deep=True)
    had_mismatch = False
    done = set(integ.items_done)
    for ms_key, title, chk, items in results:
        penalty = 0
        if chk.verdict == "mismatch":
            had_mismatch = True
            penalty = round(chk.claimed_minutes * PENALTY_RATIO)
            integ.penalty_minutes[ms_key] = integ.penalty_minutes.get(ms_key, 0) + penalty
            integ.trust += TRUST_MISMATCH
        elif chk.verdict == "partial":
            integ.trust += TRUST_PARTIAL
        elif chk.verdict == "verified":
            integ.trust += TRUST_VERIFIED
        integ.trust = max(0, min(100, integ.trust))
        for it, c in zip(items, chk.items):
            # a disproved session credits nothing, not even its unproven items
            if chk.verdict == "mismatch" or c.status not in ("ok", "unknown"):
                continue
            if it.part_to >= 0.999:
                done.add(it.key)
                integ.items_part.pop(it.key, None)
            else:
                integ.items_part[it.key] = max(integ.items_part.get(it.key, 0.0), it.part_to)
        bad = [c.title for c in chk.items if c.status == "bad"]
        detail = ("not done per the evidence: " + ", ".join(bad[:3])) if bad else ""
        integ.events.append(IntegrityEvent(day=day, session=title, verdict=chk.verdict,
                                           claimed_minutes=chk.claimed_minutes, credit_minutes=chk.credit_minutes,
                                           penalty_minutes=penalty, trust_after=integ.trust, detail=detail))
    integ.items_done = sorted(done)
    if had_mismatch:
        integ.streak = 0
        recent = {e.day for e in integ.events
                  if e.verdict == "mismatch" and day - e.day < timedelta(days=LOCK_WINDOW_DAYS)}
        if len(recent) >= LOCK_AFTER_MISMATCH_DAYS:
            integ.lock_until = day + timedelta(days=LOCK_DAYS)
    elif results:
        integ.streak += 1
    elif anything_planned:
        integ.streak = 0                       # planned work, nothing ticked
    integ.events = integ.events[-MAX_EVENTS:]
    return integ


def now() -> datetime:
    return datetime.now(timezone.utc)
