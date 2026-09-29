"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, ErrorNote, Input, Label, PageTitle } from "@/components/ui";
import { supabase } from "@/lib/supabase";

export default function Login() {
  const router = useRouter();
  const [mode, setMode] = useState<"in" | "up">("in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // sent here after a 401 (e.g. the account was deleted): say why
  useEffect(() => {
    const reason = new URLSearchParams(window.location.search).get("reason");
    if (reason) setError(reason);
  }, []);

  if (!supabase) {
    return <PageTitle sub="Login is off because Supabase isn't configured (local dev mode).">No login needed</PageTitle>;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNote(null);
    const auth = supabase!.auth;
    const { data, error } =
      mode === "in"
        ? await auth.signInWithPassword({ email, password })
        : await auth.signUp({ email, password, options: { emailRedirectTo: window.location.origin } });
    setBusy(false);
    if (error) return setError(error.message);
    if (mode === "up" && !data.session) return setNote("Check your email and click the confirmation link, then sign in.");
    router.replace("/");
  }

  return (
    <div className="mx-auto max-w-sm">
      <PageTitle sub="Plans that check themselves against your real time.">
        {mode === "in" ? "Sign in" : "Create account"}
      </PageTitle>
      <form onSubmit={submit} className="space-y-4">
        <label className="block">
          <Label>Email</Label>
          <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
        </label>
        <label className="block">
          <Label hint={mode === "up" ? "at least 6 characters" : undefined}>Password</Label>
          <Input type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)}
                 autoComplete={mode === "in" ? "current-password" : "new-password"} />
        </label>
        <Button className="w-full" disabled={busy}>{busy ? "…" : mode === "in" ? "Sign in" : "Create account"}</Button>
      </form>
      <ErrorNote error={error} />
      {note && <p className="mt-4 text-sm">{note}</p>}
      <p className="mt-6 text-sm text-muted">
        {mode === "in" ? "New here? " : "Have an account? "}
        <button className="cursor-pointer text-ink underline underline-offset-4" onClick={() => setMode(mode === "in" ? "up" : "in")}>
          {mode === "in" ? "Create an account" : "Sign in"}
        </button>
      </p>
    </div>
  );
}
