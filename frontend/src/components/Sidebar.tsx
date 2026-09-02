"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  FolderKanban,
  Layers,
  LogOut,
  Plus,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { createClient } from "@/lib/supabase/client";
import { useWorkspaces } from "@/context/WorkspaceContext";
import { WorkspaceDocuments } from "@/components/WorkspaceDocuments";

export function Sidebar() {
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [isSigningOut, setIsSigningOut] = useState(false);
  const {
    workspaces,
    activeWorkspace,
    isLoading,
    error,
    fetchWorkspaces,
    addWorkspace,
    setActiveWorkspace,
  } = useWorkspaces();

  useEffect(() => {
    void fetchWorkspaces();
  }, [fetchWorkspaces]);

  async function handleCreateWorkspace() {
    const name = window.prompt("Name your new workspace:")?.trim();
    if (!name) {
      return;
    }
    await addWorkspace(name);
  }

  async function handleSignOut() {
    setIsSigningOut(true);
    try {
      const supabase = createClient();
      await supabase.auth.signOut();
      router.push("/login");
      router.refresh();
    } finally {
      setIsSigningOut(false);
    }
  }

  return (
    <aside
      className={cn(
        "flex h-full flex-col border-r border-zinc-800 bg-zinc-950 transition-[width] duration-200 ease-in-out",
        collapsed ? "w-16" : "w-64"
      )}
    >
      <div className="flex h-14 items-center justify-between border-b border-zinc-800 px-3">
        <div
          className={cn(
            "flex items-center gap-2 overflow-hidden",
            collapsed && "w-0 opacity-0"
          )}
        >
          <ShieldCheck className="h-5 w-5 shrink-0 text-indigo-400" />
          <span className="truncate text-sm font-semibold text-zinc-100">
            SourceGuard
          </span>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed((prev) => !prev)}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-zinc-900 hover:text-zinc-100"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <PanelLeftOpen className="h-4 w-4" />
          ) : (
            <PanelLeftClose className="h-4 w-4" />
          )}
        </button>
      </div>

      <div className="flex flex-1 flex-col gap-1 overflow-y-auto p-3">
        <button
          type="button"
          onClick={handleCreateWorkspace}
          title={collapsed ? "New Workspace" : undefined}
          className={cn(
            "flex items-center gap-2 rounded-md border border-dashed border-zinc-700 px-3 py-2 text-sm font-medium text-zinc-300 transition-colors hover:border-indigo-500 hover:bg-indigo-500/10 hover:text-indigo-300",
            collapsed && "justify-center px-0"
          )}
        >
          <Plus className="h-4 w-4 shrink-0" />
          {!collapsed && <span>New Workspace</span>}
        </button>

        <div className="mt-4">
          <div
            className={cn(
              "flex items-center gap-2 px-2 text-xs font-semibold uppercase tracking-wider text-zinc-500",
              collapsed && "justify-center px-0"
            )}
          >
            <Layers className="h-3.5 w-3.5 shrink-0" />
            {!collapsed && <span>Workspaces</span>}
          </div>

          <div className="mt-2 flex flex-col gap-0.5">
            {isLoading && !collapsed && (
              <p className="px-2 py-1.5 text-sm text-zinc-600">Loading…</p>
            )}

            {!isLoading && error && !collapsed && (
              <div className="px-2 py-1.5">
                <p className="text-sm text-red-400">{error}</p>
                <button
                  type="button"
                  onClick={() => void fetchWorkspaces()}
                  className="mt-1 text-xs font-medium text-indigo-400 transition-colors hover:text-indigo-300"
                >
                  Retry
                </button>
              </div>
            )}

            {!isLoading && !error && workspaces.length === 0 && !collapsed && (
              <p className="px-2 py-1.5 text-sm text-zinc-600">
                No workspaces yet.
              </p>
            )}

            {workspaces.length > 0 && (
              <ul className="flex flex-col gap-0.5">
                {workspaces.map((workspace) => {
                  const isActive = activeWorkspace?.id === workspace.id;
                  return (
                    <li key={workspace.id}>
                      <button
                        type="button"
                        onClick={() => setActiveWorkspace(workspace)}
                        title={collapsed ? workspace.name : undefined}
                        aria-current={isActive ? "true" : undefined}
                        className={cn(
                          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                          isActive
                            ? "bg-indigo-500/10 text-indigo-300 ring-1 ring-inset ring-indigo-500/30"
                            : "text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100",
                          collapsed && "justify-center px-0"
                        )}
                      >
                        <FolderKanban
                          className={cn(
                            "h-4 w-4 shrink-0",
                            isActive ? "text-indigo-400" : "text-zinc-500"
                          )}
                        />
                        {!collapsed && (
                          <span className="truncate">{workspace.name}</span>
                        )}
                      </button>
                      {isActive && !collapsed && (
                        <WorkspaceDocuments workspaceId={workspace.id} />
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      </div>

      <div className="border-t border-zinc-800 p-3">
        <button
          type="button"
          onClick={() => void handleSignOut()}
          disabled={isSigningOut}
          title={collapsed ? "Sign Out" : undefined}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-zinc-400 transition-colors hover:bg-red-500/10 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-50",
            collapsed && "justify-center px-0"
          )}
        >
          <LogOut className="h-4 w-4 shrink-0" />
          {!collapsed && <span>{isSigningOut ? "Signing out…" : "Sign Out"}</span>}
        </button>
      </div>
    </aside>
  );
}
