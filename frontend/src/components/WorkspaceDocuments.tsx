"use client";

import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { ApiError, getWorkspaceDocuments } from "@/lib/api";
import type { WorkspaceDocument } from "@/types";

export function WorkspaceDocuments({ workspaceId }: { workspaceId: string }) {
  const [documents, setDocuments] = useState<WorkspaceDocument[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setIsLoading(true);
      setError(null);
      try {
        const docs = await getWorkspaceDocuments(workspaceId);
        if (!cancelled) {
          setDocuments(docs);
        }
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof ApiError ? cause.message : "Failed to load documents.");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  if (isLoading) {
    return <p className="py-1 pl-7 text-xs text-zinc-600">Loading documents…</p>;
  }

  if (error) {
    return <p className="py-1 pl-7 text-xs text-red-400">{error}</p>;
  }

  if (documents.length === 0) {
    return <p className="py-1 pl-7 text-xs text-zinc-600">No documents yet.</p>;
  }

  return (
    <ul className="flex flex-col gap-0.5 py-1 pl-7">
      {documents.map((doc) => (
        <li
          key={doc.id}
          title={doc.filename}
          className="flex items-center gap-1.5 text-xs text-zinc-400"
        >
          <FileText className="h-3 w-3 shrink-0 text-zinc-600" />
          <span className="min-w-0 flex-1 truncate">{doc.filename}</span>
          <span className="shrink-0 text-zinc-600">{doc.total_chunks}</span>
        </li>
      ))}
    </ul>
  );
}
