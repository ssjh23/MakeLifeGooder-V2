/**
 * PLACEHOLDER. Overwritten by `npm run generate:client`.
 *
 * The real file is generated from the backend's OpenAPI document, which is
 * itself generated from the Pydantic schemas. That chain is the seam ADR-001
 * bought when it put Python on the backend and TypeScript here: a field renamed
 * in a Pydantic model becomes a TypeScript compile error rather than a runtime
 * surprise in production.
 *
 * Never hand-edit the generated file. If a shape here is wrong, the backend
 * schema is wrong.
 *
 * This stub exists only so the project typechecks before the backend has ever
 * been started. It declares the handful of operations screen 03 uses.
 */

export interface paths {
  "/api/v1/me": {
    get: {
      responses: {
        200: {
          content: {
            "application/json": {
              id: string;
              email: string;
              name: string | null;
              has_statements: boolean;
            };
          };
        };
      };
    };
  };
  "/api/v1/statements/upload-url": {
    post: {
      requestBody: {
        content: {
          "application/json": { content_type: string; size_bytes: number };
        };
      };
      responses: {
        200: {
          content: {
            "application/json": {
              upload_id: string;
              url: string;
              expires_at: string;
              required_headers: Record<string, string>;
            };
          };
        };
      };
    };
  };
  "/api/v1/statements": {
    post: {
      requestBody: {
        content: {
          "application/json": { upload_id: string; card_id?: string | null };
        };
      };
      responses: {
        202: { content: { "application/json": StatementResponse } };
      };
    };
  };
  "/api/v1/statements/{statement_id}": {
    get: {
      parameters: { path: { statement_id: string } };
      responses: {
        200: { content: { "application/json": StatementResponse } };
      };
    };
  };
}

export interface StatementResponse {
  id: string;
  status: "pending" | "processing" | "needs_review" | "ready" | "failed";
  card_id: string | null;
  trace_id: string | null;
  request_id: string | null;
  extraction: {
    has_text_layer: boolean;
    card_identified: string | null;
    printed_total: string | null;
    rows_extracted: number;
    parser: string | null;
  } | null;
  failure: { reason: string; message: string } | null;
  uploaded_at: string;
}
