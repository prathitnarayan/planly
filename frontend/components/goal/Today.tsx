"use client";

import { useState } from "react";
import { ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { day, parseDay, pretty, shortName } from "@/lib/format";
import type { IntegrityEvent, SessionItem, Standing, TodaySession, TodayView } from "@/lib/types";

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

function Box({ done, disabled, locked }: { done: boolean; disabled?: boolean; locked?: boolean }) {
  return (
    <span aria-hidden className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-[1.5px] text-sm font-bold transition-colors ${
      done ? "border-ink bg-ink text-paper" : disabled || locked ? "hatch border-line text-muted" : "border-ink/60 group-hover:border-ink"}`}>
      {done ? <span className="pop">✓</span> : locked ? <span className="text-xs">⊘</span> : null}
    </span>
  );
}

const GLYPH = { video: "▶", site: "◇", none: "○" } as const;

/** The lectures / problems this session covers, with live evidence. */
function Items({ items, watched }: { items: SessionItem[]; watched: Record<string, number> }) {
  if (!items.length) return null;
  return (
    <ul className="mt-2 space-y-1">
      {items.map((it) => {
        const part = it.part_from > 0 || it.part_to < 1 ? ` · ${Math.round(it.part_from * 100)}–${Math.round(it.part_to * 100)}%` : "";
        const w = it.video_key ? watched[it.video_key] : undefined;
        return (
          <li key={it.key + it.part_from} className="flex items-center gap-2 text-xs">
            <span aria-hidden className="w-3 text-center text-muted">{GLYPH[it.verify]}</span>
            <span className="min-w-0 flex-1 truncate">{it.title}<span className="text-muted">{part}</span></span>
            {it.verify === "video" && (
              <span className="flex shrink-0 items-center gap-1.5 text-muted">
                <span className="hidden h-1 w-12 overflow-hidden rounded-full bg-line sm:block">
                  <span className="grow block h-1 bg-ink" style={{ width: `${Math.round((w ?? 0) * 100)}%` }} />
                </span>
                <span className="num w-12 whitespace-nowrap text-right">{w === undefined ? "0%" : `${Math.round(w * 100)}%`}</span>
              </span>
            )}
            {it.verify === "site" && <span className="shrink-0 text-muted">site</span>}
            {it.verify === "none" && <span className="shrink-0 text-muted">self</span>}
          </li>
        );
      })}
    </ul>
  );
}

function Row({ s, onToggle, busy, preview, watched = {} }: {
  s: TodaySession; onToggle?: () => void; busy?: boolean; preview?: boolean; watched?: Record<string, number>;
}) {
  const when = s.start ? `${s.start}–${s.end}` : `${s.minutes} min`;
  const inner = (
    <>
      <Box done={s.done} disabled={preview} locked={s.locked} />
      <span className="min-w-0 flex-1">
        <span className="strike text-[15px] leading-snug" data-done={s.done}>{shortName(s.milestone_name, s.deliverable)}</span>
        <span className="mt-0.5 block text-xs text-muted">
          {pretty(s.deliverable)} · {s.kind}
          {s.auto && <strong className="ml-1 font-medium text-ink">· ticked by evidence</strong>}
          {s.locked && <span className="ml-1">· ticks itself when watched / solved</span>}
        </span>
        <Items items={s.items} watched={watched} />
      </span>
      <span className="num shrink-0 text-right text-sm text-muted">
        {when}
        {s.start && <span className="block text-xs">{s.minutes} min</span>}
      </span>
    </>
  );
  if (preview) return <li className="flex items-start gap-4 px-4 py-3.5 opacity-70">{inner}</li>;
  return (
    <li>
      <button role="checkbox" aria-checked={s.done} aria-disabled={s.locked && !s.done} disabled={busy} onClick={onToggle}
              className={`group flex w-full items-start gap-4 px-4 py-3.5 text-left transition-colors hover:bg-soft disabled:cursor-wait ${
                s.locked && !s.done ? "cursor-not-allowed" : "cursor-pointer"}`}>
        {inner}
      </button>
    </li>
  );
}

const VERDICT: Record<string, [string, string]> = {
  verified: ["✓", "verified"], partial: ["~", "partly done"], self: ["○", "self-reported"], mismatch: ["!", "mismatch"],
};

function StandingChips({ st }: { st: Standing }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="num rounded-full border border-line px-2.5 py-1" title="Trust: -15 per mismatch, -5 partial, +2 verified">
        Trust <strong className={st.trust < 70 ? "" : "font-semibold"}>{st.trust}</strong>
      </span>
      <span className="num rounded-full border border-line px-2.5 py-1" title="Days in a row with every tick holding up">
        Streak <strong className="font-semibold">{st.streak}d</strong>
      </span>
      {st.owed_minutes > 0 && (
        <span className="num rounded-full border border-ink px-2.5 py-1" title="Penalty time added to your plan">
          Owed <strong>+{st.owed_minutes}m</strong>
        </span>
      )}
      {st.evidence_only && <span className="rounded-full bg-ink px-2.5 py-1 font-medium text-paper">⊘ Evidence only</span>}
    </div>
  );
}

function LastDay({ events }: { events: IntegrityEvent[] }) {
  if (!events.length) return null;
  return (
    <div className="border-b border-line px-5 py-3 text-sm">
      <p className="mb-1 text-xs font-medium uppercase tracking-wider text-muted">Checked · {day(events[0].day)}</p>
      <ul className="space-y-0.5">
        {events.map((e, i) => (
          <li key={i} className={e.verdict === "mismatch" ? "font-semibold" : ""}>
            <span className="mr-1.5 inline-block w-3 text-center">{VERDICT[e.verdict][0]}</span>
            {e.session} — {VERDICT[e.verdict][1]}
            <span className="num text-xs font-normal text-muted">
              {" "}· {e.credit_minutes}/{e.claimed_minutes} min counted{e.penalty_minutes ? ` · +${e.penalty_minutes} min owed` : ""}
              {e.detail && ` · ${e.detail}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
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
    if (s.locked && !s.done) {
      setError(view.standing?.lock_reason ? `${view.standing.lock_reason} Watch / solve it and it ticks itself.`
        : "Evidence only: it ticks itself when watched / solved.");
      return;
    }
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
        {view.standing && <div className="hidden sm:block"><StandingChips st={view.standing} /></div>}
        {has && (
          <div className="relative flex items-center justify-center">
            <Ring value={ratio} />
            <span className="num absolute text-sm font-semibold">{Math.round(ratio * 100)}%</span>
          </div>
        )}
      </div>

      {view.standing && <div className="border-b border-line px-5 py-2.5 sm:hidden"><StandingChips st={view.standing} /></div>}
      {view.standing?.lock_reason && (
        <p className="border-b border-line px-5 py-2.5 text-sm"><strong>⊘</strong> {view.standing.lock_reason}</p>
      )}
      {view.standing && <LastDay events={view.standing.last_day} />}
      {view.moved.length > 0 && (
        <div className="border-b border-line bg-soft px-5 py-3 text-sm">
          <p className="mb-0.5 text-xs font-medium uppercase tracking-wider text-muted">↻ Carried over</p>
          {view.moved.map((m) => <p key={m}>{m}</p>)}
        </div>
      )}

      {has ? (
        <>
          <ul className="divide-y divide-line">
            {view.sessions.map((s) => <Row key={s.id} s={s} busy={busy === s.id} watched={view.watched} onToggle={() => toggle(s)} />)}
          </ul>
          <p className="num border-t border-line px-5 py-3 text-xs text-muted">
            {allDone
              ? <span className="font-semibold text-ink">✓ Day done — {view.done_minutes} min. Nice.</span>
              : <>{view.done_minutes} of {view.planned_minutes} min · {left} left. Unticked tonight = moved to the coming days, spread out.</>}
          </p>
          {view.sessions.some((s) => s.items.length) && (
            <p className="border-t border-line px-5 py-2.5 text-[11px] text-muted">
              ▶ checked by watch time (Planly extension) · ◇ checked on the course site · ○ self-reported.
              Ticks are checked tonight: disproved = no credit, +25% time owed, trust −15.
            </p>
          )}
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
