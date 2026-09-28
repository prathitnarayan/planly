import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { AuthGate, SignOut } from "@/components/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "Planly",
  description: "Plans that check themselves against your real time.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <div className="mx-auto max-w-3xl px-5 pb-24 sm:px-8">
          <nav className="flex h-16 items-center justify-between border-b border-line">
            <Link href="/" className="text-lg font-bold tracking-tight">
              planly<span className="text-muted">.</span>
            </Link>
            <SignOut />
          </nav>
          <main className="pt-10">
            <AuthGate>{children}</AuthGate>
          </main>
        </div>
      </body>
    </html>
  );
}
