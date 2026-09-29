"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Confirm } from "@/components/goal/Confirm";
import { Courses } from "@/components/goal/Courses";
import { Estimates } from "@/components/goal/Estimates";
import { FreeTime } from "@/components/goal/FreeTime";
import { Interview } from "@/components/goal/Interview";
import { Plan } from "@/components/goal/Plan";
import { ErrorNote, Loading, PageTitle, Rule } from "@/components/ui";
import { api } from "@/lib/api";
import type { GoalView } from "@/lib/types";

type Edit = null | "hours" | "time";
const STEPS = ["Questions", "Check", "Hours", "Free time", "Plan"];

export default function GoalPage() {
  const { id } = useParams<{ id: string }>();
  const [goal, setGoal] = useState<GoalView | null>(null);
  const [edit, setEdit] = useState<Edit>(null);
  const [reviewing, setReviewing] = useState(false);   // hours step shown until "Next"
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => api<GoalView>(`/goals/${id}`).then(setGoal).catch((e) => setError(e.message)), [id]);
  useEffect(() => { reload(); }, [reload]);

  if (!goal) return error ? <ErrorNote error={error} /> : <Loading />;

  const step = !goal.interview_done ? 0 : !goal.has_blueprint ? 1 : reviewing || edit === "hours" ? 2
    : !goal.has_capacity || edit === "time" ? 3 : 4;

  return (
    <>
      <Link href="/" className="mb-6 inline-block text-sm text-muted hover:text-ink">← Goals</Link>
      <PageTitle>{goal.goal}</PageTitle>

      <ol className="mb-10 flex gap-1" aria-label="Progress">
        {STEPS.map((s, i) => (
          <li key={s} className="flex-1">
            <span className={`block h-1 rounded-full ${i <= step ? "bg-ink" : "bg-line"}`} />
            <span className={`mt-1.5 hidden text-xs sm:block ${i === step ? "font-medium" : "text-muted"}`}>{s}</span>
          </li>
        ))}
      </ol>

      {step === 0 && <Interview goal={goal} onChange={setGoal} />}
      {step === 1 && <Confirm goal={goal} onChange={setGoal} onBuilt={() => { setReviewing(true); reload(); }} />}
      {step === 2 && (
        <Estimates goalId={id} doneLabel={goal.has_capacity ? "Back to the plan" : "Next: your free time"}
                   onDone={() => { setReviewing(false); setEdit(null); reload(); }} />
      )}
      {step === 3 && <FreeTime goalId={id} firstTime={!goal.plan_start} onSaved={() => { setEdit(null); reload(); }} />}
      {step === 4 && (
        <Plan goalId={id} hasCheckins={!!goal.checked_through}
              onEditHours={() => setEdit("hours")} onEditTime={() => setEdit("time")} />
      )}
      {goal.interview_done && (
        <>
          <Rule />
          <Courses goalId={id} onChanged={reload} />
        </>
      )}
      <ErrorNote error={error} />
    </>
  );
}
