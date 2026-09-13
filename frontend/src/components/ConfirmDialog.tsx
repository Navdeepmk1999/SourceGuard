"use client";

import { useEffect, useRef } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** What will be destroyed. Cascading deletes are invisible otherwise. */
  description: React.ReactNode;
  confirmLabel: string;
  isBusy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Blocking confirmation for destructive actions.
 *
 * Deliberately not `window.confirm`: it cannot show the blast radius (how
 * many documents and embeddings a cascade will remove), cannot render a busy
 * state while the request is in flight, and is styled by the browser.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  isBusy = false,
  error = null,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }

    // Focus Cancel, not Confirm: for a destructive dialog the safe option
    // should be the one that Enter triggers.
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !isBusy) {
        onCancel();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      // Return focus to whatever opened the dialog, so keyboard users are not
      // dropped back at the top of the document.
      previouslyFocused.current?.focus?.();
    };
  }, [open, isBusy, onCancel]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      // Clicking the backdrop cancels, but only via the backdrop itself -
      // the check stops a click inside the panel from bubbling up and
      // closing the dialog.
      onClick={(event) => {
        if (event.target === event.currentTarget && !isBusy) {
          onCancel();
        }
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-description"
        className="w-full max-w-sm rounded-lg border border-zinc-800 bg-zinc-950 p-5 shadow-xl"
      >
        <div className="mb-3 flex items-start gap-3">
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-red-500/10">
            <AlertTriangle className="h-4 w-4 text-red-400" aria-hidden />
          </span>
          <div className="min-w-0">
            <h2 id="confirm-dialog-title" className="text-sm font-semibold text-zinc-100">
              {title}
            </h2>
            <div id="confirm-dialog-description" className="mt-1 text-xs leading-relaxed text-zinc-400">
              {description}
            </div>
          </div>
        </div>

        {error ? (
          <p role="alert" className="mb-3 text-xs text-red-400">
            {error}
          </p>
        ) : null}

        <div className="flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={isBusy}
            className="rounded-md px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-900 hover:text-zinc-100 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={isBusy}
            className={cn(
              "flex items-center gap-1.5 rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors",
              "hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-60"
            )}
          >
            {isBusy ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : null}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
