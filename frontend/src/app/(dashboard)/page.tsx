"use client";

import { useState, type ComponentType, type FormEvent } from "react";
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
import { DocumentUpload } from "@/components/DocumentUpload";
import { useWorkspaces } from "@/context/WorkspaceContext";
import { ApiError, streamQuery } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ClaimVerification, EntailmentLabel } from "@/types";

type StreamStatus = "idle" | "loading" | "streaming" | "done" | "error";

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

export default function Home() {
  const { activeWorkspace } = useWorkspaces();

  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [answer, setAnswer] = useState("");
  const [claims, setClaims] = useState<ClaimVerification[]>([]);
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [overallScore, setOverallScore] = useState<number | null>(null);
  const [isFullySupported, setIsFullySupported] = useState<boolean | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);

  const isBusy = status === "loading" || status === "streaming";
  const inputDisabled = !activeWorkspace || isBusy;
  const hasStarted = status !== "idle";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed || !activeWorkspace || isBusy) {
      return;
    }

    setQuery("");
    setSubmittedQuery(trimmed);
    setAnswer("");
    setClaims([]);
    setOverallScore(null);
    setIsFullySupported(null);
    setStreamError(null);
    setStatus("loading");

    try {
      for await (const streamEvent of streamQuery(activeWorkspace.id, trimmed)) {
        switch (streamEvent.type) {
          case "token":
            setStatus("streaming");
            setAnswer((current) => current + streamEvent.token);
            break;
          case "verification":
            setClaims((current) => [...current, streamEvent.claim]);
            break;
          case "done":
            setAnswer(streamEvent.answer);
            setOverallScore(streamEvent.overall_score);
            setIsFullySupported(streamEvent.is_fully_supported);
            setStatus("done");
            break;
          case "error":
            setStreamError(streamEvent.detail);
            setStatus("error");
            break;
        }
      }
    } catch (cause) {
      setStreamError(
        cause instanceof ApiError
          ? cause.message
          : "An unexpected error occurred while streaming the answer."
      );
      setStatus("error");
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-800 px-6">
        <div>
          <h1 className="text-sm font-semibold text-zinc-100">
            {activeWorkspace?.name ?? "Dashboard"}
          </h1>
          <p className="text-xs text-zinc-500">
            {activeWorkspace ? "Active workspace" : "No workspace selected"}
          </p>
        </div>
        <DocumentUpload />
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
        <section className="flex min-h-0 flex-col border-r border-zinc-800">
          <div className="flex-1 overflow-y-auto">
            {!hasStarted ? (
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
                <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-4 py-3">
                  <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
                    You asked
                  </p>
                  <p className="mt-1 text-sm text-zinc-200">{submittedQuery}</p>
                </div>

                {status === "error" ? (
                  <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{streamError}</span>
                  </div>
                ) : (
                  <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 px-4 py-3 text-sm leading-relaxed text-zinc-200">
                    {status === "loading" && answer === "" ? (
                      <span className="text-zinc-500">Thinking…</span>
                    ) : (
                      <>
                        {answer}
                        {isBusy && (
                          <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-indigo-400 align-text-bottom" />
                        )}
                      </>
                    )}
                  </div>
                )}

                {status === "done" && overallScore !== null && (
                  <div
                    className={cn(
                      "flex items-center gap-2 rounded-lg border px-4 py-2 text-xs font-medium",
                      isFullySupported
                        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                        : "border-amber-500/30 bg-amber-500/10 text-amber-300"
                    )}
                  >
                    {isFullySupported ? (
                      <ShieldCheck className="h-3.5 w-3.5" />
                    ) : (
                      <AlertCircle className="h-3.5 w-3.5" />
                    )}
                    <span>
                      {isFullySupported ? "Fully supported" : "Partially supported"} —
                      overall score {overallScore.toFixed(2)}
                    </span>
                  </div>
                )}
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
                  activeWorkspace
                    ? "Ask a question about your documents..."
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
            <h2 className="text-sm font-semibold text-zinc-100">
              Verification Audit Log
            </h2>
          </div>

          {claims.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
              <FileSearch className="h-6 w-6 text-zinc-700" />
              <p className="text-sm text-zinc-500">
                Claim-level verification results will appear here in real time
                as answers are generated.
              </p>
            </div>
          ) : (
            <ul className="flex flex-1 flex-col gap-2 overflow-y-auto p-3">
              {claims.map((claim, index) => {
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
    </div>
  );
}
