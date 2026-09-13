"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ApiError, createWorkspace, deleteWorkspace, getWorkspaces } from "@/lib/api";
import type { DeletionResult, Workspace } from "@/types";

interface WorkspaceContextValue {
  workspaces: Workspace[];
  activeWorkspace: Workspace | null;
  isLoading: boolean;
  error: string | null;
  fetchWorkspaces: () => Promise<void>;
  /** Returns the created workspace, or `null` if the request failed. */
  addWorkspace: (name: string) => Promise<Workspace | null>;
  /**
   * Deletes a workspace and everything beneath it. Returns the cascade
   * counts, or `null` if the request failed.
   */
  removeWorkspace: (workspaceId: string) => Promise<DeletionResult | null>;
  setActiveWorkspace: (workspace: Workspace | null) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

function toMessage(cause: unknown): string {
  if (cause instanceof ApiError) {
    return cause.message;
  }
  return "An unexpected error occurred.";
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<Workspace | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchWorkspaces = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const next = await getWorkspaces();
      setWorkspaces(next);
      // Drop the active selection if it no longer exists server-side.
      setActiveWorkspace((current) =>
        current && next.some((workspace) => workspace.id === current.id)
          ? current
          : null
      );
    } catch (cause) {
      setError(toMessage(cause));
    } finally {
      setIsLoading(false);
    }
  }, []);

  const addWorkspace = useCallback(async (name: string) => {
    setError(null);
    try {
      const created = await createWorkspace(name);
      setWorkspaces((current) => [...current, created]);
      setActiveWorkspace(created);
      return created;
    } catch (cause) {
      setError(toMessage(cause));
      return null;
    }
  }, []);

  const removeWorkspace = useCallback(async (workspaceId: string) => {
    setError(null);
    try {
      const result = await deleteWorkspace(workspaceId);

      setWorkspaces((current) => current.filter((workspace) => workspace.id !== workspaceId));
      // Clear the selection if the deleted workspace was the active one -
      // leaving it selected would leave the chat panel and document list
      // pointed at a workspace the server no longer has.
      setActiveWorkspace((current) => (current?.id === workspaceId ? null : current));

      return result;
    } catch (cause) {
      // A 404 means it is already gone (deleted in another tab). Reconcile
      // local state to match rather than reporting an error for something
      // the user was trying to achieve anyway.
      if (cause instanceof ApiError && cause.status === 404) {
        setWorkspaces((current) => current.filter((workspace) => workspace.id !== workspaceId));
        setActiveWorkspace((current) => (current?.id === workspaceId ? null : current));
        return { id: workspaceId, deleted_documents: 0, deleted_chunks: 0 };
      }
      setError(toMessage(cause));
      return null;
    }
  }, []);

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      workspaces,
      activeWorkspace,
      isLoading,
      error,
      fetchWorkspaces,
      addWorkspace,
      removeWorkspace,
      setActiveWorkspace,
    }),
    [
      workspaces,
      activeWorkspace,
      isLoading,
      error,
      fetchWorkspaces,
      addWorkspace,
      removeWorkspace,
    ]
  );

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspaces(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext);
  if (context === null) {
    throw new Error("useWorkspaces must be used within a WorkspaceProvider.");
  }
  return context;
}
