import { beforeEach, describe, expect, it, vi } from "vitest";

// A controllable Supabase stub: every API call resolves its bearer through
// this, so these tests assert the JWT actually reaches fetch.
const { getSession } = vi.hoisted(() => ({ getSession: vi.fn() }));

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession } }),
}));

const { createWorkspace, deleteWorkspace, getWorkspaces, uploadDocument, getDocumentStatus } =
  await import("@/lib/api");

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function headersOf(call: number = 0): Record<string, string> {
  const init = vi.mocked(globalThis.fetch).mock.calls[call][1] as RequestInit;
  return init.headers as Record<string, string>;
}

describe("API client JWT injection", () => {
  beforeEach(() => {
    // A fresh Response per call: a body can only be read once, so reusing a
    // single instance fails the second request with "Body is unusable".
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => jsonResponse([])));
    getSession.mockResolvedValue({ data: { session: { access_token: "jwt-abc123" } } });
  });

  it.each([
    ["GET", () => getWorkspaces()],
    ["POST", () => createWorkspace("Acme")],
    ["DELETE", () => deleteWorkspace("ws-1")],
    ["GET status", () => getDocumentStatus("doc-1")],
  ])("attaches the bearer token on %s", async (_label, call) => {
    await call();

    // Every RLS policy keys off the user id inside this token. A request
    // without it is not an auth error the user sees - it is a query evaluated
    // against no tenant at all.
    expect(headersOf().Authorization).toBe("Bearer jwt-abc123");
  });

  it("attaches the bearer to multipart uploads without forcing a Content-Type", async () => {
    const file = new File(["hello"], "note.txt", { type: "text/plain" });
    await uploadDocument("ws-1", file);

    const headers = headersOf();
    expect(headers.Authorization).toBe("Bearer jwt-abc123");
    // fetch must set multipart/form-data itself so the boundary is included.
    expect(headers["Content-Type"]).toBeUndefined();
  });

  it("sends no Authorization header when there is no session", async () => {
    getSession.mockResolvedValue({ data: { session: null } });

    await getWorkspaces();

    // Fails closed at the backend rather than sending "Bearer undefined",
    // which would read as a malformed token instead of an absent one.
    expect(headersOf().Authorization).toBeUndefined();
  });

  it("refreshes the token per request rather than caching it", async () => {
    getSession
      .mockResolvedValueOnce({ data: { session: { access_token: "first" } } })
      .mockResolvedValueOnce({ data: { session: { access_token: "second" } } });

    await getWorkspaces();
    await getWorkspaces();

    // getSession refreshes an expired token, so reading it per request is
    // what keeps a long-lived tab from sending a stale JWT.
    expect(headersOf(0).Authorization).toBe("Bearer first");
    expect(headersOf(1).Authorization).toBe("Bearer second");
  });
});
