"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Learned } from "@/components/goal/Learned";
import { Today } from "@/components/goal/Today";
import { ErrorNote, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { day, hours, isoToday, minutesToHours, parseDay, pretty, shortName, time } from "@/lib/format";
import type { Feasibility, Replan, Sprint, TodayView } from "@/lib/types";

const DAY_MS = 86_400_000;
const daysBetween = (a: string, b: string) => Math.round((parseDay(b).getTime() - parseDay(a).getTime()) / DAY_MS);
const WD1 = ["S", "M", "T", "W", "T", "F", "S"];

function SectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-4">
      <h2 className="text-xs font-medium uppercase tracking-widest text-muted">{children}</h2>
      {right && <span className="num text-xs text-muted">{right}</span>}
    </div>
  );
}

// ---------- verdict: does it fit? ----------

function Verdict({ f, r, onEditTime, onEditHours }: { f: Feasibility; r: Replan; onEditTime: () => void; onEditHours: () => void }) {
  const load = f.available_hours ? f.required_hours / f.available_hours : 1;
  const o = f.options;
  return (
    <section className={`rise mb-10 rounded-2xl p-5 ${f.feasible ? "border border-line" : "border-2 border-ink"}`}>
      <p className="text-xl font-semibold tracking-tight">
        {f.feasible ? "✓ It fits." : "! It doesn't fit as it is."}
      </p>
      <div className="mt-4 grid grid-cols-3 gap-3">
        {[
          ["Need", hours(f.required_hours)],
          ["Have", hours(f.available_hours)],
          [f.feasible ? "Done by" : "Last due", day(f.feasible ? f.projected_finish : f.last_due)],
        ].map(([k, v]) => (
          <div key={k}>
            <p className="text-xs uppercase tracking-wider text-muted">{k}</p>
            <p className="num text-lg font-semibold sm:text-2xl">{v}</p>
          </div>
        ))}
      </div>
      {/* how full your free time is: ink = planned work; hatched = overflow */}
      <div className="mt-4">
        <div className="relative h-2 overflow-hidden rounded-full bg-line">
          <div className="grow h-2 rounded-full bg-ink" style={{ width: `${Math.min(100, load * 100)}%` }} />
        </div>
        <p className="num mt-1.5 text-xs text-muted">
          Uses {Math.round(load * 100)}% of your planned free time until {day(f.last_due)}
          {load > 0.95 && load <= 1 && " — tight: one missed day will show up as late"}
          {!f.feasible && load <= 1 && " — enough time in total, but not before the earlier deadlines"}
          {r.multiplier && ` · your pace ×${r.multiplier}`}
        </p>
      </div>
      {r.alerts.length > 0 && <ul className="mt-4 space-y-1 border-t border-line pt-3 text-sm">{r.alerts.map((a) => <li key={a}>! {a}</li>)}</ul>}
      {o && (
        <ol className="mt-4 space-y-1.5 border-t border-line pt-3 text-sm">
          <li><strong>1.</strong>{" "}
            {o.extra_hours_per_week === null ? "More time won't fix it on its own." : <>Add <strong className="num">{hours(o.extra_hours_per_week)}</strong> a week
              (<span className="num">~{Math.round((o.extra_hours_per_week * 60) / 7)} min a day</span>){!o.extra_hours_realistic && <span className="text-muted"> — that&apos;s a lot</span>}
              {" · "}<button className="cursor-pointer underline underline-offset-4" onClick={onEditTime}>change free time</button></>}
          </li>
          <li><strong>2.</strong> {o.later_finish_ok ? `Accept a later finish: ${day(o.earliest_finish)}` : "A later finish isn't an option — those are hard deadlines."}</li>
          <li><strong>3.</strong>{" "}
            {o.optional_cuts.length ? `Drop optional parts — then it ${o.feasible_after_cuts ? "fits" : "still doesn't fit"}.`
              : <>Nothing optional to drop. Or <button className="cursor-pointer underline underline-offset-4" onClick={onEditHours}>re-check the hours</button>.</>}
          </li>
        </ol>
      )}
    </section>
  );
}

// ---------- milestones as a timeline ----------

function Timeline({ r, deliverableOf }: { r: Replan; deliverableOf: Record<string, string | null> }) {
  const f = r.feasibility;
  const firstDay: Record<string, string> = {};
  for (const sp of r.schedule.sprints) for (const d of sp.days) for (const s of d.sessions)
    if (!firstDay[s.milestone_key]) firstDay[s.milestone_key] = d.day;
  const start = r.today;
  const ends = [f.last_due, ...f.milestones.map((m) => m.projected_finish ?? m.due ?? f.last_due)].filter(Boolean) as string[];
  const end = ends.reduce((a, b) => (b > a ? b : a), start);
  const span = Math.max(1, daysBetween(start, end) + 1);
  const pos = (iso: string) => `${Math.max(0, Math.min(100, (daysBetween(start, iso) / span) * 100))}%`;

  return (
    <section className="mb-10">
      <SectionTitle right={`${day(start)} → ${day(end)}`}>Milestones</SectionTitle>
      <ul className="space-y-4">
        {f.milestones.map((m, i) => {
          const p = r.progress.find((x) => x.key === m.key);
          const from = firstDay[m.key] ?? start;
          const to = m.projected_finish ?? end;
          const width = `${Math.max(1.5, ((daysBetween(from, to) + 1) / span) * 100)}%`;
          const doneRatio = p && p.estimate_minutes ? Math.min(1, p.spent_minutes / p.estimate_minutes) : 0;
          return (
            <li key={m.key} className="rise" style={{ animationDelay: `${i * 50}ms` }}>
              <div className="mb-1.5 flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0 truncate">
                  <span className="mr-2 text-xs text-muted">{pretty(deliverableOf[m.key] ?? "")}</span>
                  <span className={m.late ? "font-semibold" : ""}>{shortName(m.name, deliverableOf[m.key] ?? null)}</span>
                </span>
                <span className="num shrink-0 text-xs text-muted">
                  {hours(m.hours)} left · {m.late ? <strong className="text-ink">! {m.days_late ? `${m.days_late}d late` : "won't finish"}</strong> : <>done {day(m.projected_finish)}</>}
                </span>
              </div>
              <div className="relative h-3 rounded-full bg-soft">
                <div className={`grow absolute top-0 h-3 overflow-hidden rounded-full ${m.late ? "hatch border border-ink" : "bg-ink/85"}`}
                     style={{ left: pos(from), width }}>
                  {doneRatio > 0 && <div className="h-full bg-ink" style={{ width: `${doneRatio * 100}%` }} />}
                </div>
                {m.due && (
                  <span className="absolute -top-1 h-5 w-0.5 bg-ink" style={{ left: pos(m.due) }} title={`due ${day(m.due)}`} />
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs text-muted">Bar = when it&apos;s worked on · tall line = due date · hatched = late</p>
      {r.progress.some((p) => p.complete) && (
        <p className="mt-2 text-sm">✓ Finished: {r.progress.filter((p) => p.complete).map((p) => p.name).join(", ")}</p>
      )}
    </section>
  );
}

// ---------- this week as a strip of days ----------

function WeekStrip({ sp, todayIso }: { sp: Sprint; todayIso: string }) {
  const firstWithWork = sp.days.find((d) => d.sessions.length)?.day ?? sp.days[0]?.day;
  const [sel, setSel] = useState(sp.days.some((d) => d.day === todayIso) ? todayIso : firstWithWork);
  const max = Math.max(1, ...sp.days.map((d) => Math.max(d.capacity_minutes, d.sessions.reduce((a, s) => a + s.minutes, 0))));
  const selected = sp.days.find((d) => d.day === sel);

  return (
    <section className="mb-10">
      <SectionTitle right={minutesToHours(sp.planned_minutes)}>This week · {day(sp.start)} – {day(sp.end)}</SectionTitle>
      <div className="grid grid-cols-7 gap-1.5">
        {/* keep each day under its weekday: empty cells before the first planned day */}
        {Array.from({ length: (parseDay(sp.days[0]?.day ?? sp.start).getDay() + 6) % 7 }, (_, i) => (
          <span key={`pad${i}`} className="hatch rounded-xl border border-dashed border-line" aria-hidden />
        ))}
        {sp.days.map((d) => {
          const mins = d.sessions.reduce((a, s) => a + s.minutes, 0);
          const isSel = d.day === sel;
          const isToday = d.day === todayIso;
          return (
            <button key={d.day} onClick={() => setSel(d.day)} aria-pressed={isSel}
                    className={`flex cursor-pointer flex-col items-center rounded-xl border px-1 py-2 transition-colors ${
                      isSel ? "border-ink bg-soft" : "border-line hover:border-muted"}`}>
              <span className="text-[11px] text-muted">{WD1[parseDay(d.day).getDay()]}</span>
              <span className={`num flex h-7 w-7 items-center justify-center rounded-full text-sm font-semibold ${isToday ? "bg-ink text-paper" : ""}`}>
                {parseDay(d.day).getDate()}
              </span>
              <span className="mt-1.5 flex h-16 w-3 items-end overflow-hidden rounded-full bg-line">
                <span className="grow w-3 rounded-full bg-ink" style={{ height: `${(mins / max) * 100}%` }} />
              </span>
              <span className="num mt-1.5 text-[11px] text-muted">{mins ? minutesToHours(mins) : "—"}</span>
            </button>
          );
        })}
      </div>
      {selected && (
        <div className="mt-3 rounded-xl border border-line">
          <p className="border-b border-line px-4 py-2 text-xs font-medium text-muted">{day(selected.day)}{selected.day === todayIso && " · tick these in Today above"}</p>
          {selected.sessions.length === 0 ? (
            <p className="px-4 py-3 text-sm text-muted">Free day.</p>
          ) : (
            <ul className="divide-y divide-line">
              {selected.sessions.map((s, i) => (
                <li key={i} className="flex items-center gap-4 px-4 py-2.5 text-sm">
                  <span className="num w-24 shrink-0 text-muted">{s.start ? `${time(s.start)}–${time(s.end)}` : `${s.minutes} min`}</span>
                  <span className="min-w-0 flex-1">
                    {shortName(s.milestone_name, s.deliverable)}
                    <span className="block text-xs text-muted">{pretty(s.deliverable)} · {s.kind}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {sp.definition_of_done.length > 0 && (
        <div className="mt-4 text-sm">
          <p className="mb-1 text-xs font-medium uppercase tracking-wider text-muted">Done this week when</p>
          {sp.definition_of_done.map((c) => <p key={c}>○ {c}</p>)}
        </div>
      )}
    </section>
  );
}

// ---------- later weeks ----------

function weekLine(sp: Sprint) {
  const names: string[] = [];
  for (const d of sp.days) for (const s of d.sessions) {
    const n = shortName(s.milestone_name, s.deliverable) + (sp.finishing.includes(s.milestone_key) ? " ✓" : "");
    if (!names.includes(n)) names.push(n);
  }
  return names.join(" · ");
}

function Later({ sprints }: { sprints: Sprint[] }) {
  const max = Math.max(1, ...sprints.map((s) => s.planned_minutes));
  return (
    <section className="mb-10">
      <SectionTitle>After that</SectionTitle>
      <ul className="divide-y divide-line border-y border-line">
        {sprints.map((sp) => (
          <li key={sp.number} className="grid grid-cols-[4.5rem_1fr] gap-x-4 py-3 text-sm sm:grid-cols-[6.5rem_1fr]">
            <span className="num font-medium">{day(sp.start)}</span>
            <span className="min-w-0">
              <span className="flex items-center gap-2">
                <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-line">
                  <span className="grow block h-1.5 rounded-full bg-ink" style={{ width: `${(sp.planned_minutes / max) * 100}%` }} />
                </span>
                <span className="num w-12 shrink-0 text-right text-xs text-muted">{minutesToHours(sp.planned_minutes)}</span>
              </span>
              <span className="mt-1 block truncate text-muted">{weekLine(sp) || "free week"}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ---------- page ----------

export function Plan({ goalId, hasCheckins, onEditHours, onEditTime }: {
  goalId: string; hasCheckins: boolean; onEditHours: () => void; onEditTime: () => void;
}) {
  const [r, setR] = useState<Replan | null>(null);
  const [today, setToday] = useState<TodayView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const todayIso = isoToday();

  // /today first: it closes finished days (unticked -> missed), then the plan reflects that.
  const load = useCallback(() => {
    const query = { today: isoToday() };
    return api<TodayView>(`/goals/${goalId}/today`, { query })
      .then((t) => { setToday(t); return api<Replan>(`/goals/${goalId}/replan`, { query }); })
      .then(setR)
      .catch((e) => setError(e.message));
  }, [goalId]);
  useEffect(() => { load(); }, [load]);

  if (!r) return error ? <ErrorNote error={error} /> : <Loading label="Planning" />;

  const deliverableOf: Record<string, string | null> = {};
  for (const sp of r.schedule.sprints) for (const d of sp.days) for (const s of d.sessions) deliverableOf[s.milestone_key] = s.deliverable;
  const [thisWeek, ...later] = r.schedule.sprints;

  return (
    <div>
      {today && <Today goalId={goalId} view={today} onChange={setToday} onStarted={load} />}
      <Verdict f={r.feasibility} r={r} onEditTime={onEditTime} onEditHours={onEditHours} />
      <Learned />

      <div className="mb-10 flex flex-wrap gap-2">
        <button onClick={onEditHours} className="h-9 cursor-pointer rounded-full border border-line px-4 text-sm hover:border-ink">Adjust hours</button>
        <button onClick={onEditTime} className="h-9 cursor-pointer rounded-full border border-line px-4 text-sm hover:border-ink">Change free time</button>
        <Link href={`/goals/${goalId}/checkin`} className="inline-flex h-9 items-center rounded-full border border-line px-4 text-sm hover:border-ink">
          Log exact minutes
        </Link>
      </div>

      <Timeline r={r} deliverableOf={deliverableOf} />
      {thisWeek && <WeekStrip key={thisWeek.start} sp={thisWeek} todayIso={todayIso} />}
      {later.length > 0 && <Later sprints={later} />}
      {!hasCheckins && <p className="text-sm text-muted">Tip: tick sessions as you finish them. Unticked work moves to the next days on its own, and the plan tells you early if a deadline slips.</p>}
      <ErrorNote error={error} />
    </div>
  );
}
