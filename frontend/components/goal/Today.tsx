"use client";

import { useState } from "react";
import { ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { day, parseDay, pretty, shortName } from "@/lib/format";
import type { TodaySession, TodayView } from "@/lib/types";

const WD = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MO = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** A progress ring: the only "colour" is ink on a grey track. */
function Ring({ value, size = 76 }: { value: number; size?: number }) {
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line)" strokeWidth="6" />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--ink)" strokeWidth="6" strokeLinecap="round"
              strokeDasharray={c} strokeDashoffset={c * (1 - Math.min(1, value))} className="grow" />
    </svg>
  );
}

function Box({ done, disabled }: { done: boolean; disabled?: boolean }) {
  return (
    <span aria-hidden className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-[1.5px] text-sm font-bold transition-colors ${
      done ? "border-ink bg-ink text-paper" : disabled ? "hatch border-line" : "border-ink/60 group-hover:border-ink"}`}>
      {done && <span className="pop">✓</span>}
    </span>
  );
}

function Row({ s, onToggle, busy, preview }: { s: TodaySession; onToggle?: () => void; busy?: boolean; preview?: boolean }) {
  const when = s.start ? `${s.start}–${s.end}` : `${s.minutes} min`;
  const inner = (
    <>
      <Box done={s.done} disabled={preview} />
      <span className="min-w-0 flex-1">
        <span className="strike text-[15px] leading-snug" data-done={s.done}>{shortName(s.milestone_name, s.deliverable)}</span>
        <span className="mt-0.5 block text-xs text-muted">{pretty(s.deliverable)} · {s.kind}</span>
      </span>
      <span className="num shrink-0 text-right text-sm text-muted">
        {when}
        {s.start && <span className="block text-xs">{s.minutes} min</span>}
      </span>
    </>
  );
  if (preview) return <li className="flex items-center gap-4 px-4 py-3.5 opacity-70">{inner}</li>;
  return (
    <li>
      <button role="checkbox" aria-checked={s.done} disabled={busy} onClick={onToggle}
              className="group flex w-full cursor-pointer items-center gap-4 px-4 py-3.5 text-left transition-colors hover:bg-soft disabled:cursor-wait">
        {inner}
      </button>
    </li>
  );
}

/** Today's sessions as checkboxes. Struck through ONLY while ticked. Unticked work is
 *  logged as missed when the day ends, and spread over the next days (not piled on one). */
export function Today({ goalId, view, onChange, onStarted }: {
  goalId: string; view: TodayView; onChange: (v: TodayView) => void; onStarted: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function toggle(s: TodaySession) {
    if (busy) return;
    setError(null);
    setBusy(s.id);
    onChange({
      ...view,
      sessions: view.sessions.map((x) => (x.id === s.id ? { ...x, done: !s.done } : x)),
      done_minutes: view.done_minutes + (s.done ? -s.minutes : s.minutes),
    });
    try {
      onChange(await api<TodayView>(`/goals/${goalId}/ticks`, { method: "PUT", body: { day: view.day, session_id: s.id, done: !s.done } }));
    } catch (e) {
      onChange(view);
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function startToday() {
    setError(null);
    setBusy("start");
    try {
      onChange(await api<TodayView>(`/goals/${goalId}/start-today`, { body: { today: view.day } }));
      onStarted();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const d = parseDay(view.day);
  const has = view.sessions.length > 0;
  const ratio = view.planned_minutes ? view.done_minutes / view.planned_minutes : 0;
  const left = view.sessions.filter((s) => !s.done).length;
  const allDone = has && left === 0;

  return (
    <section className="rise mb-10 overflow-hidden rounded-2xl border border-ink" aria-labelledby="today-h">
      {/* header: the date, big */}
      <div className="flex items-center justify-between gap-4 border-b border-line px-5 py-5">
        <div className="flex items-baseline gap-3">
          <span className="num text-5xl font-semibold leading-none tracking-tighter">{d.getDate()}</span>
          <span>
            <span id="today-h" className="block text-sm font-semibold">Today · {WD[d.getDay()]}</span>
            <span className="block text-xs text-muted">{MO[d.getMonth()]} {d.getFullYear()}</span>
          </span>
        </div>
        {has && (
          <div className="relative flex items-center justify-center">
            <Ring value={ratio} />
            <span className="num absolute text-sm font-semibold">{Math.round(ratio * 100)}%</span>
          </div>
        )}
      </div>

      {view.moved.length > 0 && (
        <div className="border-b border-line bg-soft px-5 py-3 text-sm">
          <p className="mb-0.5 text-xs font-medium uppercase tracking-wider text-muted">↻ Carried over</p>
          {view.moved.map((m) => <p key={m}>{m}</p>)}
        </div>
      )}

      {has ? (
        <>
          <ul className="divide-y divide-line">
            {view.sessions.map((s) => <Row key={s.id} s={s} busy={busy === s.id} onToggle={() => toggle(s)} />)}
          </ul>
          <p className="num border-t border-line px-5 py-3 text-xs text-muted">
            {allDone
              ? <span className="font-semibold text-ink">✓ Day done — {view.done_minutes} min. Nice.</span>
              : <>{view.done_minutes} of {view.planned_minutes} min · {left} left. Unticked tonight = moved to the coming days, spread out.</>}
          </p>
        </>
      ) : (
        <div className="px-5 py-5">
          <p className="text-sm">{view.closed ? "✓ Today is checked in." : view.message}</p>
          {view.next_day && view.next_sessions.length > 0 && (
            <>
              <p className="mb-2 mt-4 text-xs font-medium uppercase tracking-wider text-muted">
                Next up · {day(view.next_day)}
              </p>
              <ul className="-mx-5 divide-y divide-line border-y border-line">
                {view.next_sessions.map((s) => <Row key={s.id} s={s} preview />)}
              </ul>
            </>
          )}
          {view.can_start_today && (
            <button onClick={startToday} disabled={busy === "start"}
                    className="mt-4 inline-flex h-10 cursor-pointer items-center rounded-md bg-ink px-4 text-sm font-medium text-paper hover:opacity-85 disabled:opacity-40">
              {busy === "start" ? "Starting…" : "Start today instead →"}
            </button>
          )}
        </div>
      )}
      <div className="px-5"><ErrorNote error={error} /></div>
    </section>
  );
}
