"use client";

import { useEffect, useState } from "react";
import { Button, ErrorNote, Input, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { hours, pretty, shortName, words } from "@/lib/format";
import type { Blueprint, EstimateCheck, Milestone } from "@/lib/types";

function total(ms: Milestone[]) {
  return ms.reduce((s, m) => s + Object.values(m.hours_by_kind).reduce((a, b) => a + (b ?? 0), 0), 0);
}

/** The plan's milestones, grouped by deliverable. Hours are the user's call — the AI only proposes. */
export function Estimates({ goalId, onDone, doneLabel = "Next: your free time" }: {
  goalId: string;
  onDone: () => void;
  doneLabel?: string;
}) {
  const [bp, setBp] = useState<Blueprint | null>(null);
  const [check, setCheck] = useState<EstimateCheck | null>(null);
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const [b, c] = await Promise.all([
      api<Blueprint>(`/goals/${goalId}/blueprint`),
      api<EstimateCheck>(`/goals/${goalId}/estimate-check`),
    ]);
    setBp(b);
    setCheck(c);
  }
  useEffect(() => { load().catch((e) => setError(e.message)); }, [goalId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function setHours(deliverable: string, h: number) {
    setError(null);
    try {
      await api(`/goals/${goalId}/blueprint/deliverables/${deliverable}`, { method: "PATCH", body: { hours: h } });
      setEditing((e) => ({ ...e, [deliverable]: "" }));
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!bp) return error ? <ErrorNote error={error} /> : <Loading label="Loading the plan" />;

  const groups = new Map<string, Milestone[]>();
  for (const m of bp.milestones) {
    const k = m.deliverable ?? m.key;
    groups.set(k, [...(groups.get(k) ?? []), m]);
  }

  return (
    <div>
      <p className="mb-2 text-xl">{bp.summary}</p>
      <p className="mb-8 text-sm text-muted">Check the hours — they decide whether the plan fits. You know your pace better than the AI.</p>

      {check?.warnings.map((w) => (
        <div key={w.deliverable} className="mb-6 rounded-lg border-2 border-ink p-4">
          <p className="text-sm">
            <strong>! {pretty(w.deliverable)}</strong> is estimated at <strong className="num">{hours(w.ai_hours)}</strong>,
            but <em>{w.benchmark_what}</em> took you <strong className="num">{hours(w.benchmark_hours)}</strong>
            {Object.keys(w.benchmark_parts).length > 0 && (
              <span className="text-muted"> ({Object.entries(w.benchmark_parts).map(([k, v]) => `${words(k)} ${v}h`).join(", ")})</span>
            )}{" "}— <span className="num">{w.ratio}×</span>.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button onClick={() => setHours(w.deliverable, w.suggested_hours)}>Use {hours(w.suggested_hours)}</Button>
            <Button variant="outline" onClick={() => setCheck({ ...check, warnings: check.warnings.filter((x) => x !== w) })}>
              Keep {hours(w.ai_hours)}
            </Button>
          </div>
        </div>
      ))}

      <div className="space-y-6">
        {[...groups.entries()].map(([key, ms]) => {
          const multi = ms.length > 1 || ms[0].deliverable;
          return (
            <section key={key} className="rounded-lg border border-line">
              <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
                <h3 className="font-semibold">{multi ? pretty(key) : ms[0].name}</h3>
                <form className="flex items-center gap-2"
                      onSubmit={(e) => { e.preventDefault(); const v = parseFloat(editing[key]); if (v > 0) setHours(key, v); }}>
                  <span className="num text-sm text-muted">{hours(total(ms))}</span>
                  <Input className="!h-8 w-20 text-right num" inputMode="decimal" placeholder="hours"
                         aria-label={`Your hours for ${pretty(key)}`}
                         value={editing[key] ?? ""} onChange={(e) => setEditing({ ...editing, [key]: e.target.value })} />
                  <Button variant="outline" className="!h-8 !px-3" disabled={!(parseFloat(editing[key]) > 0)}>Set</Button>
                </form>
              </header>
              <ul className="divide-y divide-line">
                {ms.map((m) => (
                  <li key={m.key} className="px-4 py-3">
                    <div className="flex items-baseline justify-between gap-4">
                      <span className="text-sm font-medium">
                        {multi ? shortName(m.name, m.deliverable) : "Hours"}
                        {!m.required && <span className="ml-2 text-xs text-muted">optional</span>}
                      </span>
                      <span className="num shrink-0 text-sm">{hours(total([m]))}</span>
                    </div>
                    <p className="num mt-1 text-xs text-muted">
                      {Object.entries(m.hours_by_kind).map(([k, v]) => `${k} ${hours(v ?? 0)}`).join(" · ")}
                      {m.start_after && " · after the deadline window opens"}
                    </p>
                    <ul className="mt-2 space-y-0.5">
                      {m.done_criteria.map((c) => <li key={c} className="text-xs">✓ {c}</li>)}
                    </ul>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>

      {(bp.assumptions.length > 0 || bp.open_questions.length > 0) && (
        <details className="mt-6 text-sm">
          <summary className="cursor-pointer text-muted hover:text-ink">What the AI assumed ({bp.assumptions.length + bp.open_questions.length})</summary>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-muted">
            {bp.assumptions.map((a) => <li key={a}>{a}</li>)}
            {bp.open_questions.map((q) => <li key={q}>? {q}</li>)}
          </ul>
        </details>
      )}

      <div className="mt-8 flex items-center gap-4">
        <Button onClick={onDone}>{doneLabel}</Button>
        <span className="num text-sm text-muted">Total {hours(total(bp.milestones))}</span>
      </div>
      <ErrorNote error={error} />
    </div>
  );
}
