"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";

import { createClient } from "@/lib/supabase/client";

/** Matches Supabase's own minimum; a shorter value is rejected server-side. */
const MIN_PASSWORD_LENGTH = 6;

export default function ResetPasswordPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasRecoverySession, setHasRecoverySession] = useState<boolean | null>(null);

  useEffect(() => {
    const supabase = createClient();

    // Supabase puts the recovery token in the URL fragment and exchanges it
    // for a session on load. That exchange is asynchronous, so checking
    // synchronously here would usually observe "no session" on a perfectly
    // valid link — hence the listener rather than a one-shot getSession().
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === "PASSWORD_RECOVERY" || session) {
        setHasRecoverySession(true);
      }
    });

    void supabase.auth.getSession().then(({ data }) => {
      setHasRecoverySession((current) => current ?? Boolean(data.session));
    });

    return () => subscription.unsubscribe();
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (password !== confirmation) {
      setError("Passwords do not match.");
      return;
    }

    setIsSubmitting(true);
    const supabase = createClient();
    const { error: updateError } = await supabase.auth.updateUser({ password });

    if (updateError) {
      setError(updateError.message);
      setIsSubmitting(false);
      return;
    }

    // Sign out so the new password is actually exercised on the next login,
    // rather than leaving the recovery session logged in.
    await supabase.auth.signOut();
    router.push("/login?reset=1");
  }

  if (hasRecoverySession === false) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
        <div className="w-full max-w-sm rounded-lg border border-slate-800 bg-slate-900 p-8 text-center">
          <h1 className="mb-2 text-lg font-semibold text-slate-100">Link expired</h1>
          <p className="mb-6 text-sm text-slate-400">
            Reset links are single-use and time-limited. Request a new one.
          </p>
          <a href="/forgot-password" className="text-sm text-emerald-400 hover:text-emerald-300">
            Request a new link
          </a>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-lg border border-slate-800 bg-slate-900 p-8"
      >
        <h1 className="mb-6 text-lg font-semibold text-slate-100">Choose a new password</h1>

        <label htmlFor="password" className="mb-2 block text-sm text-slate-300">
          New password
        </label>
        <input
          id="password"
          type="password"
          required
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="mb-4 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-500"
        />

        <label htmlFor="confirmation" className="mb-2 block text-sm text-slate-300">
          Confirm password
        </label>
        <input
          id="confirmation"
          type="password"
          required
          autoComplete="new-password"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          className="mb-4 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-500"
        />

        {error ? (
          <p role="alert" className="mb-4 text-sm text-red-400">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={isSubmitting || hasRecoverySession === null}
          className="flex w-full items-center justify-center gap-2 rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-60"
        >
          {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
          Update password
        </button>
      </form>
    </main>
  );
}
