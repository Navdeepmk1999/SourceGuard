import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceDocuments } from "@/components/WorkspaceDocuments";
import type { WorkspaceDocument } from "@/types";

const { getWorkspaceDocuments, deleteDocument } = vi.hoisted(() => ({
  getWorkspaceDocuments: vi.fn(),
  deleteDocument: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getWorkspaceDocuments, deleteDocument };
});

const { ApiError } = await import("@/lib/api");

const REPORT: WorkspaceDocument = {
  id: "doc-report",
  filename: "report.pdf",
  document_type: "pdf",
  created_at: "2026-01-01T00:00:00Z",
  total_chunks: 14,
};
const NOTES: WorkspaceDocument = {
  id: "doc-notes",
  filename: "notes.txt",
  document_type: "txt",
  created_at: "2026-01-02T00:00:00Z",
  total_chunks: 2,
};

async function renderList(docs: WorkspaceDocument[] = [REPORT, NOTES]) {
  getWorkspaceDocuments.mockResolvedValue(docs);
  const user = userEvent.setup();
  render(<WorkspaceDocuments workspaceId="ws-alpha" />);
  // Wait on whatever was actually rendered, not a fixed filename.
  await screen.findByText(docs[0].filename);
  return { user };
}

/** Opens the confirmation dialog for a document and returns it. */
async function openDeleteDialog(user: ReturnType<typeof userEvent.setup>, filename: string) {
  await user.click(screen.getByRole("button", { name: `Delete document ${filename}` }));
  return screen.getByRole("alertdialog");
}

describe("WorkspaceDocuments delete flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists documents with their chunk counts", async () => {
    await renderList();

    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
  });

  it("does not delete until the confirmation is accepted", async () => {
    const { user } = await renderList();

    // Clicking the icon must open a dialog, never delete immediately.
    const dialog = await openDeleteDialog(user, "report.pdf");
    expect(deleteDocument).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(deleteDocument).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
  });

  it("names the file and its chunk count so the blast radius is visible", async () => {
    const { user } = await renderList();

    const dialog = await openDeleteDialog(user, "report.pdf");

    expect(within(dialog).getByText(/Delete "report\.pdf"\?/)).toBeInTheDocument();
    expect(within(dialog).getByText(/14 embedded chunks/)).toBeInTheDocument();
  });

  it("singularises the chunk count", async () => {
    const { user } = await renderList([{ ...NOTES, total_chunks: 1 }]);

    const dialog = await openDeleteDialog(user, "notes.txt");

    expect(within(dialog).getByText(/1 embedded chunk(?!s)/)).toBeInTheDocument();
  });

  it("removes the document from the list on success without refetching", async () => {
    const { user } = await renderList();
    deleteDocument.mockResolvedValue({ id: REPORT.id, deleted_documents: 1, deleted_chunks: 14 });
    const fetchesBefore = getWorkspaceDocuments.mock.calls.length;

    const dialog = await openDeleteDialog(user, "report.pdf");
    await user.click(within(dialog).getByRole("button", { name: "Delete document" }));

    await waitFor(() => expect(screen.queryByText("report.pdf")).not.toBeInTheDocument());
    expect(deleteDocument).toHaveBeenCalledWith(REPORT.id);
    // Sibling documents survive, and no refetch was needed.
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
    expect(getWorkspaceDocuments).toHaveBeenCalledTimes(fetchesBefore);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("treats a 404 as already deleted and reconciles the list", async () => {
    const { user } = await renderList();
    deleteDocument.mockRejectedValue(new ApiError("Document not found", 404));

    const dialog = await openDeleteDialog(user, "report.pdf");
    await user.click(within(dialog).getByRole("button", { name: "Delete document" }));

    // Deleted in another tab: the user's goal was achieved, so this is not an
    // error and the row must not linger.
    await waitFor(() => expect(screen.queryByText("report.pdf")).not.toBeInTheDocument());
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("keeps the dialog open and shows why when deletion fails", async () => {
    const { user } = await renderList();
    deleteDocument.mockRejectedValue(new ApiError("Database unavailable", 500));

    const dialog = await openDeleteDialog(user, "report.pdf");
    await user.click(within(dialog).getByRole("button", { name: "Delete document" }));

    // Dismissing here would leave the row present with no explanation.
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Database unavailable"));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
  });

  it("disables the dialog actions while the delete is in flight", async () => {
    const { user } = await renderList();
    let resolveDelete: (value: unknown) => void = () => {};
    deleteDocument.mockReturnValue(new Promise((resolve) => (resolveDelete = resolve)));

    const dialog = await openDeleteDialog(user, "report.pdf");
    await user.click(within(dialog).getByRole("button", { name: "Delete document" }));

    // Guards against a double-submit while the request is outstanding.
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled());

    resolveDelete({ id: REPORT.id, deleted_documents: 1, deleted_chunks: 14 });
    await waitFor(() => expect(screen.queryByText("report.pdf")).not.toBeInTheDocument());
  });
});
