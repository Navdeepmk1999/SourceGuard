import type { Workspace } from "@/types";

// Already includes the `/api/v1` prefix (see frontend/.env.local), so route
// paths below are appended bare: `/workspaces` -> `<base>/api/v1/workspaces`.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL;

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

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
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
