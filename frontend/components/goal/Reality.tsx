"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { isoToday } from "@/lib/format";
import type { RealityView } from "@/lib/types";

/** "UPSC in a month?" — the usual prep time for well-known goals vs your runway.
 *  Warns, never blocks. `quiet`: show nothing when the runway is fine (plan page). */
export function Reality({ goalId, quiet = false }: { goalId: string; quiet?: boolean }) {
  const [r, setR] = useState<RealityView | null>(null);
  useEffect(() => {
    api<RealityView>(`/goals/${goalId}/reality`, { query: { today: isoToday() } }).then(setR).catch(() => {});
  }, [goalId]);
  if (!r || r.verdict.level === "unknown") return null;
  const v = r.verdict;
  if (quiet && v.level === "ok") return null;

  if (v.level === "ok") {
    return (
      <p className="rise mb-8 rounded-lg border border-line px-4 py-3 text-sm">
        <span className="mr-1.5">✓</span>{v.headline} <span className="text-muted">{v.lines[0]}</span>
      </p>
    );
  }

  const share = Math.max(0, Math.min(1, v.share_of_minimum ?? 0));
  return (
    <section className={`rise mb-8 rounded-2xl p-5 ${v.level === "unrealistic" ? "border-2 border-ink" : "border border-ink"}`}>
      <p className="text-lg font-semibold tracking-tight">
        {v.level === "unrealistic" ? "! This deadline is far shorter than this goal usually takes." : "~ This is tight."}
      </p>
      <p className="mt-1 text-sm">{v.headline}</p>

      {/* your runway as a share of the usual minimum */}
      <div className="mt-4">
        <div className="hatch relative h-3 overflow-hidden rounded-full border border-line">
          <div className="grow h-full rounded-full bg-ink" style={{ width: `${Math.max(1.5, share * 100)}%` }} />
        </div>
        <div className="num mt-1.5 flex justify-between text-xs text-muted">
          <span>your runway ≈ {Math.round(share * 100)}%</span>
          <span>usual minimum</span>
        </div>
      </div>

      <ul className="mt-4 space-y-1 text-sm">
        {v.lines.slice(0, -1).map((l) => <li key={l}>{l}</li>)}
      </ul>
      {v.suggestions.length > 0 && (
        <div className="mt-4 border-t border-line pt-3 text-sm">
          <p className="mb-1 text-xs font-medium uppercase tracking-wider text-muted">What would work</p>
          {v.suggestions.map((s) => <p key={s}>→ {s}</p>)}
        </div>
      )}
      <p className="mt-3 text-xs text-muted">{v.lines[v.lines.length - 1]} You can still go ahead — Planly will plan it honestly.</p>
    </section>
  );
}
