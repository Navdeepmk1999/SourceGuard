import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useIngestionStatus } from "@/hooks/useIngestionStatus";
import type { DocumentStatusRead, DocumentStatus } from "@/types";

// Only getDocumentStatus is mocked. ApiError stays real, because the hook
// branches on `instanceof ApiError` - a mocked class would make every error
// look transient and the 404 path would never be exercised.
const { getDocumentStatus } = vi.hoisted(() => ({ getDocumentStatus: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getDocumentStatus };
});

const { ApiError } = await import("@/lib/api");

const POLL_INTERVAL_MS = 2500;
const DOC_ID = "11111111-1111-1111-1111-111111111111";

function statusOf(status: DocumentStatus, overrides: Partial<DocumentStatusRead> = {}) {
  return {
    id: DOC_ID,
    filename: "report.pdf",
    status,
    chunk_count: status === "completed" ? 12 : 0,
    error_message: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } satisfies DocumentStatusRead;
}

/**
 * Flushes pending microtasks inside act().
 *
 * Used instead of RTL's `waitFor`, which drives its own timers and deadlocks
 * against vi.useFakeTimers. The clock here is fully controlled, so waiting is
 * deterministic rather than a poll-until-true.
 */
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** Advances past one poll interval and lets its promises settle. */
async function tick() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
  });
  await flush();
}

describe("useIngestionStatus", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not poll until a document is tracked", async () => {
    renderHook(() => useIngestionStatus());

    await tick();

    expect(getDocumentStatus).not.toHaveBeenCalled();
  });

  it("polls through the lifecycle and reports completion once", async () => {
    getDocumentStatus
      .mockResolvedValueOnce(statusOf("pending"))
      .mockResolvedValueOnce(statusOf("processing"))
      .mockResolvedValueOnce(statusOf("completed"));

    const onSettled = vi.fn();
    const { result } = renderHook(() => useIngestionStatus(onSettled));

    act(() => result.current.track(DOC_ID));

    // The first poll fires immediately rather than waiting a full interval,
    // so the UI is not blank for 2.5s after an upload.
    await flush();
    expect(result.current.statuses[DOC_ID]?.status).toBe("pending");
    expect(result.current.isPolling).toBe(true);

    await tick();
    expect(result.current.statuses[DOC_ID]?.status).toBe("processing");
    expect(onSettled).not.toHaveBeenCalled();

    await tick();
    expect(result.current.statuses[DOC_ID]?.status).toBe("completed");
    expect(onSettled).toHaveBeenCalledOnce();
    expect(onSettled.mock.calls[0][0]).toMatchObject({ status: "completed", chunk_count: 12 });
  });

  it("stops polling once a terminal status is seen", async () => {
    getDocumentStatus.mockResolvedValue(statusOf("completed"));

    const { result } = renderHook(() => useIngestionStatus());
    act(() => result.current.track(DOC_ID));
    await flush();

    expect(result.current.isPolling).toBe(false);
    const callsAfterSettling = getDocumentStatus.mock.calls.length;

    await tick();
    await tick();

    // No further requests: a settled document must not be polled forever.
    expect(getDocumentStatus).toHaveBeenCalledTimes(callsAfterSettling);
  });

  it("treats a failure as terminal and surfaces the reason", async () => {
    getDocumentStatus.mockResolvedValue(
      statusOf("failed", { error_message: "Unsupported document type '.exe'" })
    );

    const onSettled = vi.fn();
    const { result } = renderHook(() => useIngestionStatus(onSettled));
    act(() => result.current.track(DOC_ID));
    await flush();

    expect(onSettled).toHaveBeenCalledOnce();
    expect(onSettled.mock.calls[0][0]).toMatchObject({
      status: "failed",
      error_message: "Unsupported document type '.exe'",
    });
    expect(result.current.isPolling).toBe(false);
  });

  it("stops polling a document that was deleted while watching (404)", async () => {
    getDocumentStatus.mockRejectedValue(new ApiError("Document not found", 404));

    const onSettled = vi.fn();
    const { result } = renderHook(() => useIngestionStatus(onSettled));
    act(() => result.current.track(DOC_ID));
    await flush();

    expect(result.current.isPolling).toBe(false);
    // A deletion is not a settled ingestion - there is nothing to report.
    expect(onSettled).not.toHaveBeenCalled();
  });

  it("keeps polling through a transient error rather than giving up", async () => {
    getDocumentStatus
      .mockRejectedValueOnce(new ApiError("Bad gateway", 502))
      .mockResolvedValue(statusOf("completed"));

    const { result } = renderHook(() => useIngestionStatus());
    act(() => result.current.track(DOC_ID));
    await flush();

    // Still watching after the failure - one network blip must not orphan a
    // document that is ingesting perfectly well.
    expect(result.current.isPolling).toBe(true);

    await tick();
    expect(result.current.statuses[DOC_ID]?.status).toBe("completed");
  });

  it("stops polling a document that is untracked", async () => {
    getDocumentStatus.mockResolvedValue(statusOf("processing"));

    const { result } = renderHook(() => useIngestionStatus());
    act(() => result.current.track(DOC_ID));
    await flush();
    expect(result.current.isPolling).toBe(true);

    act(() => result.current.untrack(DOC_ID));
    const callsAtUntrack = getDocumentStatus.mock.calls.length;

    await tick();

    expect(result.current.isPolling).toBe(false);
    expect(getDocumentStatus).toHaveBeenCalledTimes(callsAtUntrack);
  });

  it("gives up after the maximum poll window", async () => {
    // A task killed by a restart never reaches a terminal status, so an
    // unbounded poll would run until the tab is closed.
    getDocumentStatus.mockResolvedValue(statusOf("processing"));

    const { result } = renderHook(() => useIngestionStatus());
    act(() => result.current.track(DOC_ID));
    await flush();
    expect(result.current.isPolling).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60 * 1000 + POLL_INTERVAL_MS);
    });

    expect(result.current.isPolling).toBe(false);
  });
});
