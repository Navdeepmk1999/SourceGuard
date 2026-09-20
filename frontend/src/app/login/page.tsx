"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Eye, EyeOff, ShieldCheck } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";

type Mode = "login" | "signup";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const confirmPasswordRef = useRef<HTMLInputElement>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmationSent, setConfirmationSent] = useState(false);
  // Set when Supabase rejects a sign-in because the address was never
  // confirmed. Without an explicit resend, a user whose first email was lost
  // or expired has no way forward from this screen at all.
  const [needsConfirmation, setNeedsConfirmation] = useState(false);
  const [isResending, setIsResending] = useState(false);
  const [resendNotice, setResendNotice] = useState<string | null>(null);
  // Supabase rate-limits resends (a 429 with an unhelpful message), so the
  // button is held closed for a cooldown rather than letting the user hit the
  // limit and see a confusing error.
  const [resendCooldown, setResendCooldown] = useState(0);

  useEffect(() => {
    if (resendCooldown <= 0) {
      return;
    }
    const timer = setTimeout(() => setResendCooldown((seconds) => seconds - 1), 1000);
    return () => clearTimeout(timer);
  }, [resendCooldown]);

  async function handleResendConfirmation() {
    if (isResending || resendCooldown > 0 || !email) {
      return;
    }
    setIsResending(true);
    setError(null);
    setResendNotice(null);

    try {
      const supabase = createClient();
      const { error: resendError } = await supabase.auth.resend({
        type: "signup",
        email,
        options: { emailRedirectTo: `${window.location.origin}/login` },
      });

      if (resendError) {
        setError(resendError.message);
        return;
      }
      // Worded so it reveals nothing about whether the address is registered.
      setResendNotice("If that address needs confirming, a new link is on its way.");
      setResendCooldown(60);
    } catch {
      setError("Unable to reach Supabase. Check your connection and try again.");
    } finally {
      setIsResending(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }

    setError(null);
    setConfirmationSent(false);
    setNeedsConfirmation(false);
    setResendNotice(null);
    setIsSubmitting(true);

    try {
      const supabase = createClient();

      if (mode === "login") {
        const { error: signInError } = await supabase.auth.signInWithPassword({
          email,
          password,
        });
        if (signInError) {
          setError(signInError.message);
          // Supabase reports this as an error code rather than a distinct
          // status, and older projects only set the message.
          const unconfirmed =
            signInError.code === "email_not_confirmed" ||
            /confirm/i.test(signInError.message);
          setNeedsConfirmation(unconfirmed);
          return;
        }
        router.push("/");
        router.refresh();
      } else {
        if (password !== confirmPassword) {
          // Checked before the network call: Supabase has no concept of a
          // confirmation field, so sending a mistyped password would create a
          // real account the user cannot sign in to.
          //
          // No banner here - the inline message under the field already says
          // this, and duplicating it puts the same sentence on screen twice.
          // Focus moves to the offending field instead, which is the more
          // useful outcome and the one a screen reader announces.
          confirmPasswordRef.current?.focus();
          return;
        }

        const { data, error: signUpError } = await supabase.auth.signUp({
          email,
          password,
        });
        if (signUpError) {
          setError(signUpError.message);
          return;
        }
        if (data.session) {
          // Email confirmation is disabled on this project - already signed in.
          router.push("/");
          router.refresh();
        } else {
          setConfirmationSent(true);
          setNeedsConfirmation(true);
        }
      }
    } catch {
      setError("Unable to reach Supabase. Check your connection and try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen w-full flex-1 items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-2 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-900 ring-1 ring-zinc-800">
            <ShieldCheck className="h-5 w-5 text-indigo-400" />
          </div>
          <h1 className="text-lg font-semibold text-zinc-100">SourceGuard</h1>
          <p className="text-sm text-zinc-500">
            {mode === "login" ? "Sign in to your workspace" : "Create an account"}
          </p>
        </div>

        <div className="mb-6 flex rounded-lg border border-zinc-800 bg-zinc-900 p-1 text-sm">
          <button
            type="button"
            onClick={() => {
              setMode("login");
              setError(null);
              setConfirmationSent(false);
              // Dropped on every mode switch: a stale value would otherwise
              // sit hidden behind the login form and reappear - already
              // mismatched - the next time signup is opened.
              setConfirmPassword("");
            }}
            className={cn(
              "flex-1 rounded-md py-1.5 font-medium transition-colors",
              mode === "login" ? "bg-indigo-500 text-white" : "text-zinc-400 hover:text-zinc-200"
            )}
          >
            Log In
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("signup");
              setError(null);
              setConfirmationSent(false);
              // Dropped on every mode switch: a stale value would otherwise
              // sit hidden behind the login form and reappear - already
              // mismatched - the next time signup is opened.
              setConfirmPassword("");
            }}
            className={cn(
              "flex-1 rounded-md py-1.5 font-medium transition-colors",
              mode === "signup" ? "bg-indigo-500 text-white" : "text-zinc-400 hover:text-zinc-200"
            )}
          >
            Sign Up
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="email" className="text-xs font-medium text-zinc-400">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none"
              placeholder="you@company.com"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="password" className="text-xs font-medium text-zinc-400">
              Password
            </label>
            <div className="relative">
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                required
                minLength={6}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 pr-10 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none"
                placeholder="••••••••"
              />
              <button
                type="button"
                onClick={() => setShowPassword((visible) => !visible)}
                // aria-label rather than a title alone: the control has no
                // text, and its meaning inverts with state.
                aria-label={showPassword ? "Hide password" : "Show password"}
                aria-pressed={showPassword}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-zinc-500 transition-colors hover:text-zinc-300"
              >
                {showPassword ? (
                  <EyeOff className="h-4 w-4" aria-hidden />
                ) : (
                  <Eye className="h-4 w-4" aria-hidden />
                )}
              </button>
            </div>
          </div>

          {mode === "signup" && (
            <div className="flex flex-col gap-1.5">
              <label htmlFor="confirm-password" className="text-xs font-medium text-zinc-400">
                Confirm Password
              </label>
              <input
                id="confirm-password"
                ref={confirmPasswordRef}
                // Intentionally follows the same toggle. Revealing one field
                // while masking the other defeats the point of the toggle,
                // which exists so the user can check what they typed.
                type={showPassword ? "text" : "password"}
                required
                minLength={6}
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                aria-invalid={Boolean(confirmPassword) && confirmPassword !== password}
                className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none"
                placeholder="••••••••"
              />
              {confirmPassword && confirmPassword !== password && (
                <p className="text-xs text-amber-400">Passwords do not match.</p>
              )}
            </div>
          )}

          {error && (
            <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}

          {confirmationSent && (
            <p className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
              Check your email to confirm your account before signing in.
            </p>
          )}

          {resendNotice && (
            <p
              role="status"
              className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300"
            >
              {resendNotice}
            </p>
          )}

          {needsConfirmation && (
            <button
              type="button"
              onClick={() => void handleResendConfirmation()}
              disabled={isResending || resendCooldown > 0 || !email}
              className="text-sm text-indigo-400 transition-colors hover:text-indigo-300 disabled:cursor-not-allowed disabled:text-zinc-600"
            >
              {isResending
                ? "Sending…"
                : resendCooldown > 0
                  ? `Resend confirmation email (${resendCooldown}s)`
                  : "Resend confirmation email"}
            </button>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="rounded-md bg-indigo-500 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-400 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-500"
          >
            {isSubmitting
              ? "Please wait…"
              : mode === "login"
                ? "Log In"
                : "Sign Up"}
          </button>

          {mode === "login" ? (
            <Link
              href="/forgot-password"
              className="text-center text-sm text-zinc-400 transition-colors hover:text-zinc-200"
            >
              Forgot your password?
            </Link>
          ) : null}
        </form>
      </div>
    </div>
  );
}
