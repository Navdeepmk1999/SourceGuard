"use client";

// SubmitEvent, not the deprecated FormEvent - @types/react marks the latter
// "doesn't actually exist" and points at SubmitEvent for form submission.
import { useEffect, useRef, useState, type ComponentType, type SubmitEvent } from "react";
import {
  AlertCircle,
  AlertTriangle,
  ArrowUp,
  CheckCircle2,
  FileSearch,
  Loader2,
  MessageSquare,
  Search,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { ApiError, streamQuery } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ClaimVerification, EntailmentLabel, Workspace } from "@/types";

type StreamStatus = "idle" | "loading" | "streaming" | "done" | "error";

export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** Populated on assistant turns as `verification` frames arrive. */
  claims?: ClaimVerification[];
  overallScore?: number | null;
  isFullySupported?: boolean | null;
  /** Set instead of `content` when the turn failed. */
  error?: string;
}

const CLAIM_STYLES: Record<
  EntailmentLabel,
  {
    border: string;
    bg: string;
    text: string;
    label: string;
    icon: ComponentType<{ className?: string }>;
  }
> = {
  entailed: {
    border: "border-emerald-500/30",
    bg: "bg-emerald-500/10",
    text: "text-emerald-300",
    label: "Entailed",
    icon: CheckCircle2,
  },
  not_entailed: {
    border: "border-red-500/30",
    bg: "bg-red-500/10",
    text: "text-red-300",
    label: "Not entailed",
    icon: XCircle,
  },
  insufficient_evidence: {
    border: "border-amber-500/30",
    bg: "bg-amber-500/10",
    text: "text-amber-300",
    label: "Insufficient evidence",
    icon: AlertCircle,
  },
};

/**
 * The multi-turn chat thread plus its verification audit pane.
 *
 * Split out of `page.tsx` and mounted with `key={workspace.id}` so switching
 * workspaces *remounts* it - which resets `messages` and `sessionId` for
 * free. That's React's documented way to reset state on a prop change, and
 * it avoids resetting via a `setState`-inside-`useEffect`, which this repo's
 * lint config rejects (`react-hooks/set-state-in-effect`).
 */
export function ChatPanel({ workspace }: { workspace: Workspace | null }) {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<ChatTurn[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<StreamStatus>("idle");

  const scrollRef = useRef<HTMLDivElement>(null);

  const isBusy = status === "loading" || status === "streaming";
  const inputDisabled = !workspace || isBusy;

  // Auto-scroll to the newest turn. Depends on the streamed content too, not
  // just message count, so the view keeps following a long answer as tokens
  // land rather than only jumping once per turn.
  const lastContent = messages[messages.length - 1]?.content ?? "";
  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages.length, lastContent]);

  // The audit pane mirrors the most recent assistant turn that produced claims.
  const latestClaims =
    [...messages].reverse().find((m) => m.claims && m.claims.length > 0)?.claims ?? [];

  function updateTurn(id: string, patch: Partial<ChatTurn>) {
    setMessages((current) =>
      current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn))
    );
  }

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed || !workspace || isBusy) {
      return;
    }

    const assistantId = crypto.randomUUID();
    setQuery("");
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", content: trimmed },
      { id: assistantId, role: "assistant", content: "", claims: [] },
    ]);
    setStatus("loading");

    try {
      for await (const streamEvent of streamQuery(workspace.id, trimmed, sessionId)) {
        switch (streamEvent.type) {
          case "session":
            // Sent before any token, so a brand-new conversation is
            // continuable from the very next turn.
            setSessionId(streamEvent.session_id);
            break;
          case "token":
            setStatus("streaming");
            setMessages((current) =>
              current.map((turn) =>
                turn.id === assistantId
                  ? { ...turn, content: turn.content + streamEvent.token }
                  : turn
              )
            );
            break;
          case "verification":
            setMessages((current) =>
              current.map((turn) =>
                turn.id === assistantId
                  ? { ...turn, claims: [...(turn.claims ?? []), streamEvent.claim] }
                  : turn
              )
            );
            break;
          case "done":
            if (streamEvent.session_id) {
              setSessionId(streamEvent.session_id);
            }
            updateTurn(assistantId, {
              content: streamEvent.answer,
              overallScore: streamEvent.overall_score,
              isFullySupported: streamEvent.is_fully_supported,
            });
            setStatus("done");
            break;
          case "error":
            updateTurn(assistantId, { error: streamEvent.detail });
            setStatus("error");
            break;
        }
      }
    } catch (cause) {
      updateTurn(assistantId, {
        error:
          cause instanceof ApiError
            ? cause.message
            : "An unexpected error occurred while streaming the answer.",
      });
      setStatus("error");
    }
  }

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
      <section className="flex min-h-0 flex-col border-r border-zinc-800">
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-zinc-900 ring-1 ring-zinc-800">
                <MessageSquare className="h-5 w-5 text-zinc-500" />
              </div>
              <div>
                <p className="text-sm font-medium text-zinc-300">
                  Ask a question about your documents
                </p>
                <p className="mt-1 max-w-sm text-sm text-zinc-500">
                  Answers are grounded in your workspace, and every claim is
                  verified against its source before it&apos;s shown to you.
                </p>
              </div>
            </div>
          ) : (
            <div className="mx-auto flex max-w-2xl flex-col gap-4 px-6 py-6">
              {messages.map((turn, index) => {
                const isLast = index === messages.length - 1;

                if (turn.role === "user") {
                  return (
                    <div key={turn.id} className="flex justify-end">
                      <div className="max-w-[85%] rounded-lg bg-indigo-500/10 px-4 py-2.5 text-sm text-indigo-100 ring-1 ring-inset ring-indigo-500/30">
                        {turn.content}
                      </div>
                    </div>
                  );
                }

                if (turn.error) {
                  return (
                    <div
                      key={turn.id}
                      className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300"
                    >
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                      <span>{turn.error}</span>
                    </div>
                  );
                }

                return (
                  <div key={turn.id} className="flex flex-col gap-2">
                    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-4 py-3 text-sm leading-relaxed text-zinc-200">
                      {turn.content === "" && isBusy && isLast ? (
                        <span className="text-zinc-500">Thinking…</span>
                      ) : (
                        <>
                          {turn.content}
                          {isBusy && isLast && (
                            <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-indigo-400 align-text-bottom" />
                          )}
                        </>
                      )}
                    </div>

                    {turn.overallScore != null && (
                      <div
                        className={cn(
                          "flex items-center gap-2 self-start rounded-lg border px-3 py-1.5 text-xs font-medium",
                          turn.isFullySupported
                            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                            : "border-amber-500/30 bg-amber-500/10 text-amber-300"
                        )}
                      >
                        {turn.isFullySupported ? (
                          <ShieldCheck className="h-3.5 w-3.5" />
                        ) : (
                          <AlertCircle className="h-3.5 w-3.5" />
                        )}
                        <span>
                          {turn.isFullySupported ? "Fully supported" : "Partially supported"} —
                          overall score {turn.overallScore.toFixed(2)}
                        </span>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <form onSubmit={handleSubmit} className="border-t border-zinc-800 p-4">
          <div className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2">
            <Search className="h-4 w-4 shrink-0 text-zinc-500" />
            <input
              type="text"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              disabled={inputDisabled}
              placeholder={
                workspace
                  ? messages.length > 0
                    ? "Ask a follow-up..."
                    : "Ask a question about your documents..."
                  : "Select a workspace to start querying..."
              }
              className="w-full bg-transparent text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none disabled:cursor-not-allowed"
            />
            <button
              type="submit"
              disabled={inputDisabled || query.trim().length === 0}
              aria-label="Send query"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-500 text-white transition-colors hover:bg-indigo-400 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-600"
            >
              {isBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <ArrowUp className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
        </form>
      </section>

      <aside className="flex min-h-0 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-zinc-800 px-4">
          <ShieldCheck className="h-4 w-4 text-indigo-400" />
          <h2 className="text-sm font-semibold text-zinc-100">Verification Audit Log</h2>
        </div>

        {latestClaims.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
            <FileSearch className="h-6 w-6 text-zinc-700" />
            <p className="text-sm text-zinc-500">
              Claim-level verification results will appear here in real time as
              answers are generated.
            </p>
          </div>
        ) : (
          <ul className="flex flex-1 flex-col gap-2 overflow-y-auto p-3">
            {latestClaims.map((claim, index) => {
              const style = CLAIM_STYLES[claim.label];
              const Icon = style.icon;
              return (
                <li
                  key={index}
                  className={cn(
                    "rounded-lg border px-3 py-2 text-sm",
                    style.border,
                    style.bg
                  )}
                >
                  <div
                    className={cn(
                      "flex items-center gap-1.5 text-xs font-medium",
                      style.text
                    )}
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0" />
                    <span>{style.label}</span>
                    <span className="ml-auto text-zinc-500">
                      {Math.round(claim.score * 100)}%
                    </span>
                  </div>
                  <p className="mt-1 text-zinc-300">{claim.claim}</p>
                </li>
              );
            })}
          </ul>
        )}
      </aside>
    </div>
  );
}
