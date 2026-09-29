"use client";

import { useState } from "react";
import { Button, ErrorNote, Label, Textarea } from "@/components/ui";
import { Reality } from "@/components/goal/Reality";
import { api } from "@/lib/api";
import type { GoalView } from "@/lib/types";

/** "What I understood" in plain lines. Fix it in your own words before any plan is built. */
export function Confirm({ goal, onChange, onBuilt }: {
  goal: GoalView;
  onChange: (g: GoalView) => void;
  onBuilt: () => void;
}) {
  const [fix, setFix] = useState("");
  const [fixing, setFixing] = useState(false);
  const [busy, setBusy] = useState<"fix" | "build" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function correct(e: React.FormEvent) {
    e.preventDefault();
    setBusy("fix");
    setError(null);
    try {
      onChange(await api<GoalView>(`/goals/${goal.id}/correct`, { body: { text: fix } }));
      setFix("");
      setFixing(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function build() {
    setBusy("build");
    setError(null);
    try {
      await api(`/goals/${goal.id}/blueprint`, { method: "POST" });
      onBuilt();
    } catch (err) {
      setError((err as Error).message);
      setBusy(null);
    }
  }

  return (
    <div>
      <Reality goalId={goal.id} />
      <p className="mb-4 text-xl">Here&apos;s what I understood.</p>
      <dl className="mb-8 divide-y divide-line border-y border-line">
        {goal.summary.map((line, i) => {
          const [k, ...rest] = line.split(":");
          return (
            <div key={i} className="grid grid-cols-1 gap-1 py-3 sm:grid-cols-[9rem_1fr]">
              <dt className="text-xs font-medium uppercase tracking-wider text-muted">{k.trim()}</dt>
              <dd className="text-sm">{rest.join(":").trim()}</dd>
            </div>
          );
        })}
      </dl>

      {!fixing ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={build} disabled={busy !== null}>{busy === "build" ? "Building your plan…" : "Looks right — build the plan"}</Button>
          <Button variant="outline" onClick={() => setFixing(true)} disabled={busy !== null}>Fix something</Button>
        </div>
      ) : (
        <form onSubmit={correct} className="space-y-3">
          <label className="block">
            <Label>What&apos;s wrong? Say it in your own words</Label>
            <Textarea rows={3} autoFocus required value={fix} onChange={(e) => setFix(e.target.value)}
                      placeholder="e.g. peer reviews are mandatory, not optional" />
          </label>
          <div className="flex gap-3">
            <Button disabled={busy !== null || !fix.trim()}>{busy === "fix" ? "Updating…" : "Update"}</Button>
            <Button type="button" variant="ghost" onClick={() => setFixing(false)}>Cancel</Button>
          </div>
        </form>
      )}
      <ErrorNote error={error} />
    </div>
  );
}
