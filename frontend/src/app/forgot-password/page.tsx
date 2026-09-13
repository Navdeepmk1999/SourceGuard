"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Loader2, MailCheck } from "lucide-react";

import { createClient } from "@/lib/supabase/client";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);

    const supabase = createClient();
    const { error: resetError } = await supabase.auth.resetPasswordForEmail(email, {
      // Where Supabase sends the user after they click the emailed link. It
      // must be on the allow-list in Supabase Auth → URL Configuration, or
      // the link silently bounces to the site root.
      redirectTo: `${window.location.origin}/reset-password`,
    });

    if (resetError) {
      setError(resetError.message);
      setIsSubmitting(false);
      return;
    }

    // Shown regardless of whether the address exists. Saying "no account
    // found" would turn this form into an account-enumeration oracle.
    setSent(true);
    setIsSubmitting(false);
  }

  if (sent) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
        <div className="w-full max-w-sm rounded-lg border border-slate-800 bg-slate-900 p-8 text-center">
          <MailCheck className="mx-auto mb-4 h-10 w-10 text-emerald-400" aria-hidden />
          <h1 className="mb-2 text-lg font-semibold text-slate-100">Check your email</h1>
          <p className="mb-6 text-sm text-slate-400">
            If an account exists for <span className="text-slate-200">{email}</span>, a reset
            link is on its way.
          </p>
          <Link href="/login" className="text-sm text-emerald-400 hover:text-emerald-300">
            Back to sign in
          </Link>
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
        <h1 className="mb-1 text-lg font-semibold text-slate-100">Reset your password</h1>
        <p className="mb-6 text-sm text-slate-400">
          We&apos;ll email you a link to choose a new one.
        </p>

        <label htmlFor="email" className="mb-2 block text-sm text-slate-300">
          Email
        </label>
        <input
          id="email"
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className="mb-4 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-emerald-500"
        />

        {error ? (
          <p role="alert" className="mb-4 text-sm text-red-400">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={isSubmitting}
          className="flex w-full items-center justify-center gap-2 rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-60"
        >
          {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
          Send reset link
        </button>

        <Link
          href="/login"
          className="mt-4 block text-center text-sm text-slate-400 hover:text-slate-200"
        >
          Back to sign in
        </Link>
      </form>
    </main>
  );
}
