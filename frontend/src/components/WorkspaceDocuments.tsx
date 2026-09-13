"use client";

import { useEffect, useState } from "react";
import { FileText, Trash2 } from "lucide-react";

import { ApiError, deleteDocument, getWorkspaceDocuments } from "@/lib/api";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { WorkspaceDocument } from "@/types";

export function WorkspaceDocuments({ workspaceId }: { workspaceId: string }) {
  const [documents, setDocuments] = useState<WorkspaceDocument[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Held as the document itself so the dialog can name the file and show how
  // many chunks the cascade will take with it.
  const [pendingDocument, setPendingDocument] = useState<WorkspaceDocument | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

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

  async function handleConfirmDelete() {
    if (!pendingDocument) {
      return;
    }
    setIsDeleting(true);
    setDeleteError(null);

    try {
      await deleteDocument(pendingDocument.id);
      // Removed locally rather than refetching: the list is already correct
      // after one deletion, and a refetch would cost a round trip to learn
      // something we know.
      setDocuments((current) => current.filter((doc) => doc.id !== pendingDocument.id));
      setPendingDocument(null);
    } catch (cause) {
      // A 404 means it is already gone - reconcile instead of reporting an
      // error for the outcome the user wanted.
      if (cause instanceof ApiError && cause.status === 404) {
        setDocuments((current) => current.filter((doc) => doc.id !== pendingDocument.id));
        setPendingDocument(null);
      } else {
        setDeleteError(
          cause instanceof ApiError ? cause.message : "Could not delete this document."
        );
      }
    } finally {
      setIsDeleting(false);
    }
  }

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
    <>
      <ul className="flex flex-col gap-0.5 py-1 pl-7">
        {documents.map((doc) => (
          <li
            key={doc.id}
            title={doc.filename}
            className="group flex items-center gap-1.5 rounded text-xs text-zinc-400"
          >
            <FileText className="h-3 w-3 shrink-0 text-zinc-600" />
            <span className="min-w-0 flex-1 truncate">{doc.filename}</span>

            <span className="shrink-0 text-zinc-600">{doc.total_chunks}</span>

            {/* Faded rather than `hidden`: a display:none button cannot be
                focused, so `hidden` would make this unreachable by keyboard
                entirely. opacity-0 keeps it in the tab order and holds its
                space, so the row does not reflow on hover. */}
            <button
              type="button"
              onClick={() => setPendingDocument(doc)}
              aria-label={`Delete document ${doc.filename}`}
              title={`Delete ${doc.filename}`}
              className="shrink-0 rounded p-0.5 text-zinc-600 opacity-0 transition-all hover:bg-red-500/10 hover:text-red-400 focus-visible:opacity-100 group-hover:opacity-100"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </li>
        ))}
      </ul>

      <ConfirmDialog
        open={pendingDocument !== null}
        title={`Delete "${pendingDocument?.filename ?? ""}"?`}
        description={
          <>
            This permanently deletes the document and its{" "}
            <strong className="text-zinc-300">
              {pendingDocument?.total_chunks ?? 0} embedded chunk
              {pendingDocument?.total_chunks === 1 ? "" : "s"}
            </strong>
            , removing it from future answers. This cannot be undone.
          </>
        }
        confirmLabel="Delete document"
        isBusy={isDeleting}
        error={deleteError}
        onConfirm={handleConfirmDelete}
        onCancel={() => {
          setPendingDocument(null);
          setDeleteError(null);
        }}
      />
    </>
  );
}
