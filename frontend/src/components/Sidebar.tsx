"use client";

import { useState } from "react";
import {
  FolderKanban,
  Layers,
  Plus,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Workspace } from "@/types";

// Static placeholder until the workspaces API is wired up.
const PLACEHOLDER_WORKSPACES: Workspace[] = [];

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);

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
            {PLACEHOLDER_WORKSPACES.length === 0 ? (
              !collapsed && (
                <p className="px-2 py-1.5 text-sm text-zinc-600">
                  No workspaces yet.
                </p>
              )
            ) : (
              <ul className="flex flex-col gap-0.5">
                {PLACEHOLDER_WORKSPACES.map((workspace) => (
                  <li key={workspace.id}>
                    <button
                      type="button"
                      className={cn(
                        "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-zinc-300 transition-colors hover:bg-zinc-900 hover:text-zinc-100",
                        collapsed && "justify-center px-0"
                      )}
                    >
                      <FolderKanban className="h-4 w-4 shrink-0 text-zinc-500" />
                      {!collapsed && (
                        <span className="truncate">{workspace.name}</span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
}
