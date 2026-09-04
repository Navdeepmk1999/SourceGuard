"use client";

import { ChatPanel } from "@/components/ChatPanel";
import { DocumentUpload } from "@/components/DocumentUpload";
import { useWorkspaces } from "@/context/WorkspaceContext";

export default function Home() {
  const { activeWorkspace } = useWorkspaces();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-800 px-6">
        <div>
          <h1 className="text-sm font-semibold text-zinc-100">
            {activeWorkspace?.name ?? "Dashboard"}
          </h1>
          <p className="text-xs text-zinc-500">
            {activeWorkspace ? "Active workspace" : "No workspace selected"}
          </p>
        </div>
        <DocumentUpload />
      </header>

      {/*
        Keyed by workspace id: switching workspaces remounts ChatPanel, which
        clears the chat thread and the active sessionId. A conversation is
        scoped to one workspace server-side (the backend 404s a session_id
        used against a different workspace), so carrying either across a
        switch would be wrong.
      */}
      <ChatPanel key={activeWorkspace?.id ?? "no-workspace"} workspace={activeWorkspace} />
    </div>
  );
}
