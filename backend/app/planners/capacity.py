"""
Capacity: how many minutes the user can realistically work on each day.

Pure functions only — no DB, no LLM. Easy to test, easy to trust.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from pydantic import BaseModel, Field, model_validator


class Slot(BaseModel):
    """A recurring weekly free slot. weekday: 0 = Monday ... 6 = Sunday."""

    weekday: int = Field(ge=0, le=6)
    start: time
    end: time

    @model_validator(mode="after")
    def end_after_start(self) -> "Slot":
        if self.end <= self.start:
            raise ValueError(f"slot end {self.end} must be after start {self.start}")
        return self

    @property
    def minutes(self) -> int:
        start = datetime.combine(date.min, self.start)
        end = datetime.combine(date.min, self.end)
        return int((end - start).total_seconds() // 60)


class CapacityProfile(BaseModel):
    slots: list[Slot]
    # Plan at "sustainable", not "maximum". Nobody uses 100% of free time for months.
    sustainable_ratio: float = Field(default=0.8, gt=0, le=1)
    fallback_ratio: float = Field(default=0.5, gt=0, le=1)
    # One-off exceptions: {date: minutes available that day}
    overrides: dict[date, int] = Field(default_factory=dict)

    def max_minutes_on(self, day: date) -> int:
        if day in self.overrides:
            return self.overrides[day]
        return sum(s.minutes for s in self.slots if s.weekday == day.weekday())

    def sustainable_minutes_on(self, day: date, scale: float = 1.0) -> int:
        """scale > 1 simulates 'what if I had more free time' (used by the options search)."""
        # Overrides are what the user said they have — don't discount them again.
        if day in self.overrides:
            return int(self.overrides[day] * scale)
        return int(self.max_minutes_on(day) * self.sustainable_ratio * scale)

    def weekly_summary(self) -> dict[str, float]:
        """Hours per week at each level, ignoring one-off overrides."""
        max_min = sum(s.minutes for s in self.slots)
        return {
            "max_hours": round(max_min / 60, 2),
            "sustainable_hours": round(max_min * self.sustainable_ratio / 60, 2),
            "fallback_hours": round(max_min * self.fallback_ratio / 60, 2),
        }


def days_between(start: date, end_inclusive: date):
    day = start
    while day <= end_inclusive:
        yield day
        day += timedelta(days=1)


def available_minutes(profile: CapacityProfile, start: date, end_inclusive: date) -> int:
    return sum(profile.sustainable_minutes_on(d) for d in days_between(start, end_inclusive))
