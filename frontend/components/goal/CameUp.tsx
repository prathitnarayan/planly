"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { isoToday } from "@/lib/format";
import type { BreakKind, BreakReason, BreaksView } from "@/lib/types";

const REASONS: BreakReason[] = ["health", "family", "work", "travel", "other"];
const LOST = [30, 60, 120, 180];
const fmtMin = (m: number) => (m >= 60 ? `${m / 60} h` : `${m} min`);
const plus = (iso: string, n: number) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const half = (n: number) => (n === 0.5 ? "½" : Number.isInteger(n) ? String(n) : `${Math.floor(n)}½`);

/** "Something came up": excuse today's open work, or take days off. Applies to all goals.
 *  The checks live on the server (say it when it happens, monthly allowance, patterns);
 *  this panel only shows them BEFORE you confirm, so nothing is a surprise. */
export function CameUp({ onDone }: { onDone: () => void }) {
  const today = isoToday();
  const [open, setOpen] = useState(false);
  const [v, setV] = useState<BreaksView | null>(null);
  const [kind, setKind] = useState<BreakKind>("minutes");
  const [minutes, setMinutes] = useState(60);
  const [first, setFirst] = useState(plus(today, 1));
  const [last, setLast] = useState(plus(today, 1));
  const [reason, setReason] = useState<BreakReason | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (open && !v) api<BreaksView>("/me/breaks").then(setV).catch((e) => setMsg(e.message));
  }, [open, v]);

  async function send(path: string, init: { method?: string; body?: unknown }, done: string) {
    setBusy(true); setMsg(null);
    try {
      setV(await api<BreaksView>(path, init));
      setMsg(done);
      onDone();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }

  if (!open) {
    return (
      <div className="flex justify-end border-t border-line px-5 py-2.5">
        <button onClick={() => setOpen(true)} className="text-xs text-muted underline-offset-4 hover:text-ink hover:underline">
          Something came up?
        </button>
      </div>
    );
  }

  const cost = kind === "minutes" ? 0.5 : kind === "rest_of_day" ? 1 : first <= today ? 1 : 0;
  const left = v?.tally.allowance_left ?? 0;
  const over = cost > 0 && cost > left + 1e-9;

  return (
    <div className="rise border-t border-ink bg-soft px-5 py-5">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <p className="text-sm font-semibold">Something came up</p>
        <button onClick={() => { setOpen(false); setMsg(null); }} className="text-xs text-muted hover:text-ink">Close</button>
      </div>

      <div className="mb-4 inline-flex flex-wrap rounded-full border border-line bg-paper p-0.5 text-sm">
        {([["minutes", "Lost some time"], ["rest_of_day", "Skip the rest of today"], ["days", "Away for days"]] as const).map(([k, label]) => (
          <button key={k} onClick={() => setKind(k)}
                  className={`rounded-full px-3.5 py-1.5 ${kind === k ? "bg-ink text-paper" : "text-muted hover:text-ink"}`}>{label}</button>
        ))}
      </div>

      {kind === "minutes" && (
        <div className="mb-4 flex flex-wrap gap-2">
          {LOST.map((m) => (
            <button key={m} onClick={() => setMinutes(m)}
                    className={`num h-8 rounded-full px-3.5 text-sm ${minutes === m ? "bg-ink text-paper" : "border border-line bg-paper hover:border-ink"}`}>
              {fmtMin(m)}
            </button>
          ))}
        </div>
      )}
      {kind === "days" && (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
          <input type="date" value={first} min={today} onChange={(e) => { setFirst(e.target.value); if (e.target.value > last) setLast(e.target.value); }}
                 className="num h-9 rounded-md border border-line bg-paper px-2" />
          <span className="text-muted">to</span>
          <input type="date" value={last} min={first} onChange={(e) => setLast(e.target.value)}
                 className="num h-9 rounded-md border border-line bg-paper px-2" />
        </div>
      )}

      <div className="mb-3 flex flex-wrap gap-1.5 text-xs">
        {REASONS.map((r) => (
          <button key={r} onClick={() => setReason(reason === r ? null : r)}
                  className={`rounded-full px-3 py-1 capitalize ${reason === r ? "bg-ink text-paper" : "border border-line bg-paper text-muted hover:text-ink"}`}>
            {r}
          </button>
        ))}
        <input value={note} maxLength={120} onChange={(e) => setNote(e.target.value)} placeholder="note (optional, only you see it)"
               className="h-7 min-w-40 flex-1 rounded-full border border-line bg-paper px-3 placeholder:text-muted focus:border-ink focus:outline-none" />
      </div>

      {/* the checks, shown before confirming */}
      <ul className="mb-4 space-y-1 text-xs text-muted">
        {kind !== "days" && <li>· Only work that hasn&apos;t ended yet (+1 h) can be excused — say it when it happens.</li>}
        {kind === "days" && first > today && <li>· Told ahead: those days get no study time; the plan routes around them.</li>}
        <li>· The work itself still has to happen — it moves to the coming days, spread out. Applies to all your goals.</li>
        {v && (cost === 0
          ? <li>· Doesn&apos;t use your allowance.</li>
          : over
            ? <li className="font-semibold text-ink">! Past your allowance ({v.allowance} free days per 30). The work still moves, but this counts as missed — the streak resets.</li>
            : <li>· Uses {half(cost)} of your <span className="num">{half(left)}</span> free day{left === 1 ? "" : "s"} left this month. Inside it: no streak lost, and Planly doesn&apos;t learn anything from it.</li>)}
      </ul>
      {v?.tally.pattern && <p className="mb-4 rounded-lg border border-ink bg-paper px-3 py-2 text-xs"><strong>!</strong> {v.tally.pattern}</p>}

      <button disabled={busy || !v} onClick={() => send("/me/breaks", { body: {
                kind, minutes: kind === "minutes" ? minutes : null, first: kind === "days" ? first : null,
                last: kind === "days" ? last : null, reason, note: note || null } }, "Done — your plan is updated.")}
              className="inline-flex h-10 items-center rounded-md bg-ink px-4 text-sm font-medium text-paper hover:opacity-85 disabled:opacity-40">
        {busy ? "Saving…" : kind === "minutes" ? `Excuse ${fmtMin(minutes)}` : kind === "rest_of_day" ? "Excuse the rest of today" : "Take these days off"}
      </button>
      {msg && <p className="rise mt-3 text-sm">{msg}</p>}

      {v && v.breaks.length > 0 && (
        <div className="mt-5 border-t border-line pt-3">
          <p className="mb-1.5 flex justify-between text-[11px] uppercase tracking-wider text-muted">
            <span>Last 30 days & ahead</span>
            <span className="num normal-case tracking-normal">{v.tally.times} time{v.tally.times === 1 ? "" : "s"} · {v.tally.days_off} day{v.tally.days_off === 1 ? "" : "s"} off{v.tally.minutes ? ` · ${fmtMin(v.tally.minutes)} lost` : ""}</span>
          </p>
          <ul className="space-y-1 text-sm">
            {v.breaks.map(({ brk, label, can_undo }) => (
              <li key={brk.id} className="flex items-center justify-between gap-3">
                <span>
                  {label}
                  {brk.reason && <span className="text-muted"> · {brk.reason}</span>}
                  {!brk.free && <strong className="text-xs"> · counted as missed</strong>}
                </span>
                {can_undo && (
                  <button disabled={busy} onClick={() => send(`/me/breaks/${brk.id}`, { method: "DELETE" }, "Undone.")}
                          className="text-xs text-muted underline-offset-4 hover:text-ink hover:underline">Undo</button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
