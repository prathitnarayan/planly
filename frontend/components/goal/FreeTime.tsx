"use client";

import { useEffect, useState } from "react";
import { Button, ErrorNote, Input, Label } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Capacity } from "@/lib/types";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
type Row = { hours: string; start: string };

function tomorrowIso() {
  const d = new Date(Date.now() + 86_400_000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function toSlotEnd(start: string, hours: number): string | null {
  const [h, m] = start.split(":").map(Number);
  const end = h * 60 + m + Math.round(hours * 60);
  if (end >= 24 * 60) return null;
  return `${String(Math.floor(end / 60)).padStart(2, "0")}:${String(end % 60).padStart(2, "0")}`;
}

/** Real free time per day. The planner only fills 80% of it — the rest is buffer. */
export function FreeTime({ goalId, firstTime, onSaved }: { goalId: string; firstTime: boolean; onSaved: () => void }) {
  const [rows, setRows] = useState<Row[]>(
    DAYS.map((_, i) => (i < 5 ? { hours: "1.5", start: "19:00" } : { hours: "4", start: "10:00" })),
  );
  const [start, setStart] = useState(tomorrowIso());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (firstTime) return;   // nothing saved yet — don't ask (avoids a noisy 404)
    api<Capacity>(`/goals/${goalId}/capacity`)
      .then((c) => {
        const next: Row[] = DAYS.map(() => ({ hours: "0", start: "19:00" }));
        for (const s of c.slots) {
          const [sh, sm] = s.start.split(":").map(Number);
          const [eh, em] = s.end.split(":").map(Number);
          next[s.weekday] = { hours: String(((eh * 60 + em) - (sh * 60 + sm)) / 60), start: s.start.slice(0, 5) };
        }
        setRows(next);
      })
      .catch((e) => { if (!(e instanceof ApiError && e.status === 404)) setError(e.message); });
  }, [goalId, firstTime]);

  const week = rows.reduce((s, r) => s + (parseFloat(r.hours) || 0), 0);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const slots = [];
    for (const [i, r] of rows.entries()) {
      const h = parseFloat(r.hours) || 0;
      if (h <= 0) continue;
      const end = toSlotEnd(r.start, h);
      if (!end) return setError(`${DAYS[i]}: ${h}h from ${r.start} runs past midnight — start earlier.`);
      slots.push({ weekday: i, start: r.start, end });
    }
    setBusy(true);
    setError(null);
    try {
      await api(`/goals/${goalId}/capacity`, { method: "PUT", body: { slots }, query: firstTime ? { start } : undefined });
      onSaved();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  const set = (i: number, patch: Partial<Row>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <form onSubmit={save}>
      <p className="mb-2 text-xl">When are you free?</p>
      <p className="mb-2 text-sm text-muted">Be honest, not hopeful. Planly plans on 80% of this, so a bad day doesn&apos;t sink the week.</p>
      <p className="mb-6 text-sm text-muted">This is your free time for <strong className="text-ink">all</strong> your goals: they share it,
        and the one higher on your goals list gets first pick. Changing it here changes it for every goal.</p>

      <div className="mb-2 grid grid-cols-[3rem_1fr_1fr] gap-x-3 text-xs font-medium uppercase tracking-wider text-muted">
        <span /> <span>Hours</span> <span>From</span>
      </div>
      <div className="space-y-2">
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-[3rem_1fr_1fr] items-center gap-x-3">
            <span className="text-sm font-medium">{DAYS[i]}</span>
            <Input className="num" inputMode="decimal" value={r.hours} onChange={(e) => set(i, { hours: e.target.value })}
                   aria-label={`${DAYS[i]} hours`} />
            <Input type="time" value={r.start} onChange={(e) => set(i, { start: e.target.value })} aria-label={`${DAYS[i]} start`} />
          </div>
        ))}
      </div>
      <p className="num mt-3 text-sm">{week}h a week free → plans on <strong>{Math.round(week * 0.8 * 10) / 10}h</strong></p>

      {firstTime && (
        <label className="mt-6 block max-w-48">
          <Label>Start on</Label>
          <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} required />
        </label>
      )}
      <Button className="mt-8" disabled={busy || week <= 0}>{busy ? "Planning…" : "See my plan"}</Button>
      <ErrorNote error={error} />
    </form>
  );
}
