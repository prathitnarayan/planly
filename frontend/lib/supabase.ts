import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

/** null = local dev mode: no login (the backend runs with one dev user). */
export const supabase: SupabaseClient | null = url && anonKey ? createClient(url, anonKey) : null;
export const authEnabled = supabase !== null;
