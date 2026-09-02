import { createClient } from "@/lib/supabase/client";
import type {
  ClaimVerification,
  DocumentUploadResponse,
  EntailmentLabel,
  Workspace,
  WorkspaceDocument,
} from "@/types";

// Already includes the `/api/v1` prefix (see frontend/.env.local), so route
// paths below are appended bare: `/workspaces` -> `<base>/api/v1/workspaces`.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL;

/**
 * Fetches the active Supabase session and returns an `Authorization: Bearer
 * <token>` header, or `{}` if there's no session - the backend's own
 * `get_current_user` (app/api/deps.py) is what actually enforces auth; a
 * missing header there is a normal 401, not something to special-case here.
 */
async function getAuthHeaders(): Promise<Record<string, string>> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {};
}

/** `status === 0` means the request never reached the server (network/CORS). */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number, options?: ErrorOptions) {
    super(message, options);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Pulls a human-readable message out of a FastAPI error body, which is either
 * `{"detail": "..."}` (our explicit HTTPExceptions) or `{"detail": [{msg, loc}]}`
 * (Pydantic 422 validation failures).
 */
function extractDetail(body: unknown): string | null {
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return null;
  }

  const { detail } = body as { detail: unknown };

  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .map((entry) =>
        typeof entry === "object" && entry !== null && "msg" in entry
          ? String((entry as { msg: unknown }).msg)
          : null
      )
      .filter((msg): msg is string => msg !== null);

    if (messages.length > 0) {
      return messages.join("; ");
    }
  }

  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API_BASE_URL) {
    throw new ApiError(
      "NEXT_PUBLIC_API_URL is not configured. Add it to frontend/.env.local.",
      0
    );
  }

  // A FormData body must NOT get an explicit Content-Type: fetch has to set
  // it itself so the multipart boundary is included.
  const isFormData = init?.body instanceof FormData;
  const authHeaders = await getAuthHeaders();

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: isFormData
        ? { ...authHeaders, ...init?.headers }
        : { "Content-Type": "application/json", ...authHeaders, ...init?.headers },
    });
  } catch (cause) {
    throw new ApiError(
      `Unable to reach the SourceGuard API at ${API_BASE_URL}.`,
      0,
      { cause }
    );
  }

  // Read the body once, as text, so a non-JSON error page (proxy 502, HTML
  // 404) doesn't throw an opaque parse error on top of the real failure.
  const raw = await response.text();
  let parsed: unknown = null;
  if (raw.length > 0) {
    try {
      parsed = JSON.parse(raw);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    throw new ApiError(
      extractDetail(parsed) ?? `Request to ${path} failed (${response.status}).`,
      response.status
    );
  }

  return parsed as T;
}

/**
 * NOTE: the backend does not implement this route yet — `app/api/endpoints/
 * workspaces.py` currently registers only `POST ""`. This will reject with an
 * `ApiError(404)` until a list endpoint is added.
 */
export function getWorkspaces(): Promise<Workspace[]> {
  return request<Workspace[]>("/workspaces", { method: "GET" });
}

/** Rejects with `ApiError(409)` when `name` is already taken. */
export function createWorkspace(name: string): Promise<Workspace> {
  return request<Workspace>("/workspaces", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

/**
 * Uploads a single file to a workspace. Rejects with `ApiError(400)` for a
 * disallowed extension or a path-traversal/disguised-extension filename
 * (`DocumentParser._validate_filename`), `ApiError(404)` for an unknown
 * `workspaceId`, or `ApiError(422)` for a decode failure — each carrying the
 * backend's exact rejection detail as its message.
 */
export function uploadDocument(
  workspaceId: string,
  file: File
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("workspace_id", workspaceId);
  formData.append("files", file);

  return request<DocumentUploadResponse>("/documents/upload", {
    method: "POST",
    body: formData,
  });
}

/** Rejects with `ApiError(404)` for an unknown `workspaceId`. */
export function getWorkspaceDocuments(workspaceId: string): Promise<WorkspaceDocument[]> {
  return request<WorkspaceDocument[]>(`/workspaces/${workspaceId}/documents`, {
    method: "GET",
  });
}

export type QueryStreamEvent =
  | { type: "token"; token: string }
  | { type: "verification"; claim: ClaimVerification }
  | { type: "done"; answer: string; overall_score: number; is_fully_supported: boolean }
  | { type: "error"; detail: string };

/**
 * Converts one decoded SSE frame into a typed event. Returns `null` for a
 * malformed or unrecognized payload rather than throwing, so one bad frame
 * doesn't abort an otherwise-good stream.
 *
 * NOTE: `app/api/endpoints/query.py::_stream_query_events` never puts
 * `supporting_chunk_index` on the wire for a `verification` event, even
 * though `ClaimVerification` declares the field — it's always `null` here.
 */
function toQueryStreamEvent(eventName: string, payload: unknown): QueryStreamEvent | null {
  if (typeof payload !== "object" || payload === null) {
    return null;
  }
  const data = payload as Record<string, unknown>;

  switch (eventName) {
    case "token":
      return typeof data.token === "string" ? { type: "token", token: data.token } : null;

    case "verification":
      if (
        typeof data.claim === "string" &&
        typeof data.label === "string" &&
        typeof data.score === "number"
      ) {
        return {
          type: "verification",
          claim: {
            claim: data.claim,
            label: data.label as EntailmentLabel,
            score: data.score,
            supporting_chunk_index: null,
          },
        };
      }
      return null;

    case "done":
      if (
        typeof data.answer === "string" &&
        typeof data.overall_score === "number" &&
        typeof data.is_fully_supported === "boolean"
      ) {
        return {
          type: "done",
          answer: data.answer,
          overall_score: data.overall_score,
          is_fully_supported: data.is_fully_supported,
        };
      }
      return null;

    case "error":
      return typeof data.detail === "string" ? { type: "error", detail: data.detail } : null;

    default:
      return null;
  }
}

/**
 * Streams `POST /api/v1/query/stream` and yields typed events as they
 * arrive. The native `EventSource` API only supports `GET`, so this hand-
 * parses the `event:`/`data:` SSE frames off a `fetch` + `ReadableStream`
 * instead: buffer decoded bytes, split on `\n`, accumulate `data:` lines
 * until a blank line closes the frame, then dispatch by the last-seen
 * `event:` name.
 *
 * Rejects with `ApiError(404)` (unknown workspace) before the stream ever
 * starts, since that check happens synchronously in the route handler.
 */
export async function* streamQuery(
  workspaceId: string,
  query: string
): AsyncGenerator<QueryStreamEvent, void, void> {
  if (!API_BASE_URL) {
    throw new ApiError(
      "NEXT_PUBLIC_API_URL is not configured. Add it to frontend/.env.local.",
      0
    );
  }

  const authHeaders = await getAuthHeaders();

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/query/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders },
      body: JSON.stringify({ workspace_id: workspaceId, query }),
    });
  } catch (cause) {
    throw new ApiError(
      `Unable to reach the SourceGuard API at ${API_BASE_URL}.`,
      0,
      { cause }
    );
  }

  if (!response.ok) {
    const raw = await response.text();
    let parsed: unknown = null;
    if (raw.length > 0) {
      try {
        parsed = JSON.parse(raw);
      } catch {
        parsed = null;
      }
    }
    throw new ApiError(
      extractDetail(parsed) ?? `Request to /query/stream failed (${response.status}).`,
      response.status
    );
  }

  if (!response.body) {
    throw new ApiError("The server response had no readable body.", 0);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "message";
  let dataLines: string[] = [];

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });

    let newlineIndex: number;
    while ((newlineIndex = buffer.indexOf("\n")) !== -1) {
      let line = buffer.slice(0, newlineIndex);
      buffer = buffer.slice(newlineIndex + 1);
      if (line.endsWith("\r")) {
        line = line.slice(0, -1);
      }

      if (line === "") {
        if (dataLines.length > 0) {
          let payload: unknown = null;
          try {
            payload = JSON.parse(dataLines.join("\n"));
          } catch {
            payload = null;
          }
          const event = toQueryStreamEvent(eventName, payload);
          if (event) {
            yield event;
          }
        }
        eventName = "message";
        dataLines = [];
        continue;
      }

      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
      // Other SSE fields (id:, retry:, ":"-prefixed keep-alive comments) are unused here.
    }
  }
}
