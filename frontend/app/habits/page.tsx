"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Button, ErrorNote, Input, Label, Loading, PageTitle, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { isoToday } from "@/lib/format";
import type { HabitView, StripDay } from "@/lib/types";

const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const word = (k: "quit" | "build") => (k === "quit" ? "clean" : "done");
const shift = (iso: string, n: number) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

export default function Habits() {
  const [list, setList] = useState<HabitView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() =>
    api<HabitView[]>("/habits", { query: { today: isoToday() } }).then((v) => { setList(v); setAdding((a) => a || v.length === 0); }),
  []);
  useEffect(() => { load().catch((e) => setError(e.message)); }, [load]);

  const replace = (v: HabitView) => setList((l) => (l ?? []).map((x) => (x.habit.id === v.habit.id ? v : x)));

  if (!list) return error ? <ErrorNote error={error} /> : <Loading />;
  const active = list.filter((v) => !v.habit.archived);
  const archived = list.filter((v) => v.habit.archived);

  return (
    <>
      <Link href="/" className="mb-6 inline-block text-sm text-muted hover:text-ink">← Goals</Link>
      <PageTitle kicker="Habits" sub="Say yes or no each day — here or with one tap in Telegram. A slip resets the streak, never your record. Nothing here is penalised.">
        Streaks
      </PageTitle>

      {active.map((v, i) => (
        <HabitCard key={v.habit.id} v={v} delay={i * 60} onChange={replace} onGone={load} onError={setError} />
      ))}

      {adding ? (
        <NewHabit onDone={(v) => { if (v) setList((l) => [...(l ?? []), v]); setAdding(false); }} onError={setError}
                  cancellable={active.length > 0} />
      ) : (
        <button onClick={() => setAdding(true)}
                className="rise flex w-full items-center justify-center gap-2 rounded-2xl border border-dashed border-line py-5 text-sm text-muted hover:border-ink hover:text-ink">
          + Add a habit
        </button>
      )}

      {archived.length > 0 && (
        <details className="mt-10">
          <summary className="cursor-pointer text-xs uppercase tracking-widest text-muted">Archived · {archived.length}</summary>
          <div className="mt-4 space-y-2">
            {archived.map((v) => (
              <div key={v.habit.id} className="flex items-center justify-between rounded-lg border border-line px-4 py-3 text-sm">
                <span><span className="text-muted">{v.habit.name}</span> <span className="num text-xs text-muted">· best {v.stats.best}</span></span>
                <Button variant="ghost" onClick={async () => {
                  try { replace(await api<HabitView>(`/habits/${v.habit.id}`, { method: "PATCH", body: { archived: false, today: isoToday() } })); }
                  catch (e) { setError((e as Error).message); }
                }}>Bring back</Button>
              </div>
            ))}
          </div>
        </details>
      )}
      <ErrorNote error={error} />
    </>
  );
}

// ---------- one habit ----------

function HabitCard({ v, delay, onChange, onGone, onError }: {
  v: HabitView; delay: number; onChange: (v: HabitView) => void; onGone: () => void; onError: (e: string | null) => void;
}) {
  const h = v.habit, st = v.stats;
  const today = isoToday(), yesterday = shift(today, -1);
  const [busy, setBusy] = useState<string | null>(null);
  const [reply, setReply] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const w = word(h.kind);

  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key); onError(null);
    try { await fn(); } catch (e) { onError((e as Error).message); } finally { setBusy(null); }
  }
  const answer = (day: string, kept: boolean | null) => run(`d${day}`, async () => {
    onChange(await api<HabitView>(`/habits/${h.id}/days`, { method: "PUT", body: { day, kept, today } }));
  });
  const patch = (body: object) => run("patch", async () => {
    onChange(await api<HabitView>(`/habits/${h.id}`, { method: "PATCH", body: { ...body, today } }));
  });

  const showYesterday = st.yesterday === null && yesterday >= h.started;

  return (
    <section className="rise mb-6 overflow-hidden rounded-2xl border border-ink" style={{ animationDelay: `${delay}ms` }}>
      {/* the number */}
      <div className="flex items-start justify-between gap-4 p-5 pb-3">
        <div>
          <p className="text-xs uppercase tracking-widest text-muted">
            {h.kind === "quit" ? "Quitting" : "Building"}{h.label && <> · shows as <span className="text-ink">{h.label}</span></>}
          </p>
          <h2 className="mt-1 text-lg font-semibold tracking-tight">{h.name}</h2>
        </div>
        <div className="text-right">
          <p key={st.current} className="num pop text-5xl font-bold leading-none tracking-tight">{st.current}</p>
          <p className="mt-1 text-xs text-muted">day{st.current === 1 ? "" : "s"} {w}</p>
        </div>
      </div>

      <div className="num flex flex-wrap gap-x-5 gap-y-1 px-5 text-xs text-muted">
        <span>best <b className="text-ink">{st.best}</b></span>
        {st.window > 0 && <span><b className="text-ink">{st.kept_window}</b> of the last {st.window} days {w}</span>}
        <span>{st.total_kept} {w} in total</span>
      </div>

      {/* 30 days: filled = kept, hatched = slip, hollow = not answered. Last 7 days are tappable. */}
      <Strip strip={st.strip} started={h.started} today={today} disabled={!!busy}
             onCycle={(d) => {
               const cur = d.state;
               answer(d.day, cur === "blank" ? true : cur === "kept" ? false : null);
             }} />

      {/* today */}
      <div className="border-t border-line px-5 py-4">
        <p className="mb-2 text-sm font-medium">{h.kind === "quit" ? "Clean today?" : "Done today?"}</p>
        <div className="flex flex-wrap gap-2">
          <Choice on={st.today === true} disabled={!!busy} onClick={() => answer(today, st.today === true ? null : true)}>
            ✓ {h.kind === "quit" ? "Clean" : "Done"}
          </Choice>
          <Choice on={st.today === false} disabled={!!busy} onClick={() => answer(today, st.today === false ? null : false)}>
            ✗ {h.kind === "quit" ? "Slipped" : "Missed"}
          </Choice>
          {h.kind === "quit" && (
            <button disabled={!!busy} onClick={() => run("urge", async () => {
              const r = await api<{ reply: string; view: HabitView }>(`/habits/${h.id}/urges`, { method: "POST" });
              setReply(r.reply); onChange(r.view);
            })} className="ml-auto h-9 rounded-full border border-ink px-4 text-sm font-medium hover:bg-ink hover:text-paper disabled:opacity-40">
              🔥 Urge right now
            </button>
          )}
        </div>
        {showYesterday && (
          <p className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted">
            Yesterday isn&apos;t answered:
            <button className="underline underline-offset-4 hover:text-ink" onClick={() => answer(yesterday, true)}>{w}</button>
            <span>/</span>
            <button className="underline underline-offset-4 hover:text-ink" onClick={() => answer(yesterday, false)}>{h.kind === "quit" ? "slipped" : "missed"}</button>
          </p>
        )}
        {reply && (
          <div className="rise mt-4 whitespace-pre-line rounded-xl bg-ink px-4 py-3 text-sm leading-relaxed text-paper">
            {reply}
            <button className="mt-2 block text-xs opacity-70 hover:opacity-100" onClick={() => setReply(null)}>close</button>
          </div>
        )}
      </div>

      {/* personal lines + their own patterns */}
      {(v.lines.length > 0 || v.risk_time || v.risk_day) && (
        <div className="space-y-1.5 border-t border-line px-5 py-4 text-sm">
          {v.lines.map((l) => <p key={l} className="italic">{l}</p>)}
          {(v.risk_time || v.risk_day) && (
            <p className="pt-1 text-xs text-muted">
              From your own log: {[v.risk_time && `urges mostly ${v.risk_time}`, v.risk_day && `slips mostly on ${v.risk_day}`].filter(Boolean).join(" · ")}
              {v.risk_time && h.nudge && " — Telegram gives you a heads-up 30 min before."}
            </p>
          )}
        </div>
      )}
      {h.kind === "quit" && !v.risk_time && (
        <p className="border-t border-line px-5 py-3 text-xs text-muted">
          Log a few urges and Planly learns when they hit, then nudges you 30 minutes before. {v.urges_7d > 0 && <span className="num">({v.urges_7d} this week)</span>}
        </p>
      )}

      {/* manage */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-line bg-soft px-5 py-3 text-xs">
        <button className="text-muted hover:text-ink" onClick={() => setEditing((e) => !e)}>{editing ? "Close" : "Edit"}</button>
        {h.kind === "quit" && (
          <label className="flex items-center gap-2 text-muted">
            <input type="checkbox" className="h-3.5 w-3.5 accent-current" checked={h.nudge} onChange={(e) => patch({ nudge: e.target.checked })} />
            Heads-up before risky times
          </label>
        )}
        <button className="text-muted hover:text-ink" onClick={() => patch({ archived: true })}>Archive</button>
        {confirmDelete ? (
          <span className="ml-auto flex items-center gap-3">
            <span>Delete this habit and its history?</span>
            <button className="font-semibold underline underline-offset-4" onClick={() => run("del", async () => {
              await api(`/habits/${h.id}`, { method: "DELETE" }); onGone();
            })}>Yes, delete</button>
            <button className="text-muted" onClick={() => setConfirmDelete(false)}>Keep</button>
          </span>
        ) : (
          <button className="ml-auto text-muted hover:text-ink" onClick={() => setConfirmDelete(true)}>Delete</button>
        )}
      </div>
      {editing && (
        <EditHabit v={v} onSave={async (b) => { await patch(b); setEditing(false); }} />
      )}
    </section>
  );
}

function Choice({ on, children, ...rest }: { on: boolean; children: React.ReactNode } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button {...rest} aria-pressed={on}
            className={`h-9 rounded-full px-4 text-sm font-medium transition-colors disabled:opacity-40 ${on ? "pop bg-ink text-paper" : "border border-line hover:border-ink"}`}>
      {children}
    </button>
  );
}

function Strip({ strip, today, started, disabled, onCycle }: {
  strip: StripDay[]; today: string; started: string; disabled: boolean; onCycle: (d: StripDay) => void;
}) {
  const editableFrom = shift(today, -7);
  return (
    <div className="px-5 pb-4 pt-4">
      <div className="grid grid-cols-[repeat(15,minmax(0,1fr))] gap-[4px] sm:grid-cols-[repeat(30,minmax(0,1fr))] sm:gap-[3px]">
        {strip.map((d) => {
          const dt = new Date(`${d.day}T00:00:00`);
          const label = `${WD[dt.getDay()]} ${dt.getDate()} — ${{ kept: "kept", slip: "slipped", blank: "not answered", before: "before start" }[d.state]}`;
          const editable = d.state !== "before" && d.day >= editableFrom && d.day >= started && d.day <= today;
          const look = {
            kept: "bg-ink",
            slip: "hatch border border-ink",
            blank: "border border-line",
            before: "opacity-0",
          }[d.state];
          return (
            <button key={d.day} title={label} aria-label={label} disabled={!editable || disabled}
                    onClick={() => onCycle(d)}
                    className={`grow aspect-square rounded-[3px] ${look} ${d.day === today ? "ring-1 ring-ink ring-offset-2 ring-offset-paper" : ""} ${editable ? "cursor-pointer hover:opacity-70" : "cursor-default"}`} />
          );
        })}
      </div>
      <div className="mt-2 flex justify-between text-[10px] uppercase tracking-wider text-muted">
        <span>30 days ago</span>
        <span className="flex items-center gap-3">
          <span className="flex items-center gap-1"><i className="inline-block h-2.5 w-2.5 rounded-[2px] bg-ink" /> kept</span>
          <span className="flex items-center gap-1"><i className="hatch inline-block h-2.5 w-2.5 rounded-[2px] border border-ink" /> slip</span>
          <span className="flex items-center gap-1"><i className="inline-block h-2.5 w-2.5 rounded-[2px] border border-line" /> blank</span>
        </span>
        <span>today</span>
      </div>
    </div>
  );
}

// ---------- add / edit ----------

function NewHabit({ onDone, onError, cancellable }: {
  onDone: (v: HabitView | null) => void; onError: (e: string | null) => void; cancellable: boolean;
}) {
  const [kind, setKind] = useState<"quit" | "build">("quit");
  const [name, setName] = useState("");
  const [why, setWhy] = useState("");
  const [label, setLabel] = useState("");
  const [started, setStarted] = useState(isoToday());
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true); onError(null);
    try {
      onDone(await api<HabitView>("/habits", { body: {
        name, kind, why: why || null, label: label || null, started, today: isoToday() } }));
    } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <section className="rise rounded-2xl border border-line p-5">
      <h2 className="mb-4 font-semibold tracking-tight">New habit</h2>
      <div className="mb-4 inline-flex rounded-full border border-line p-0.5 text-sm">
        {(["quit", "build"] as const).map((k) => (
          <button key={k} onClick={() => setKind(k)}
                  className={`rounded-full px-4 py-1.5 ${kind === k ? "bg-ink text-paper" : "text-muted hover:text-ink"}`}>
            {k === "quit" ? "Stop doing" : "Start doing"}
          </button>
        ))}
      </div>
      <div className="space-y-4">
        <label className="block">
          <Label>{kind === "quit" ? "What are you stopping?" : "What are you building?"}</Label>
          <Input value={name} maxLength={80} onChange={(e) => setName(e.target.value)}
                 placeholder={kind === "quit" ? "No smoking · No Instagram after 11pm" : "Gym · Sleep by 12"} />
        </label>
        <label className="block">
          <Label hint="optional — quoted back to you on hard days">Why, in your words</Label>
          <Textarea rows={2} value={why} maxLength={200} onChange={(e) => setWhy(e.target.value)}
                    placeholder="I want to run 10k without stopping" />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <Label hint="optional">Lock-screen name</Label>
            <Input value={label} maxLength={30} onChange={(e) => setLabel(e.target.value)} placeholder="H1" />
            <span className="mt-1 block text-xs text-muted">Telegram shows this instead of the real name.</span>
          </label>
          <label className="block">
            <Label>Counting from</Label>
            <Input type="date" value={started} max={isoToday()} onChange={(e) => setStarted(e.target.value)} />
          </label>
        </div>
      </div>
      <div className="mt-5 flex gap-2">
        <Button onClick={save} disabled={busy || !name.trim()}>{busy ? "Adding…" : "Add habit"}</Button>
        {cancellable && <Button variant="ghost" onClick={() => onDone(null)}>Cancel</Button>}
      </div>
    </section>
  );
}

function EditHabit({ v, onSave }: { v: HabitView; onSave: (b: object) => Promise<void> }) {
  const [name, setName] = useState(v.habit.name);
  const [why, setWhy] = useState(v.habit.why ?? "");
  const [label, setLabel] = useState(v.habit.label ?? "");
  return (
    <div className="rise space-y-3 border-t border-line p-5">
      <Input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
      <Textarea rows={2} value={why} maxLength={200} placeholder="Why, in your words" onChange={(e) => setWhy(e.target.value)} />
      <Input value={label} maxLength={30} placeholder="Lock-screen name (optional)" onChange={(e) => setLabel(e.target.value)} />
      <Button onClick={() => onSave({ name, why: why || null, label: label || null })} disabled={!name.trim()}>Save</Button>
    </div>
  );
}
