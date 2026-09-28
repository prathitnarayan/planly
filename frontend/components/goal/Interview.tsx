"use client";

import { useState } from "react";
import { Button, ErrorNote, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import type { GoalView } from "@/lib/types";

/** One question at a time. The code (not the AI) guarantees the deadline + track-record questions. */
export function Interview({ goal, onChange }: { goal: GoalView; onChange: (g: GoalView) => void }) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onChange(await api<GoalView>(`/goals/${goal.id}/answer`, { body: { answer } }));
      setAnswer("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <p className="mb-2 text-xs font-medium uppercase tracking-widest text-muted">Question {goal.questions_asked}</p>
      <p className="mb-6 text-xl leading-snug">{goal.question}</p>
      <form onSubmit={send} className="space-y-3">
        <Textarea rows={4} value={answer} onChange={(e) => setAnswer(e.target.value)} autoFocus required
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) e.currentTarget.form?.requestSubmit(); }} />
        <div className="flex items-center gap-4">
          <Button disabled={busy || !answer.trim()}>{busy ? "Thinking…" : "Answer"}</Button>
          <span className="text-xs text-muted">⌘ + Enter</span>
        </div>
      </form>
      <ErrorNote error={error} />
    </div>
  );
}
