"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ApiError, createWorkspace, getWorkspaces } from "@/lib/api";
import type { Workspace } from "@/types";

interface WorkspaceContextValue {
  workspaces: Workspace[];
  activeWorkspace: Workspace | null;
  isLoading: boolean;
  error: string | null;
  fetchWorkspaces: () => Promise<void>;
  /** Returns the created workspace, or `null` if the request failed. */
  addWorkspace: (name: string) => Promise<Workspace | null>;
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

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      workspaces,
      activeWorkspace,
      isLoading,
      error,
      fetchWorkspaces,
      addWorkspace,
      setActiveWorkspace,
    }),
    [workspaces, activeWorkspace, isLoading, error, fetchWorkspaces, addWorkspace]
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
