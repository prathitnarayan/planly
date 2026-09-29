"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorNote, Loading, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { Today } from "@/components/goal/Today";
import { day, hours, isoToday, minutesToHours, pretty, shortName, time } from "@/lib/format";
import type { Replan, Sprint, TodayView } from "@/lib/types";

function weekLine(sp: Sprint, deliverableOf: Record<string, string | null>) {
  const groups = new Map<string, string[]>();
  const seen = new Set<string>();
  for (const d of sp.days)
    for (const s of d.sessions) {
      if (seen.has(s.milestone_key)) continue;
      seen.add(s.milestone_key);
      const g = pretty(deliverableOf[s.milestone_key] ?? "") || "Other";
      const tick = sp.finishing.includes(s.milestone_key) ? " ✓" : "";
      groups.set(g, [...(groups.get(g) ?? []), shortName(s.milestone_name, s.deliverable) + tick]);
    }
  return [...groups.entries()].map(([g, ms]) => `${g}: ${ms.join(", ")}`).join(" · ");
}

export function Plan({ goalId, hasCheckins, onEditHours, onEditTime }: {
  goalId: string;
  hasCheckins: boolean;
  onEditHours: () => void;
  onEditTime: () => void;
}) {
  const [r, setR] = useState<Replan | null>(null);
  const [today, setToday] = useState<TodayView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // /today first: it closes finished days (unticked -> missed), then the plan reflects that.
    const query = { today: isoToday() };
    api<TodayView>(`/goals/${goalId}/today`, { query })
      .then((t) => { setToday(t); return api<Replan>(`/goals/${goalId}/replan`, { query }); })
      .then(setR)
      .catch((e) => setError(e.message));
  }, [goalId]);

  if (!r) return error ? <ErrorNote error={error} /> : <Loading label="Planning" />;

  const f = r.feasibility;
  const deliverableOf: Record<string, string | null> = {};
  for (const sp of r.schedule.sprints) for (const d of sp.days) for (const s of d.sessions) deliverableOf[s.milestone_key] = s.deliverable;
  const [thisWeek, ...later] = r.schedule.sprints;
  const o = f.options;

  return (
    <div>
      {today && <Today goalId={goalId} view={today} onChange={setToday} />}

      {/* verdict */}
      <div className={`mb-8 rounded-lg p-5 ${f.feasible ? "border border-line" : "border-2 border-ink"}`}>
        <p className="text-xl font-semibold">
          {f.feasible ? `✓ It fits. Everything done by ${day(f.projected_finish)}.` : "! It doesn't fit as it is."}
        </p>
        <p className="num mt-1 text-sm text-muted">
          Need {hours(f.required_hours)} · have {hours(f.available_hours)} until {day(f.last_due)}
          {r.multiplier && ` · your pace ×${r.multiplier}`}
        </p>
        {r.alerts.length > 0 && (
          <ul className="mt-3 space-y-1 text-sm">{r.alerts.map((a) => <li key={a}>— {a}</li>)}</ul>
        )}
        {o && (
          <ol className="mt-4 space-y-1.5 text-sm">
            <li>
              <strong>1.</strong>{" "}
              {o.extra_hours_per_week === null
                ? "More time won't fix it on its own."
                : <>Add <strong className="num">{hours(o.extra_hours_per_week)}</strong> free time a week
                    (<span className="num">~{Math.round((o.extra_hours_per_week * 60) / 7)} min a day</span>)
                    {!o.extra_hours_realistic && <span className="text-muted"> — that&apos;s a lot</span>}
                    {" · "}<button className="cursor-pointer underline underline-offset-4" onClick={onEditTime}>change free time</button></>}
            </li>
            <li>
              <strong>2.</strong>{" "}
              {o.later_finish_ok ? `Accept a later finish: ${day(o.earliest_finish)}` : "A later finish isn't an option — those are hard deadlines."}
            </li>
            <li>
              <strong>3.</strong>{" "}
              {o.optional_cuts.length
                ? `Drop optional parts — then it ${o.feasible_after_cuts ? "fits" : "still doesn't fit"}.`
                : <>Nothing optional to drop. Or <button className="cursor-pointer underline underline-offset-4" onClick={onEditHours}>re-check the hours</button>.</>}
            </li>
          </ol>
        )}
      </div>

      <div className="mb-10 flex flex-wrap gap-3">
        <Link href={`/goals/${goalId}/checkin`} className="inline-flex h-10 items-center rounded-md bg-ink px-4 text-sm font-medium text-paper hover:opacity-85">
          Check in
        </Link>
        <button onClick={onEditHours} className="h-10 cursor-pointer rounded-md border border-ink px-4 text-sm font-medium hover:bg-soft">Adjust hours</button>
        <button onClick={onEditTime} className="h-10 cursor-pointer rounded-md border border-ink px-4 text-sm font-medium hover:bg-soft">Change free time</button>
      </div>

      {/* milestones vs due dates */}
      <h2 className="mb-3 text-xs font-medium uppercase tracking-widest text-muted">Milestones</h2>
      <div className="mb-10 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink text-left text-xs uppercase tracking-wider text-muted">
              <th className="py-2 pr-3 font-medium">Milestone</th>
              <th className="py-2 pr-3 text-right font-medium">Left</th>
              <th className="py-2 pr-3 font-medium">Due</th>
              <th className="py-2 font-medium">Done by</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {f.milestones.map((m) => {
              const p = r.progress.find((x) => x.key === m.key);
              return (
                <tr key={m.key}>
                  <td className="py-2.5 pr-3">
                    <span className="text-xs text-muted">{pretty(deliverableOf[m.key] ?? "")}</span>
                    <span className="block">{shortName(m.name, deliverableOf[m.key] ?? null)}</span>
                  </td>
                  <td className="num py-2.5 pr-3 text-right">{hours(m.hours)}
                    {p && p.spent_minutes > 0 && <span className="block text-xs text-muted">{minutesToHours(p.spent_minutes)} done</span>}
                  </td>
                  <td className="num py-2.5 pr-3 whitespace-nowrap">{day(m.due)}</td>
                  <td className="py-2.5 whitespace-nowrap">
                    <Status state={m.late ? "late" : "ok"}>
                      <span className="num">{day(m.projected_finish)}</span>
                      {m.late && <span>· {m.days_late ? `${m.days_late}d late` : "won't finish"}</span>}
                    </Status>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {r.progress.filter((p) => p.complete).length > 0 && (
          <p className="mt-3 text-sm text-muted">
            Finished: {r.progress.filter((p) => p.complete).map((p) => p.name).join(", ")}
          </p>
        )}
      </div>

      {/* this week */}
      {thisWeek && (
        <>
          <h2 className="mb-3 text-xs font-medium uppercase tracking-widest text-muted">
            This week · {day(thisWeek.start)} – {day(thisWeek.end)} · <span className="num">{minutesToHours(thisWeek.planned_minutes)}</span>
          </h2>
          <ul className="mb-4 divide-y divide-line border-y border-line">
            {thisWeek.days.map((d) => (
              <li key={d.day} className="grid grid-cols-1 gap-1 py-3 sm:grid-cols-[6.5rem_1fr] sm:gap-3">
                <span className="num text-sm font-medium">{day(d.day)}</span>
                <span className="space-y-2 sm:space-y-1">
                  {d.sessions.length === 0 && <span className="text-sm text-muted">free</span>}
                  {d.sessions.map((s, i) => (
                    <span key={i} className="flex flex-col text-sm sm:flex-row sm:gap-3">
                      <span className="num shrink-0 text-muted sm:w-24">{s.start ? `${time(s.start)}–${time(s.end)}` : `${s.minutes} min`}</span>
                      <span>
                        {shortName(s.milestone_name, s.deliverable)}
                        <span className="ml-2 text-xs text-muted">{pretty(s.deliverable)} · {s.kind}</span>
                      </span>
                    </span>
                  ))}
                </span>
              </li>
            ))}
          </ul>
          {thisWeek.definition_of_done.length > 0 && (
            <div className="mb-10 text-sm">
              <p className="mb-1 font-medium">Done this week when</p>
              {thisWeek.definition_of_done.map((c) => <p key={c}>✓ {c}</p>)}
            </div>
          )}
        </>
      )}

      {later.length > 0 && (
        <>
          <h2 className="mb-3 text-xs font-medium uppercase tracking-widest text-muted">After that</h2>
          <ul className="divide-y divide-line border-y border-line">
            {later.map((sp) => (
              <li key={sp.number} className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 py-3 text-sm sm:grid-cols-[7rem_3.5rem_1fr]">
                <span className="num font-medium sm:font-normal">{day(sp.start)}</span>
                <span className="num text-right text-muted">{minutesToHours(sp.planned_minutes)}</span>
                <span className="col-span-2 sm:col-span-1">{weekLine(sp, deliverableOf) || <span className="text-muted">free week</span>}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {!hasCheckins && <p className="mt-8 text-sm text-muted">Tip: tick sessions as you finish them. Unticked work moves to the next days on its own, and the plan tells you early if a deadline slips.</p>}
    </div>
  );
}
