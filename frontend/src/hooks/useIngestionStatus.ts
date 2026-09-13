"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getDocumentStatus } from "@/lib/api";
import { TERMINAL_STATUSES, type DocumentStatusRead } from "@/types";

/** Poll cadence. Ingestion is paced by the embedding provider's rate limit,
 *  so sub-second polling would only burn the API's own rate budget. */
const POLL_INTERVAL_MS = 2500;
/** Stop after ~10 minutes. A task killed by a restart never reaches a
 *  terminal status, so an unbounded poll would run until the tab closes. */
const MAX_POLL_MS = 10 * 60 * 1000;

export interface IngestionTracker {
  statuses: Record<string, DocumentStatusRead>;
  track: (documentId: string) => void;
  untrack: (documentId: string) => void;
  isPolling: boolean;
}

/**
 * Polls `GET /documents/{id}/status` for documents still being ingested.
 *
 * Upload returns 202 before anything is parsed, so the UI has no counts to
 * show and no way to know when a document becomes searchable without asking.
 */
export function useIngestionStatus(
  onSettled?: (status: DocumentStatusRead) => void
): IngestionTracker {
  const [statuses, setStatuses] = useState<Record<string, DocumentStatusRead>>({});
  const [pending, setPending] = useState<string[]>([]);
  const startedAt = useRef<Record<string, number>>({});

  // Held in a ref so a caller passing an inline arrow does not restart the
  // interval on every render.
  const settledCallback = useRef(onSettled);
  useEffect(() => {
    settledCallback.current = onSettled;
  }, [onSettled]);

  const track = useCallback((documentId: string) => {
    startedAt.current[documentId] = Date.now();
    setPending((ids) => (ids.includes(documentId) ? ids : [...ids, documentId]));
  }, []);

  const untrack = useCallback((documentId: string) => {
    setPending((ids) => ids.filter((id) => id !== documentId));
  }, []);

  useEffect(() => {
    if (pending.length === 0) {
      return;
    }

    let cancelled = false;

    async function poll() {
      const results = await Promise.allSettled(pending.map((id) => getDocumentStatus(id)));
      if (cancelled) {
        return;
      }

      const settled: string[] = [];
      const next: Record<string, DocumentStatusRead> = {};

      results.forEach((result, index) => {
        const documentId = pending[index];

        if (result.status === "rejected") {
          // A 404 means the document was deleted while we were watching it.
          // Anything else is transient - keep polling rather than giving up
          // on a single network blip.
          if (result.reason instanceof ApiError && result.reason.status === 404) {
            settled.push(documentId);
          }
          return;
        }

        next[documentId] = result.value;
        if (TERMINAL_STATUSES.includes(result.value.status)) {
          settled.push(documentId);
          settledCallback.current?.(result.value);
        } else if (Date.now() - (startedAt.current[documentId] ?? 0) > MAX_POLL_MS) {
          settled.push(documentId);
        }
      });

      setStatuses((current) => ({ ...current, ...next }));
      if (settled.length > 0) {
        setPending((ids) => ids.filter((id) => !settled.includes(id)));
      }
    }

    void poll();
    const timer = setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pending]);

  return { statuses, track, untrack, isPolling: pending.length > 0 };
}
