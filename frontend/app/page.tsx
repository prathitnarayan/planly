"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { DeleteGoal } from "@/components/goal/DeleteGoal";
import { ErrorNote, Loading, PageTitle } from "@/components/ui";
import { api } from "@/lib/api";
import type { GoalSummary } from "@/lib/types";

function stage(g: GoalSummary): string {
  if (!g.interview_done) return "Interview in progress";
  if (!g.has_blueprint) return "Confirm details";
  if (!g.has_capacity) return "Set your free time";
  return g.checkins ? `${g.checkins} check-ins` : "Planned";
}

export default function Goals() {
  const [goals, setGoals] = useState<GoalSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // show goals in priority order (the order they share your free time in)
    Promise.all([api<GoalSummary[]>("/goals"), api<{ goal_order: string[] }>("/me/learned").catch(() => ({ goal_order: [] as string[] }))])
      .then(([gs, me]) => {
        const rank = (id: string) => { const i = me.goal_order.indexOf(id); return i < 0 ? 1e9 : i; };
        setGoals([...gs].sort((a, b) => rank(a.id) - rank(b.id)));
      })
      .catch((e) => setError(e.message));
  }, []);

  async function move(i: number, dir: -1 | 1) {
    if (!goals) return;
    const next = [...goals];
    [next[i], next[i + dir]] = [next[i + dir], next[i]];
    setGoals(next);
    try {
      await api("/me/goal-order", { method: "PUT", body: { goal_ids: next.map((g) => g.id) } });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <>
      <PageTitle kicker="Your goals" sub="Each plan is checked against your real free time — and adjusts as you go.">
        What are you working towards?
      </PageTitle>

      <Link href="/goals/new"
            className="mb-8 flex h-12 items-center justify-center rounded-md bg-ink text-sm font-medium text-paper hover:opacity-85">
        + New goal
      </Link>

      {error && <ErrorNote error={error} />}
      {!goals && !error && <Loading />}
      {goals?.length === 0 && <p className="text-sm text-muted">No goals yet.</p>}

      {goals && goals.length > 1 && (
        <p className="mb-2 text-xs text-muted">
          Your goals share one pool of free time. Higher = first pick of your evenings; lower goals get what&apos;s left.
        </p>
      )}
      <ul className="divide-y divide-line border-y border-line">
        {goals?.map((g, i) => (
          <li key={g.id} className="flex items-center gap-2">
            {goals.length > 1 && (
              <span className="flex w-6 shrink-0 flex-col items-center text-muted">
                <button aria-label="Higher priority" disabled={i === 0} onClick={() => move(i, -1)}
                        className="cursor-pointer leading-none hover:text-ink disabled:cursor-default disabled:opacity-20">▲</button>
                <span className="num text-[10px]">{i + 1}</span>
                <button aria-label="Lower priority" disabled={i === goals.length - 1} onClick={() => move(i, 1)}
                        className="cursor-pointer leading-none hover:text-ink disabled:cursor-default disabled:opacity-20">▼</button>
              </span>
            )}
            <Link href={`/goals/${g.id}`} className="group flex min-w-0 flex-1 items-center justify-between gap-4 py-4">
              <span className="min-w-0">
                <span className="block truncate font-medium group-hover:underline underline-offset-4">{g.title}</span>
                <span className="text-xs text-muted">{stage(g)}</span>
              </span>
              <span aria-hidden className="text-muted group-hover:text-ink">→</span>
            </Link>
            <DeleteGoal compact goalId={g.id} title={g.title}
                        onDeleted={() => setGoals((gs) => (gs ?? []).filter((x) => x.id !== g.id))} />
          </li>
        ))}
      </ul>
    </>
  );
}
