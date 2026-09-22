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

// openapi-fetch reads the body itself on a non-ok response (to populate the
// `error` half of its `{ data, error, response }` return) before a caller
// ever sees `response` -- a Response's body can only be read once, so every
// `toApiError(response)` call site below was re-reading an already-drained
// stream. That failed silently (caught, then ignored) and fell back to
// `response.statusText`, which for a 409 is literally the word "Conflict" --
// exactly the unhelpful message this was supposed to prevent.
//
// Cloning here, in an onResponse hook, runs before openapi-fetch's own read
// (see its source: middleware fires, then it calls response.text()), so this
// keeps one never-read copy per response for toApiError to parse instead of
// the original. No call site needs to change: they still just pass `response`.
const responseClones = new WeakMap<Response, Response>();
api.use({
  onResponse({ response }) {
    responseClones.set(response, response.clone());
    return undefined;
  },
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

/**
 * The two 409 shapes the server actually raises.
 *
 * `error.code` tells them apart: `statement_already_imported` carries
 * `existing_statement_id`, `rule_conflict` carries `existing_id` plus
 * `affected_rows` and a human `message`. They are not one shared schema
 * despite both being "a conflict a person must resolve" — read `details` by
 * `code`, not by guessing which fields are present.
 */
export interface DuplicateStatementConflict {
  existing_statement_id: string;
  resolutions: string[];
}

export interface RuleCollisionConflict {
  kind: "rule_collision";
  existing_id: string;
  resolutions: string[];
  affected_rows: number;
  message: string;
}

export function asDuplicateStatementConflict(
  error: ApiError,
): DuplicateStatementConflict | null {
  if (error.code !== "statement_already_imported") return null;
  return error.details as unknown as DuplicateStatementConflict;
}

/**
 * The exact same file was already uploaded (screen 03). Same shape as
 * {@link DuplicateStatementConflict} -- `existing_statement_id` plus
 * `resolutions` -- but a different `code`, and a different cause: this one
 * fires at registration, before the new upload's statement even exists,
 * whereas `statement_already_imported` fires at commit, on two statements
 * that both already exist for the same card and period.
 */
export function asDuplicateFileConflict(error: ApiError): DuplicateStatementConflict | null {
  if (error.code !== "duplicate_file_upload") return null;
  return error.details as unknown as DuplicateStatementConflict;
}

export function asRuleCollisionConflict(error: ApiError): RuleCollisionConflict | null {
  if (error.code !== "rule_conflict") return null;
  return error.details as unknown as RuleCollisionConflict;
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
    // The clone, not `response` itself: see the onResponse hook above.
    envelope = (await (responseClones.get(response) ?? response).json()) as ErrorEnvelope;
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
