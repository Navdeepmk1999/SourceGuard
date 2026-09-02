import { createBrowserClient } from "@supabase/ssr";

/**
 * Browser Supabase client. `createBrowserClient` is cheap to call - the
 * `@supabase/ssr` docs recommend a fresh call per use rather than a
 * module-level singleton, so this stays a plain factory function.
 */
export function createClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

  if (!url || !publishableKey) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY must be set in frontend/.env.local."
    );
  }

  return createBrowserClient(url, publishableKey);
}
