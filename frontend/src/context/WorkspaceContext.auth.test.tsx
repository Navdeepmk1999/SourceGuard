import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { WorkspaceProvider, useWorkspaces } from "@/context/WorkspaceContext";
import type { Workspace } from "@/types";

const { getWorkspaces, onAuthStateChange, unsubscribe } = vi.hoisted(() => ({
  getWorkspaces: vi.fn(),
  onAuthStateChange: vi.fn(),
  unsubscribe: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getWorkspaces };
});

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { onAuthStateChange } }),
}));

type AuthHandler = (event: string, session: unknown) => void;
let emit: AuthHandler;

const ALPHA: Workspace = { id: "ws-alpha", name: "Alpha", created_at: "2026-01-01T00:00:00Z" };
const SESSION = { access_token: "jwt-abc", user: { id: "user-1" } };

function wrapper({ children }: { children: ReactNode }) {
  return <WorkspaceProvider>{children}</WorkspaceProvider>;
}

describe("WorkspaceContext auth lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getWorkspaces.mockResolvedValue([ALPHA]);
    onAuthStateChange.mockImplementation((handler: AuthHandler) => {
      emit = handler;
      return { data: { subscription: { unsubscribe } } };
    });
  });

  it("subscribes to auth changes on mount and unsubscribes on unmount", () => {
    const { unmount } = renderHook(() => useWorkspaces(), { wrapper });

    expect(onAuthStateChange).toHaveBeenCalledOnce();

    unmount();
    // A leaked subscription would keep writing into an unmounted tree.
    expect(unsubscribe).toHaveBeenCalledOnce();
  });

  it("loads the signed-in user's workspaces on INITIAL_SESSION", async () => {
    const { result } = renderHook(() => useWorkspaces(), { wrapper });

    await act(async () => {
      emit("INITIAL_SESSION", SESSION);
    });

    await waitFor(() => expect(result.current.workspaces).toHaveLength(1));
    expect(getWorkspaces).toHaveBeenCalled();
  });

  it("loads workspaces on SIGNED_IN", async () => {
    const { result } = renderHook(() => useWorkspaces(), { wrapper });

    await act(async () => {
      emit("SIGNED_IN", SESSION);
    });

    await waitFor(() => expect(result.current.workspaces.map((w) => w.id)).toEqual([ALPHA.id]));
  });

  it("wipes every trace of the previous user on SIGNED_OUT", async () => {
    const { result } = renderHook(() => useWorkspaces(), { wrapper });

    await act(async () => {
      emit("SIGNED_IN", SESSION);
    });
    await waitFor(() => expect(result.current.workspaces).toHaveLength(1));
    act(() => result.current.setActiveWorkspace(ALPHA));

    await act(async () => {
      emit("SIGNED_OUT", null);
    });

    // The next person at this browser must not see the previous user's
    // dashboard, even for a single frame.
    expect(result.current.workspaces).toEqual([]);
    expect(result.current.activeWorkspace).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });

  it("does not fetch when a sign-in event carries no session", async () => {
    const { result } = renderHook(() => useWorkspaces(), { wrapper });

    await act(async () => {
      emit("INITIAL_SESSION", null);
    });

    // An anonymous visitor must not trigger an authenticated request.
    expect(getWorkspaces).not.toHaveBeenCalled();
    expect(result.current.workspaces).toEqual([]);
  });

  it("ignores TOKEN_REFRESHED, which does not change identity", async () => {
    const { result } = renderHook(() => useWorkspaces(), { wrapper });

    await act(async () => {
      emit("SIGNED_IN", SESSION);
    });
    await waitFor(() => expect(result.current.workspaces).toHaveLength(1));
    const callsAfterSignIn = getWorkspaces.mock.calls.length;

    await act(async () => {
      emit("TOKEN_REFRESHED", SESSION);
    });

    // Refetching on every hourly refresh would be pure waste.
    expect(getWorkspaces).toHaveBeenCalledTimes(callsAfterSignIn);
    expect(result.current.workspaces).toHaveLength(1);
  });

  it("clears state when the account disappears out from under the tab", async () => {
    const { result } = renderHook(() => useWorkspaces(), { wrapper });

    await act(async () => {
      emit("SIGNED_IN", SESSION);
    });
    await waitFor(() => expect(result.current.workspaces).toHaveLength(1));

    await act(async () => {
      emit("USER_UPDATED", null);
    });

    expect(result.current.workspaces).toEqual([]);
  });

  it("swaps datasets cleanly when a different user signs in", async () => {
    const { result } = renderHook(() => useWorkspaces(), { wrapper });

    await act(async () => {
      emit("SIGNED_IN", SESSION);
    });
    await waitFor(() => expect(result.current.workspaces.map((w) => w.id)).toEqual([ALPHA.id]));

    const BETA: Workspace = { id: "ws-beta", name: "Beta", created_at: "2026-01-02T00:00:00Z" };
    getWorkspaces.mockResolvedValue([BETA]);

    await act(async () => {
      emit("SIGNED_OUT", null);
    });
    expect(result.current.workspaces).toEqual([]);

    await act(async () => {
      emit("SIGNED_IN", { ...SESSION, user: { id: "user-2" } });
    });

    // No trace of user-1's workspaces survives into user-2's session.
    await waitFor(() => expect(result.current.workspaces.map((w) => w.id)).toEqual([BETA.id]));
  });
});
