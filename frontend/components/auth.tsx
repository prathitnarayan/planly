"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { authEnabled, supabase } from "@/lib/supabase";

/** Everything except /login needs a signed-in user (when login is enabled). */
export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const [ready, setReady] = useState(!authEnabled);

  useEffect(() => {
    if (!supabase) return;
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session && path !== "/login") router.replace("/login");
      else setReady(true);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!session && path !== "/login") router.replace("/login");
    });
    return () => sub.subscription.unsubscribe();
  }, [path, router]);

  if (!ready && path !== "/login") return null;
  return <>{children}</>;
}

export function SignOut() {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  useEffect(() => {
    supabase?.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? null));
  }, []);
  if (!supabase || !email) return authEnabled ? null : <span className="text-xs text-muted">dev mode · no login</span>;
  return (
    <span className="flex items-center gap-3 text-xs text-muted">
      <span className="hidden sm:inline">{email}</span>
      <button
        className="cursor-pointer hover:text-ink hover:underline underline-offset-4"
        onClick={async () => {
          await supabase!.auth.signOut();
          router.replace("/login");
        }}
      >
        Sign out
      </button>
    </span>
  );
}
