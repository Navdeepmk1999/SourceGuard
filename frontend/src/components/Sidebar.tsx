"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  FolderKanban,
  Layers,
  LogOut,
  Plus,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { createClient } from "@/lib/supabase/client";
import { useWorkspaces } from "@/context/WorkspaceContext";
import type { Workspace } from "@/types";
import { WorkspaceDocuments } from "@/components/WorkspaceDocuments";
import { ConfirmDialog } from "@/components/ConfirmDialog";

export function Sidebar() {
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [isSigningOut, setIsSigningOut] = useState(false);

  // The workspace awaiting confirmation. Holding the object rather than an
  // id keeps the dialog copy specific ("Delete \"Acme Reports\"?") without a
  // second lookup after the list has already changed.
  const [pendingWorkspace, setPendingWorkspace] = useState<Workspace | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function handleConfirmDelete() {
    if (!pendingWorkspace) {
      return;
    }
    setIsDeleting(true);
    setDeleteError(null);

    const result = await removeWorkspace(pendingWorkspace.id);

    setIsDeleting(false);
    if (result === null) {
      // Keep the dialog open so the failure is attached to the action that
      // caused it, rather than dismissing and leaving the row still present
      // with no explanation.
      setDeleteError("Could not delete this workspace. Please try again.");
      return;
    }
    setPendingWorkspace(null);
  }
  const {
    workspaces,
    activeWorkspace,
    isLoading,
    error,
    fetchWorkspaces,
    addWorkspace,
    setActiveWorkspace,
    removeWorkspace,
    clearWorkspaces,
  } = useWorkspaces();

  // No mount-time fetch here: WorkspaceProvider now loads workspaces in
  // response to Supabase's INITIAL_SESSION/SIGNED_IN events, so fetching here
  // too would duplicate every request and could race a sign-out.

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
      // Belt and braces: onAuthStateChange("SIGNED_OUT") already wipes this,
      // but that fires asynchronously and the redirect below could lose the
      // race on a slow render. Clearing here means no frame can paint the
      // previous user's workspaces.
      clearWorkspaces();
      router.push("/login");
      router.refresh();
    } finally {
      setIsSigningOut(false);
    }
  }

  return (
    <>
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
                      {/* The row is a flex container rather than one button:
                          the delete control cannot be nested inside the
                          select button, since nested buttons are invalid
                          HTML and swallow the inner click. */}
                      <div
                        className={cn(
                          "group flex items-center rounded-md transition-colors",
                          isActive
                            ? "bg-indigo-500/10 ring-1 ring-inset ring-indigo-500/30"
                            : "hover:bg-zinc-900"
                        )}
                      >
                        <button
                          type="button"
                          onClick={() => setActiveWorkspace(workspace)}
                          title={collapsed ? workspace.name : undefined}
                          aria-current={isActive ? "true" : undefined}
                          className={cn(
                            "flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left text-sm transition-colors",
                            isActive
                              ? "text-indigo-300"
                              : "text-zinc-300 group-hover:text-zinc-100",
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

                        {!collapsed && (
                          <button
                            type="button"
                            onClick={() => setPendingWorkspace(workspace)}
                            aria-label={`Delete workspace ${workspace.name}`}
                            title={`Delete ${workspace.name}`}
                            /* Revealed on hover to keep the list quiet, but
                               always reachable by keyboard via focus-visible
                               - a control that only exists on hover is
                               invisible to keyboard and touch users. */
                            className="mr-1 shrink-0 rounded p-1 text-zinc-600 opacity-0 transition-all hover:bg-red-500/10 hover:text-red-400 focus-visible:opacity-100 group-hover:opacity-100"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
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

    <ConfirmDialog
      open={pendingWorkspace !== null}
      title={`Delete "${pendingWorkspace?.name ?? ""}"?`}
      description={
        <>
          This permanently deletes the workspace and{" "}
          <strong className="text-zinc-300">every document, embedding, and chat
          message inside it</strong>. This cannot be undone.
        </>
      }
      confirmLabel="Delete workspace"
      isBusy={isDeleting}
      error={deleteError}
      onConfirm={handleConfirmDelete}
      onCancel={() => {
        setPendingWorkspace(null);
        setDeleteError(null);
      }}
    />
    </>
  );
}
