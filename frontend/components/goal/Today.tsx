"use client";

import { useState } from "react";
import { ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { day, pretty, shortName } from "@/lib/format";
import type { TodaySession, TodayView } from "@/lib/types";

/** Today's sessions as checkboxes. Struck through ONLY while ticked. Unticked work is
 *  logged as missed when the day ends, and spread over the next days (not piled on one). */
export function Today({ goalId, view, onChange }: { goalId: string; view: TodayView; onChange: (v: TodayView) => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function toggle(s: TodaySession) {
    if (busy) return;
    setError(null);
    setBusy(s.id);
    const optimistic = {
      ...view,
      sessions: view.sessions.map((x) => (x.id === s.id ? { ...x, done: !s.done } : x)),
      done_minutes: view.done_minutes + (s.done ? -s.minutes : s.minutes),
    };
    onChange(optimistic);
    try {
      onChange(await api<TodayView>(`/goals/${goalId}/ticks`, {
        method: "PUT", body: { day: view.day, session_id: s.id, done: !s.done },
      }));
    } catch (e) {
      onChange(view);                       // put it back the way it was
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const pct = view.planned_minutes ? Math.round((view.done_minutes / view.planned_minutes) * 100) : 0;

  return (
    <section className="mb-10" aria-labelledby="today-h">
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 id="today-h" className="text-xs font-medium uppercase tracking-widest text-muted">Today · {day(view.day)}</h2>
        {view.planned_minutes > 0 && (
          <span className="num text-sm text-muted">{view.done_minutes} / {view.planned_minutes} min</span>
        )}
      </div>

      {view.moved.length > 0 && (
        <div className="mb-3 rounded-md border border-line px-3 py-2 text-sm">
          <p className="font-medium">Carried over</p>
          {view.moved.map((m) => <p key={m} className="text-muted">{m}</p>)}
        </div>
      )}

      {view.sessions.length === 0 ? (
        <p className="text-sm text-muted">{view.message}</p>
      ) : (
        <>
          <div className="mb-3 h-1 rounded-full bg-line" aria-hidden>
            <div className="h-1 rounded-full bg-ink transition-all" style={{ width: `${pct}%` }} />
          </div>
          <ul className="divide-y divide-line border-y border-line">
            {view.sessions.map((s) => (
              <li key={s.id}>
                <button
                  role="checkbox"
                  aria-checked={s.done}
                  disabled={busy === s.id}
                  onClick={() => toggle(s)}
                  className="flex w-full cursor-pointer items-center gap-3 py-3 text-left disabled:cursor-wait"
                >
                  <span aria-hidden className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border text-xs ${
                    s.done ? "border-ink bg-ink text-paper" : "border-muted"}`}>
                    {s.done ? "✓" : ""}
                  </span>
                  <span className="num w-24 shrink-0 text-sm text-muted">
                    {s.start ? `${s.start}–${s.end}` : `${s.minutes} min`}
                  </span>
                  <span className="min-w-0 flex-1 text-sm">
                    <span className={s.done ? "text-muted line-through" : ""}>{shortName(s.milestone_name, s.deliverable)}</span>
                    <span className="ml-2 text-xs text-muted">{pretty(s.deliverable)} · {s.kind}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted">
            Tick what you finish. Anything left unticked tonight moves to the next days, spread out, not all onto tomorrow.
          </p>
        </>
      )}
      <ErrorNote error={error} />
    </section>
  );
}
