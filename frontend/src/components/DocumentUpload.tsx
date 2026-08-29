"use client";

import { useRef, useState, type ChangeEvent } from "react";
import { Loader2, Upload } from "lucide-react";
import { useWorkspaces } from "@/context/WorkspaceContext";
import { ApiError, uploadDocument } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Toast {
  id: number;
  variant: "success" | "error";
  message: string;
}

const TOAST_DURATION_MS = 5000;

export function DocumentUpload() {
  const { activeWorkspace } = useWorkspaces();
  const [isUploading, setIsUploading] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const nextToastId = useRef(0);

  function pushToast(variant: Toast["variant"], message: string) {
    const id = nextToastId.current++;
    setToasts((current) => [...current, { id, variant, message }]);
    setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, TOAST_DURATION_MS);
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Reset so selecting the same file again still fires onChange.
    event.target.value = "";
    if (!file || !activeWorkspace) {
      return;
    }

    setIsUploading(true);
    try {
      const result = await uploadDocument(activeWorkspace.id, file);
      const summary = result.documents[0];
      pushToast(
        "success",
        summary
          ? `"${summary.filename}" ingested — ${summary.total_chunks} chunk${
              summary.total_chunks === 1 ? "" : "s"
            }.`
          : `"${file.name}" uploaded.`
      );
    } catch (cause) {
      pushToast(
        "error",
        cause instanceof ApiError
          ? cause.message
          : "An unexpected error occurred while uploading."
      );
    } finally {
      setIsUploading(false);
    }
  }

  const disabled = !activeWorkspace || isUploading;

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.txt"
        className="hidden"
        onChange={handleFileChange}
        disabled={disabled}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        title={
          activeWorkspace
            ? undefined
            : "Select a workspace before uploading a document"
        }
        className={cn(
          "flex items-center gap-2 rounded-md bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-indigo-400",
          disabled &&
            "cursor-not-allowed bg-zinc-800 text-zinc-500 hover:bg-zinc-800"
        )}
      >
        {isUploading ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Upload className="h-4 w-4" />
        )}
        {isUploading ? "Uploading…" : "Upload Document"}
      </button>

      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role="status"
            className={cn(
              "pointer-events-auto rounded-md border px-3 py-2 text-sm shadow-lg backdrop-blur-sm",
              toast.variant === "success"
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                : "border-red-500/30 bg-red-500/10 text-red-300"
            )}
          >
            {toast.message}
          </div>
        ))}
      </div>
    </>
  );
}
