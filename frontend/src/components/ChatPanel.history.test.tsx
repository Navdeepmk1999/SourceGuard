import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatPanel } from "@/components/ChatPanel";
import type { Workspace, WorkspaceHistory } from "@/types";

const { getWorkspaceHistory, streamQuery } = vi.hoisted(() => ({
  getWorkspaceHistory: vi.fn(),
  streamQuery: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, getWorkspaceHistory, streamQuery };
});

const { ApiError } = await import("@/lib/api");

const WORKSPACE: Workspace = {
  id: "ws-alpha",
  name: "Alpha",
  created_at: "2026-01-01T00:00:00Z",
};

function history(overrides: Partial<WorkspaceHistory> = {}): WorkspaceHistory {
  return {
    workspace_id: WORKSPACE.id,
    session_id: "session-1",
    messages: [
      {
        id: "m1",
        role: "user",
        content: "What was Q3 revenue?",
        created_at: "2026-01-01T00:00:00Z",
        claims: null,
        overall_score: null,
        is_fully_supported: null,
      },
      {
        id: "m2",
        role: "assistant",
        content: "Third-quarter figures are summarised below.",
        created_at: "2026-01-01T00:00:01Z",
        claims: [
          { claim: "Revenue reached 4.2M.", label: "entailed", score: 0.91, supporting_chunk_index: 0 },
          { claim: "Growth was 12%.", label: "insufficient_evidence", score: 0.1, supporting_chunk_index: null },
        ],
        overall_score: 0.505,
        is_fully_supported: false,
      },
    ],
    ...overrides,
  };
}

describe("ChatPanel history restoration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    streamQuery.mockImplementation(async function* () {});
  });

  it("replays the previous conversation instead of a blank screen", async () => {
    getWorkspaceHistory.mockResolvedValue(history());
    render(<ChatPanel workspace={WORKSPACE} />);

    expect(await screen.findByText("What was Q3 revenue?")).toBeInTheDocument();
    expect(screen.getByText(/Third-quarter figures/)).toBeInTheDocument();
    expect(getWorkspaceHistory).toHaveBeenCalledWith(WORKSPACE.id);
  });

  it("shows a restoring state rather than flashing the empty prompt", async () => {
    let resolve: (value: WorkspaceHistory) => void = () => {};
    getWorkspaceHistory.mockReturnValue(new Promise((r) => (resolve = r)));

    render(<ChatPanel workspace={WORKSPACE} />);

    expect(screen.getByText(/restoring conversation/i)).toBeInTheDocument();
    // The "ask a question" empty state would otherwise appear for a moment
    // and then be replaced, which reads as data loss.
    expect(screen.queryByText(/ask a question about your documents/i)).not.toBeInTheDocument();

    resolve(history());
    await waitFor(() => expect(screen.getByText(/Third-quarter figures/)).toBeInTheDocument());
  });

  it("shows the empty state for a workspace that has never been used", async () => {
    getWorkspaceHistory.mockResolvedValue(
      history({ session_id: null, messages: [] })
    );

    render(<ChatPanel workspace={WORKSPACE} />);

    expect(await screen.findByText(/ask a question about your documents/i)).toBeInTheDocument();
  });

  it("continues the restored thread rather than starting a new session", async () => {
    getWorkspaceHistory.mockResolvedValue(history({ session_id: "session-42" }));
    const user = (await import("@testing-library/user-event")).default.setup();

    render(<ChatPanel workspace={WORKSPACE} />);
    await screen.findByText(/Third-quarter figures/);

    await user.type(screen.getByRole("textbox"), "And Q4?");
    await user.keyboard("{Enter}");

    // The restored session id must be sent back, or the model answers with no
    // memory of the exchange the user can see on screen.
    await waitFor(() =>
      expect(streamQuery).toHaveBeenCalledWith(WORKSPACE.id, "And Q4?", "session-42")
    );
  });

  it("stays usable when history cannot be loaded", async () => {
    getWorkspaceHistory.mockRejectedValue(new ApiError("Database unavailable", 500));

    render(<ChatPanel workspace={WORKSPACE} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Database unavailable");
    // A failed restore must not block asking a new question.
    expect(alert).toHaveTextContent(/still ask a new question/i);
    expect(screen.getByRole("textbox")).toBeEnabled();
  });

  it("does not fetch history when no workspace is selected", () => {
    render(<ChatPanel workspace={null} />);
    expect(getWorkspaceHistory).not.toHaveBeenCalled();
  });

  it("restores the audit log, not just the answer text", async () => {
    getWorkspaceHistory.mockResolvedValue(history());

    render(<ChatPanel workspace={WORKSPACE} />);
    await screen.findByText(/Third-quarter figures/);

    // The audit log is the product. An answer replayed without its verdicts
    // reads as unverified, which is a stronger and wronger claim than
    // "verdicts were not stored".
    expect(await screen.findByText("Growth was 12%.")).toBeInTheDocument();
    expect(screen.getByText(/insufficient evidence/i)).toBeInTheDocument();
  });

  it("says so when an old answer has no stored verdicts", async () => {
    getWorkspaceHistory.mockResolvedValue(
      history({
        messages: [
          {
            id: "m9",
            role: "assistant",
            content: "Legacy answer.",
            created_at: "2026-01-01T00:00:00Z",
            claims: null,
            overall_score: null,
            is_fully_supported: null,
          },
        ],
      })
    );

    render(<ChatPanel workspace={WORKSPACE} />);
    await screen.findByText("Legacy answer.");

    // Must not fall back to the "results will appear in real time" copy,
    // which implies verification is merely pending rather than absent.
    expect(await screen.findByText(/weren't recorded for this answer/i)).toBeInTheDocument();
    expect(screen.queryByText(/appear here in real time/i)).not.toBeInTheDocument();
  });

  it("does not present an unverified answer as a clean one", async () => {
    getWorkspaceHistory.mockResolvedValue(
      history({
        messages: [
          {
            id: "m10",
            role: "assistant",
            content: "Legacy answer.",
            created_at: "2026-01-01T00:00:00Z",
            claims: null,
            overall_score: null,
            is_fully_supported: null,
          },
        ],
      })
    );

    render(<ChatPanel workspace={WORKSPACE} />);
    await screen.findByText("Legacy answer.");

    // null must not render the "fully supported" badge that [] would.
    expect(screen.queryByText(/fully supported/i)).not.toBeInTheDocument();
  });
});
