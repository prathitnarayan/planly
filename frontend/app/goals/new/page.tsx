"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, ErrorNote, Label, PageTitle, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import type { GoalCheck, GoalView } from "@/lib/types";

export default function NewGoal() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [check, setCheck] = useState<GoalCheck | null>(null);
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start(finalGoal: string, background: string | null) {
    setBusy(true);
    setError(null);
    try {
      const g = await api<GoalView>("/goals", { body: { goal: finalGoal, background } });
      router.push(`/goals/${g.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const c = await api<GoalCheck>("/goals/check", { body: { text } });
      if (c.is_goal) return start(text, null);
      setCheck(c);
      setGoal(c.suggested_goal ?? "");
      setBusy(false);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  if (check) {
    return (
      <>
        <PageTitle kicker="New goal" sub="That reads like where you are now. What do you want to achieve?">
          One thing first
        </PageTitle>
        <form onSubmit={(e) => { e.preventDefault(); start(goal, check.background ?? text); }} className="space-y-4">
          <label className="block">
            <Label>The goal</Label>
            <Textarea rows={2} required value={goal} onChange={(e) => setGoal(e.target.value)} autoFocus />
          </label>
          <div className="rounded-md bg-soft p-3 text-sm">
            <Label>Kept as your starting point</Label>
            {check.background ?? text}
          </div>
          <Button disabled={busy || !goal.trim()}>{busy ? "Starting…" : "Start"}</Button>
        </form>
        <ErrorNote error={error} />
      </>
    );
  }

  return (
    <>
      <PageTitle kicker="New goal" sub="A few questions, then a plan checked against your real time.">
        What do you want to achieve?
      </PageTitle>
      <form onSubmit={submit} className="space-y-4">
        <Textarea rows={3} required value={text} onChange={(e) => setText(e.target.value)} autoFocus
                  placeholder="e.g. Finish my MLP Kaggle assignments 2 and 3" />
        <Button disabled={busy || !text.trim()}>{busy ? "Thinking…" : "Continue"}</Button>
      </form>
      <ErrorNote error={error} />
    </>
  );
}
