"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { LearnedView } from "@/lib/types";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MIN_SESSIONS = 3;   // same as learning.py
const MIN_VIDEOS = 3;

/** What Planly has learned about how you really work — and is now planning with. */
export function Learned() {
  const [v, setV] = useState<LearnedView | null>(null);
  useEffect(() => { api<LearnedView>("/me/learned").then(setV).catch(() => {}); }, []);
  if (!v) return null;
  const l = v.learned;
  const pace = l.video_samples >= MIN_VIDEOS ? l.video_factor : null;

  return (
    <section className="rise mb-10 rounded-2xl border border-line p-5">
      <div className="mb-4 flex items-baseline justify-between gap-4">
        <h2 className="text-xs font-medium uppercase tracking-widest text-muted">What Planly learned about you</h2>
        <span className="num text-xs text-muted">{l.outcomes} sessions seen</span>
      </div>

      {l.outcomes === 0 ? (
        <p className="text-sm text-muted">
          Nothing yet. Every day you tick (or don&apos;t), Planly learns which days you really deliver and how long
          lectures really take you — then plans with your numbers instead of guesses.
        </p>
      ) : (
        <>
          {/* how much of the plan you really do, per weekday */}
          <div className="grid grid-cols-7 gap-1.5">
            {DAYS.map((d, i) => {
              const st = l.weekday[String(i)];
              const sure = st && st.sessions >= MIN_SESSIONS;
              return (
                <div key={d} className="flex flex-col items-center" title={st ? `${Math.round(st.ratio * 100)}% of planned work, ${st.sessions} sessions` : "no data yet"}>
                  <span className={`flex h-16 w-full items-end overflow-hidden rounded-lg ${sure ? "bg-soft" : "hatch"}`}>
                    {st && <span className={`grow block w-full rounded-lg ${sure ? "bg-ink" : "bg-muted/40"}`} style={{ height: `${Math.round(st.ratio * 100)}%` }} />}
                  </span>
                  <span className="mt-1 text-[11px] text-muted">{d}</span>
                  <span className="num text-[11px] font-medium">{sure ? `${Math.round(st!.ratio * 100)}%` : "—"}</span>
                </div>
              );
            })}
          </div>
          <p className="mt-2 text-[11px] text-muted">How much of the planned work you actually do each weekday. Hatched = not enough days yet.</p>

          <div className="mt-4 flex items-baseline justify-between gap-4 border-t border-line pt-3 text-sm">
            <span>Video pace</span>
            <span className="num">
              {pace ? <strong>{pace.toFixed(1)}× the video length</strong> : <span className="text-muted">1.5× (default) · learning</span>}
              <span className="ml-2 text-xs text-muted">{l.video_samples} lecture{l.video_samples === 1 ? "" : "s"} measured</span>
            </span>
          </div>

          {v.notes.length > 0 && (
            <ul className="mt-3 space-y-1 border-t border-line pt-3 text-sm">
              {v.notes.map((n) => <li key={n}>→ {n}</li>)}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
