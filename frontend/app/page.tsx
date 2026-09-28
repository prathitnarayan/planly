"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
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
    api<GoalSummary[]>("/goals").then(setGoals).catch((e) => setError(e.message));
  }, []);

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

      <ul className="divide-y divide-line border-y border-line">
        {goals?.map((g) => (
          <li key={g.id}>
            <Link href={`/goals/${g.id}`} className="group flex items-center justify-between gap-4 py-4">
              <span>
                <span className="block font-medium group-hover:underline underline-offset-4">{g.title}</span>
                <span className="text-xs text-muted">{stage(g)}</span>
              </span>
              <span aria-hidden className="text-muted group-hover:text-ink">→</span>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
