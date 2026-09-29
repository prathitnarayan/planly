"""
Reality check: is the runway anywhere near what this kind of goal usually takes?

The AI only supplies the commonly quoted range for well-known goals (UPSC, CAT, a
marathon...). This code compares it with the user's deadline and free time:

  ok           the runway is at least the usual minimum
  tight        below the usual minimum (possible for some, risky for most)
  unrealistic  under half the usual minimum, in months or in hours

It warns, it never blocks: the user decides. Suggestions point at a smaller goal that
fits, or at a deadline that does.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.interview import RealityCheck

DAYS_PER_MONTH = 30.44
UNREALISTIC_BELOW = 0.5


class RealityVerdict(BaseModel):
    level: Literal["unknown", "ok", "tight", "unrealistic"]
    headline: str | None = None
    lines: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    months_left: float | None = None
    hours_available: float | None = None
    needed_hours_per_week: float | None = None
    share_of_minimum: float | None = None      # runway / usual minimum (the smaller of months and hours)
    earliest_realistic: date | None = None


def _n(x: float) -> str:
    return f"{x:,.0f}"


def assess(rc: RealityCheck | None, today: date, deadline: date | None,
           weekly_hours: float | None) -> RealityVerdict:
    if not rc or not rc.known:
        return RealityVerdict(level="unknown")
    h_lo, h_hi = rc.typical_hours_low, rc.typical_hours_high
    m_lo, m_hi = rc.typical_months_low, rc.typical_months_high
    headline = (f"{rc.name or 'This goal'} usually takes {_n(m_lo)}–{_n(m_hi)} months "
                f"and about {_n(h_lo)}–{_n(h_hi)} hours of focused study.")
    note = f"{rc.basis or 'Commonly quoted range'}. A rough typical figure (AI estimate); it varies a lot by person."
    if rc.adjusted_for:
        note += f" Adjusted for: {rc.adjusted_for}."
    earliest = today + timedelta(days=round(m_lo * DAYS_PER_MONTH))

    if deadline is None:
        return RealityVerdict(level="ok", headline=headline, lines=[note], earliest_realistic=earliest)

    days = max(0, (deadline - today).days)
    months = days / DAYS_PER_MONTH
    weeks = max(days / 7, 1 / 7)
    needed = h_lo / weeks
    shares = [months / m_lo]
    available = None
    if weekly_hours:
        available = weekly_hours * weeks
        shares.append(available / h_lo)
    share = min(shares)
    level = "ok" if share >= 1 else "unrealistic" if share < UNREALISTIC_BELOW else "tight"

    runway = f"{days} days" if days < 60 else f"{months:.1f} months"
    lines = [f"Your deadline is {runway} away — about {min(share, 9.99):.0%} of the usual minimum."
             if level != "ok" else f"Your deadline is {runway} away: in line with the usual range."]
    if level != "ok":
        per_day = needed / 7
        daily = "more hours than a day has" if per_day > 16 else f"~{per_day:.1f} h every day"
        lines.append(f"Even the low end would need ~{_n(needed)} h a week ({daily})"
                     + (f"; your free time gives ~{_n(weekly_hours)} h a week." if weekly_hours else "."))
    lines.append(note)

    suggestions = []
    if level != "ok":
        suggestions.append(f"Aim for a later attempt: {earliest:%b %Y} or after would match the usual minimum.")
        suggestions += [f"Or narrow this goal: {s}" for s in rc.scope_suggestions]
    return RealityVerdict(level=level, headline=headline, lines=lines, suggestions=suggestions,
                          months_left=round(months, 2), hours_available=round(available, 1) if available else None,
                          needed_hours_per_week=round(needed, 1), share_of_minimum=round(share, 3),
                          earliest_realistic=earliest)
