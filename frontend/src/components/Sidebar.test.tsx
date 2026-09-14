import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "@/components/Sidebar";
import { WorkspaceProvider } from "@/context/WorkspaceContext";
import type { Workspace } from "@/types";

const { getWorkspaces, deleteWorkspace, getWorkspaceDocuments } = vi.hoisted(() => ({
  getWorkspaces: vi.fn(),
  deleteWorkspace: vi.fn(),
  getWorkspaceDocuments: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getWorkspaces, deleteWorkspace, getWorkspaceDocuments };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: {
      signOut: vi.fn().mockResolvedValue({ error: null }),
      // Sidebar no longer fetches on mount - WorkspaceProvider loads
      // workspaces in response to this event, so the stub has to deliver one
      // or the list never populates.
      onAuthStateChange: (handler: (event: string, session: unknown) => void) => {
        handler("INITIAL_SESSION", { access_token: "jwt", user: { id: "user-1" } });
        return { data: { subscription: { unsubscribe: vi.fn() } } };
      },
    },
  }),
}));

const { ApiError } = await import("@/lib/api");

const ALPHA: Workspace = { id: "ws-alpha", name: "Alpha", created_at: "2026-01-01T00:00:00Z" };
const BETA: Workspace = { id: "ws-beta", name: "Beta", created_at: "2026-01-02T00:00:00Z" };

/**
 * Renders against the real WorkspaceProvider rather than a stubbed context,
 * so these cover the whole path: click -> dialog -> API -> state -> re-render.
 */
async function renderSidebar(workspaces: Workspace[] = [ALPHA, BETA]) {
  getWorkspaces.mockResolvedValue(workspaces);
  getWorkspaceDocuments.mockResolvedValue([]);
  const user = userEvent.setup();

  render(
    <WorkspaceProvider>
      <Sidebar />
    </WorkspaceProvider>
  );
  await screen.findByRole("button", { name: "Alpha" });
  return { user };
}

async function openDeleteDialog(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("button", { name: `Delete workspace ${name}` }));
  return screen.getByRole("alertdialog");
}

describe("Sidebar delete flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders a delete control for each workspace", async () => {
    await renderSidebar();

    expect(screen.getByRole("button", { name: "Delete workspace Alpha" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete workspace Beta" })).toBeInTheDocument();
  });

  it("selects a workspace without deleting it", async () => {
    // The select and delete controls are siblings, not nested buttons - a
    // click on the row must never reach the destructive action.
    const { user } = await renderSidebar();

    await user.click(screen.getByRole("button", { name: "Alpha" }));

    expect(deleteWorkspace).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("requires confirmation before deleting", async () => {
    const { user } = await renderSidebar();

    const dialog = await openDeleteDialog(user, "Alpha");
    expect(within(dialog).getByText(/Delete "Alpha"\?/)).toBeInTheDocument();
    expect(within(dialog).getByText(/every document, embedding, and chat/)).toBeInTheDocument();
    expect(deleteWorkspace).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(deleteWorkspace).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Alpha" })).toBeInTheDocument();
  });

  it("removes the workspace from the list once confirmed", async () => {
    const { user } = await renderSidebar();
    deleteWorkspace.mockResolvedValue({ id: ALPHA.id, deleted_documents: 2, deleted_chunks: 30 });

    const dialog = await openDeleteDialog(user, "Alpha");
    await user.click(within(dialog).getByRole("button", { name: "Delete workspace" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Delete workspace Alpha" })).not.toBeInTheDocument()
    );
    expect(deleteWorkspace).toHaveBeenCalledWith(ALPHA.id);
    // The sibling workspace is untouched.
    expect(screen.getByRole("button", { name: "Beta" })).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("clears the selection when the active workspace is deleted", async () => {
    const { user } = await renderSidebar();
    getWorkspaceDocuments.mockResolvedValue([
      {
        id: "doc-1",
        filename: "report.pdf",
        document_type: "pdf" as const,
        created_at: "2026-01-01T00:00:00Z",
        total_chunks: 4,
      },
    ]);

    // Selecting Alpha mounts its document list.
    await user.click(screen.getByRole("button", { name: "Alpha" }));
    await screen.findByText("report.pdf");

    deleteWorkspace.mockResolvedValue({ id: ALPHA.id, deleted_documents: 1, deleted_chunks: 4 });
    const dialog = await openDeleteDialog(user, "Alpha");
    await user.click(within(dialog).getByRole("button", { name: "Delete workspace" }));

    // The document list must unmount with it, rather than staying pointed at
    // a workspace the server no longer has.
    await waitFor(() => expect(screen.queryByText("report.pdf")).not.toBeInTheDocument());
  });

  it("keeps the workspace and explains the failure when deletion fails", async () => {
    const { user } = await renderSidebar();
    deleteWorkspace.mockRejectedValue(new ApiError("Internal server error", 500));

    const dialog = await openDeleteDialog(user, "Alpha");
    await user.click(within(dialog).getByRole("button", { name: "Delete workspace" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    // Still listed: removing it locally would hide a workspace that exists.
    expect(screen.getByRole("button", { name: "Delete workspace Alpha" })).toBeInTheDocument();
  });

  it("reconciles a 404 by dropping the row", async () => {
    const { user } = await renderSidebar();
    deleteWorkspace.mockRejectedValue(new ApiError("Workspace not found", 404));

    const dialog = await openDeleteDialog(user, "Alpha");
    await user.click(within(dialog).getByRole("button", { name: "Delete workspace" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Delete workspace Alpha" })).not.toBeInTheDocument()
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});
