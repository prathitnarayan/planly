"""
Planly learns how YOU work, from what actually happened. Pure code, no AI.

Every closed day leaves one Outcome per planned session (planned vs credited minutes,
weekday, and for videos: length, share watched, and real time spent on the page).
From those, two things are learned (recent weeks count more; half-life 14 days):

  weekday ratio  how much of the planned work you really do on each weekday.
                 Tuesdays at 40% -> the plan puts less on Tuesdays.
  video pace     real minutes per minute of video (pauses, notes, rewinds included),
                 measured by the extension. Replaces the 1.5x guess once there's data.

Few samples are pulled towards the default (a "prior"), so one bad Tuesday doesn't
rewrite your week. Nothing is used until there are enough samples (constants below).

Stage 2 (crowd averages) and stage 3 (a real model) need these same Outcome rows,
which is why every closed day stores them.
"""

from __future__ import annotations

import math
from datetime import date

from pydantic import BaseModel, Field

HALF_LIFE_DAYS = 14
WINDOW_DAYS = 90
MIN_WEEKDAY_SESSIONS = 3
MIN_VIDEO_SAMPLES = 3
WEEKDAY_PRIOR = 1.0          # assume you do what's planned until shown otherwise
WEEKDAY_PRIOR_WEIGHT = 1     # ...worth 1 session of evidence
VIDEO_PRIOR = 1.5            # load.VIDEO_FACTOR
VIDEO_PRIOR_WEIGHT = 3
RATIO_RANGE = (0.3, 1.0)
VIDEO_RANGE = (1.0, 3.0)
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class OutcomeItem(BaseModel):
    kind: str
    length_min: float | None = None     # video length
    watched: float | None = None        # share of the video played (0..1)
    active_min: float | None = None     # real time spent on the lecture page


class Outcome(BaseModel):
    day: date
    goal_id: str | None = None
    weekday: int
    start: str | None = None
    kind: str
    planned_min: int
    credited_min: int
    ticked: bool
    verdict: str | None = None
    items: list[OutcomeItem] = Field(default_factory=list)


class WeekdayStat(BaseModel):
    ratio: float
    sessions: int


class Learned(BaseModel):
    weekday: dict[int, WeekdayStat] = Field(default_factory=dict)
    video_factor: float | None = None
    video_samples: int = 0
    outcomes: int = 0
    updated: date | None = None

    def weekday_ratio(self, sustainable_ratio: float) -> dict[int, float]:
        """Capacity ratio per weekday, only where there's enough evidence."""
        return {wd: round(sustainable_ratio * st.ratio, 3)
                for wd, st in self.weekday.items() if st.sessions >= MIN_WEEKDAY_SESSIONS}

    def pace(self) -> float | None:
        return self.video_factor if self.video_samples >= MIN_VIDEO_SAMPLES else None

    def notes(self) -> list[str]:
        out = []
        if self.pace():
            out.append(f"Videos take you about {self.video_factor:.1f}x their length "
                       f"(from {self.video_samples} lectures). Tasks are sized with that.")
        strong = {wd: st for wd, st in self.weekday.items() if st.sessions >= MIN_WEEKDAY_SESSIONS}
        if strong:
            low = min(strong, key=lambda w: strong[w].ratio)
            high = max(strong, key=lambda w: strong[w].ratio)
            if strong[low].ratio < 0.75:
                out.append(f"{WEEKDAYS[low]}s you finish about {strong[low].ratio:.0%} of what's planned, "
                           f"so less goes there.")
            if strong[high].ratio >= 0.9 and high != low:
                out.append(f"{WEEKDAYS[high]}s are your most reliable day ({strong[high].ratio:.0%}).")
        return out


def _weight(age_days: int) -> float:
    return 0.5 ** (max(0, age_days) / HALF_LIFE_DAYS)


def learn(outcomes: list[Outcome], today: date) -> Learned:
    recent = [o for o in outcomes if 0 <= (today - o.day).days <= WINDOW_DAYS]
    per_wd: dict[int, list[Outcome]] = {}
    for o in recent:
        if o.planned_min > 0:
            per_wd.setdefault(o.weekday, []).append(o)

    weekday = {}
    for wd, rows in per_wd.items():
        newest = max(o.day for o in rows)       # recency is relative: your latest week counts fully
        w = [_weight((newest - o.day).days) for o in rows]
        planned = sum(wi * o.planned_min for wi, o in zip(w, rows))
        done = sum(wi * min(o.credited_min, o.planned_min) for wi, o in zip(w, rows))
        avg = planned / max(sum(w), 1e-9)
        k = WEEKDAY_PRIOR_WEIGHT * avg
        ratio = (done + k * WEEKDAY_PRIOR) / (planned + k)
        lo, hi = RATIO_RANGE
        weekday[wd] = WeekdayStat(ratio=round(min(hi, max(lo, ratio)), 3), sessions=len(rows))

    # video pace: real page time / video content actually watched (log-average, shrunk to the prior)
    logs, weights = [], []
    newest = max((o.day for o in recent), default=today)
    for o in recent:
        for it in o.items:
            if (it.kind == "video" and it.length_min and it.active_min and it.watched
                    and it.watched >= 0.8 and it.active_min >= 0.5 * it.length_min * it.watched):
                pace = min(4.0, max(0.8, it.active_min / (it.length_min * it.watched)))
                logs.append(math.log(pace))
                weights.append(_weight((newest - o.day).days))
    video = None
    if logs:
        num = sum(w * l for w, l in zip(weights, logs)) + VIDEO_PRIOR_WEIGHT * math.log(VIDEO_PRIOR)
        video = math.exp(num / (sum(weights) + VIDEO_PRIOR_WEIGHT))
        lo, hi = VIDEO_RANGE
        video = round(min(hi, max(lo, video)), 2)
    return Learned(weekday=weekday, video_factor=video, video_samples=len(logs),
                   outcomes=len(recent), updated=today)
