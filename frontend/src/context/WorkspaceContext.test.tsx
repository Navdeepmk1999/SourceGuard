import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { WorkspaceProvider, useWorkspaces } from "@/context/WorkspaceContext";
import type { Workspace } from "@/types";

// ApiError stays real: removeWorkspace branches on `instanceof ApiError` and
// on `.status === 404`, so a mocked class would collapse those paths.
const { getWorkspaces, createWorkspace, deleteWorkspace } = vi.hoisted(() => ({
  getWorkspaces: vi.fn(),
  createWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getWorkspaces, createWorkspace, deleteWorkspace };
});

const { ApiError } = await import("@/lib/api");

const ALPHA: Workspace = { id: "ws-alpha", name: "Alpha", created_at: "2026-01-01T00:00:00Z" };
const BETA: Workspace = { id: "ws-beta", name: "Beta", created_at: "2026-01-02T00:00:00Z" };

function wrapper({ children }: { children: ReactNode }) {
  return <WorkspaceProvider>{children}</WorkspaceProvider>;
}

async function renderLoaded(workspaces: Workspace[] = [ALPHA, BETA]) {
  getWorkspaces.mockResolvedValue(workspaces);
  const { result } = renderHook(() => useWorkspaces(), { wrapper });
  await act(async () => {
    await result.current.fetchWorkspaces();
  });
  return result;
}

describe("WorkspaceContext.removeWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("removes the workspace from local state without refetching", async () => {
    const result = await renderLoaded();
    deleteWorkspace.mockResolvedValue({
      id: ALPHA.id,
      deleted_documents: 3,
      deleted_chunks: 42,
    });

    const callsBefore = getWorkspaces.mock.calls.length;
    let returned;
    await act(async () => {
      returned = await result.current.removeWorkspace(ALPHA.id);
    });

    expect(deleteWorkspace).toHaveBeenCalledWith(ALPHA.id);
    expect(result.current.workspaces.map((w) => w.id)).toEqual([BETA.id]);
    // The list is already correct after one deletion; a refetch would cost a
    // round trip to learn something we know.
    expect(getWorkspaces).toHaveBeenCalledTimes(callsBefore);
    expect(returned).toMatchObject({ deleted_documents: 3, deleted_chunks: 42 });
  });

  it("clears the active selection when the deleted workspace was active", async () => {
    const result = await renderLoaded();
    act(() => result.current.setActiveWorkspace(ALPHA));
    expect(result.current.activeWorkspace?.id).toBe(ALPHA.id);

    deleteWorkspace.mockResolvedValue({ id: ALPHA.id, deleted_documents: 0, deleted_chunks: 0 });
    await act(async () => {
      await result.current.removeWorkspace(ALPHA.id);
    });

    // Leaving it selected would point the chat panel and document list at a
    // workspace the server no longer has.
    expect(result.current.activeWorkspace).toBeNull();
  });

  it("keeps an unrelated active selection intact", async () => {
    const result = await renderLoaded();
    act(() => result.current.setActiveWorkspace(BETA));

    deleteWorkspace.mockResolvedValue({ id: ALPHA.id, deleted_documents: 0, deleted_chunks: 0 });
    await act(async () => {
      await result.current.removeWorkspace(ALPHA.id);
    });

    expect(result.current.activeWorkspace?.id).toBe(BETA.id);
  });

  it("reconciles a 404 as success rather than reporting an error", async () => {
    // Already deleted in another tab. Reporting a failure for the outcome the
    // user wanted would be wrong, and would leave a phantom row in the list.
    const result = await renderLoaded();
    act(() => result.current.setActiveWorkspace(ALPHA));
    deleteWorkspace.mockRejectedValue(new ApiError("Workspace not found", 404));

    let returned;
    await act(async () => {
      returned = await result.current.removeWorkspace(ALPHA.id);
    });

    expect(returned).not.toBeNull();
    expect(result.current.workspaces.map((w) => w.id)).toEqual([BETA.id]);
    expect(result.current.activeWorkspace).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("keeps the workspace on a real failure and returns null", async () => {
    const result = await renderLoaded();
    deleteWorkspace.mockRejectedValue(new ApiError("Internal server error", 500));

    let returned;
    await act(async () => {
      returned = await result.current.removeWorkspace(ALPHA.id);
    });

    expect(returned).toBeNull();
    // Removing it locally would hide a workspace that still exists.
    expect(result.current.workspaces.map((w) => w.id)).toEqual([ALPHA.id, BETA.id]);
    expect(result.current.error).toBe("Internal server error");
  });
});
