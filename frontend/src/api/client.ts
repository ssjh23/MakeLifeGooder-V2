/**
 * The API client.
 *
 * Typed from the generated schema, so a backend field rename is a compile
 * error here rather than an undefined at runtime.
 *
 * Everything goes through this except uploads, which are deliberately direct to
 * object storage. See `import/upload.ts` for why that exception exists.
 */

import createClient from "openapi-fetch";
import type { paths } from "./generated/schema";

export const api = createClient<paths>({
  baseUrl: "/",
  // Sessions are cookie-based, so credentials must be sent. Without this every
  // request is anonymous and every screen redirects to sign-in.
  credentials: "same-origin",
});

/**
 * An API failure carrying its correlation id.
 *
 * The backend returns `X-Request-ID` on every response and repeats it in the
 * error body. Keeping it on the error object is what lets the boundary show it
 * to the user, so a bug report arrives with the trace pointer already in it and
 * nobody has to ask "roughly when did this happen".
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: Record<string, unknown>;

  constructor(init: {
    status: number;
    code: string;
    message: string;
    requestId: string | null;
    details?: Record<string, unknown>;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.requestId = init.requestId;
    this.details = init.details ?? {};
  }

  /** A 409 the user has to settle: a duplicate statement, or a rule collision. */
  get isConflict(): boolean {
    return this.status === 409;
  }

  /** A 422: the request was valid but the state does not allow it. */
  get isInvalidState(): boolean {
    return this.status === 422;
  }

  /** A 501: the endpoint exists but its logic is not written yet. */
  get isNotWritten(): boolean {
    return this.status === 501;
  }
}

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
    request_id?: string | null;
    details?: Record<string, unknown>;
  };
}

/** Turn a failed response into an {@link ApiError}. */
export async function toApiError(response: Response): Promise<ApiError> {
  let envelope: ErrorEnvelope = {};
  try {
    envelope = (await response.json()) as ErrorEnvelope;
  } catch {
    // A non-JSON error body is still an error; it just has less to say.
  }
  return new ApiError({
    status: response.status,
    code: envelope.error?.code ?? "unknown",
    message: envelope.error?.message ?? response.statusText,
    requestId: envelope.error?.request_id ?? response.headers.get("X-Request-ID"),
    details: envelope.error?.details,
  });
}
